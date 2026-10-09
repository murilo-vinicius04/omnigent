"""Read-only commands the companion may run to look things up.

The companion answers questions about the machine -- how full the disk is,
whether a run is still going, what the last commit was -- without waking
Claude. It may *look*, never *change*: code and everything else that edits
the machine stays Claude's job.

Two independent guards hold that line:

1. **An allowlist.** A command is parsed, never handed to a shell as typed.
   Only programs that read are accepted, each with its writing or
   program-launching options refused (``find -delete``, ``git -c``,
   ``rg --pre``, ``tail -f`` ...). Pipes between allowed programs are fine;
   redirections, ``;``, ``&&``, substitutions and globs-into-shell are not.
2. **A read-only sandbox.** What passes runs under ``bwrap`` with the whole
   filesystem mounted read-only, no network, a private ``/run`` (no host
   sockets), and credential files hidden. A gap in the allowlist still cannot write.

Output is capped and scrubbed of anything shaped like a credential before
the model sees it, because what the companion reads is sent to Gemini.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import os
import pathlib
import re
import shlex
import shutil
import signal
import time
from dataclasses import dataclass
from typing import Final

#: Longest a command may run. Long enough for a ``du`` over a big tree.
COMMAND_TIMEOUT_S: Final[float] = 20.0

#: Most output handed back to the model, head and tail kept.
MAX_OUTPUT_CHARS: Final[int] = 6000

#: Text filters: they only transform what is piped into them.
_FILTERS: Final[frozenset[str]] = frozenset(
    {
        "grep", "egrep", "fgrep", "head", "tail", "wc", "sort", "uniq", "cut", "tr",
        "nl", "column", "fold", "comm", "jq",
    }
)  # fmt: skip

#: Programs that read and report, beyond the filters.
_READERS: Final[frozenset[str]] = frozenset(
    {
        "ls", "cat", "du", "df", "stat", "file", "date", "uptime", "free", "ps", "pgrep",
        "pidof", "nproc", "uname", "whoami", "id", "hostname", "basename", "dirname",
        "realpath", "readlink", "which", "tree", "diff", "cmp", "md5sum", "sha256sum",
        "lsblk", "findmnt", "rg", "echo", "printf", "pwd", "find", "sed", "git",
        "journalctl", "systemctl", "docker", "nvidia-smi",
    }
)  # fmt: skip

ALLOWED_PROGRAMS: Final[frozenset[str]] = _FILTERS | _READERS

#: Programs that need to see the host's processes. Every other command runs
#: in its own process namespace, where it cannot signal anything outside.
_HOST_PIDS: Final[frozenset[str]] = frozenset({"ps", "pgrep", "pidof"})

_FIND_REFUSED: Final[frozenset[str]] = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

#: Subcommands of git that only read. Anything else is refused.
_GIT_READ: Final[frozenset[str]] = frozenset(
    {
        "status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame",
        "describe", "shortlog", "remote", "tag", "stash", "worktree", "reflog", "config",
        "grep", "cat-file", "ls-tree", "count-objects", "rev-list", "name-rev",
        "merge-base",
    }
)  # fmt: skip

_GIT_BRANCH_REFUSED: Final[frozenset[str]] = frozenset(
    {
        "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-u",
        "--set-upstream-to", "--unset-upstream", "--edit-description", "-f", "--force",
        "--track", "--no-track",
    }
)  # fmt: skip

_SYSTEMCTL_READ: Final[frozenset[str]] = frozenset(
    {"status", "show", "cat", "is-active", "is-enabled", "is-failed", "list-units",
     "list-timers", "list-unit-files", "list-sockets"}
)  # fmt: skip

_DOCKER_READ: Final[frozenset[tuple[str, ...]]] = frozenset(
    {("ps",), ("images",), ("system", "df"), ("stats",), ("version",), ("container", "ls"),
     ("image", "ls")}
)  # fmt: skip

#: Shell syntax refused outside quotes. Quoted, it is only text: every word is
#: re-quoted before it reaches the shell, so nothing inside quotes expands.
_SHELL_META: Final[frozenset[str]] = frozenset("`$;&<>(){}\\\n")

#: Most paths one glob may expand to.
_MAX_GLOB: Final[int] = 500


class CommandRefused(ValueError):
    """The command is not one the companion may run."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What running one command produced.

    :param command: The command as accepted.
    :param exit_code: Its exit status; ``None`` when it timed out.
    :param output: stdout and stderr, capped and scrubbed.
    :param seconds: Wall time.
    """

    command: str
    exit_code: int | None
    output: str
    seconds: float

    def for_model(self) -> str:
        """Render the result the way the companion is shown it."""
        status = "timed out" if self.exit_code is None else f"exit {self.exit_code}"
        body = self.output if self.output.strip() else "(no output)"
        return f"$ {self.command}\n[{status}, {self.seconds:.1f}s]\n{body}"


@dataclass(slots=True)
class _Word:
    text: str = ""
    quoted: bool = False


def _tokenize(command: str) -> list[list[_Word]]:
    """Split *command* into pipeline stages of words, the way ``sh`` would.

    Quotes group text and keep it literal. Outside quotes only whitespace
    and ``|`` are syntax; any other shell metacharacter is refused.

    :raises CommandRefused: On unbalanced quotes or refused syntax.
    """
    stages: list[list[_Word]] = [[]]
    word: _Word | None = None
    quote = ""
    for ch in command.strip():
        if quote:
            if ch == quote:
                quote = ""
            else:
                assert word is not None
                word.text += ch
            continue
        if ch in "'\"":
            quote = ch
            word = word or _Word()
            word.quoted = True
            continue
        if ch.isspace() or ch == "|":
            if word is not None:
                stages[-1].append(word)
                word = None
            if ch == "|":
                if not stages[-1]:
                    raise CommandRefused("only plain commands joined by | are allowed")
                stages.append([])
            continue
        if ch in _SHELL_META:
            raise CommandRefused(
                "only plain commands joined by | are allowed (no redirections, ;, &&, "
                "$ or backticks)"
            )
        word = word or _Word()
        word.text += ch
    if quote:
        raise CommandRefused("unbalanced quotes")
    if word is not None:
        stages[-1].append(word)
    if not stages[-1]:
        raise CommandRefused("empty command" if len(stages) == 1 else "empty pipeline stage")
    return stages


def _expand(word: _Word, *, cwd: pathlib.Path, home: pathlib.Path) -> list[str]:
    """Expand ``~`` and globs in an unquoted word, as the shell would."""
    text = word.text
    if word.quoted:
        return [text]
    if text == "~" or text.startswith("~/"):
        text = str(home) + text[1:]
    if any(c in text for c in "*?["):
        absolute = os.path.isabs(text)
        matches = sorted(glob.glob(text if absolute else str(cwd / text)))
        if matches:
            if not absolute:
                matches = [os.path.relpath(m, cwd) for m in matches]
            return matches[:_MAX_GLOB]
    return [text]


def _check_stage(argv: list[str]) -> None:
    """Refuse one pipeline stage unless it only reads.

    :raises CommandRefused: With the reason, phrased for the model.
    """
    program = argv[0]
    if "/" in program:
        raise CommandRefused("name the program, not a path to it")
    if program not in ALLOWED_PROGRAMS:
        raise CommandRefused(f"{program} is not a read-only command you may run")
    args = argv[1:]
    if program == "find":
        bad = _FIND_REFUSED.intersection(args)
        if bad:
            raise CommandRefused(f"find {sorted(bad)[0]} is not allowed")
    elif program == "rg":
        if any(a.startswith("--pre") for a in args):
            raise CommandRefused("rg --pre is not allowed")
    elif program == "tail":
        if any(a in ("-f", "-F", "--follow", "--retry") or a.startswith("--follow") for a in args):
            raise CommandRefused("tail cannot follow; read a fixed number of lines")
    elif program == "sort":
        if any(a == "-o" or a.startswith(("--output", "--compress-program")) for a in args):
            raise CommandRefused("sort may not write files or run programs")
    elif program == "sed":
        _check_sed(args)
    elif program == "git":
        _check_git(args)
    elif program == "systemctl":
        verbs = [a for a in args if not a.startswith("-")]
        if not verbs or verbs[0] not in _SYSTEMCTL_READ:
            raise CommandRefused("systemctl may only show status")
    elif program == "journalctl":
        if any(a.startswith(("--vacuum", "--rotate", "--flush", "--sync", "--relinquish",
                             "--smart-relinquish", "--setup-keys", "--update-catalog"))
               for a in args):  # fmt: skip
            raise CommandRefused("journalctl may only read")
        if any(a in ("-f", "--follow") for a in args):
            raise CommandRefused("journalctl cannot follow; use -n")
    elif program == "docker":
        words = tuple(a for a in args if not a.startswith("-"))
        if not any(words[: len(ok)] == ok for ok in _DOCKER_READ):
            raise CommandRefused("docker may only list containers, images and disk use")
        if words[:1] == ("stats",) and "--no-stream" not in args:
            raise CommandRefused("docker stats needs --no-stream")
    elif program == "nvidia-smi":
        if any(not (a in ("-L", "-q") or a.startswith(("--query", "--format"))) for a in args):
            raise CommandRefused("nvidia-smi may only query")


def _check_sed(args: list[str]) -> None:
    """Allow only ``sed -n 'A,Bp' file...``: printing a line range."""
    if not args or args[0] != "-n" or len(args) < 2:
        raise CommandRefused("sed is only allowed as sed -n 'A,Bp' file")
    if not re.fullmatch(r"\d+(,(\d+|\$))?p|\$p", args[1]):
        raise CommandRefused("sed is only allowed as sed -n 'A,Bp' file")
    if any(a.startswith("-") for a in args[2:]):
        raise CommandRefused("sed is only allowed as sed -n 'A,Bp' file")


def _check_git(args: list[str]) -> None:
    """Allow git's reading subcommands, with no config or program overrides."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        flag = args[i]
        if flag == "-C" and i + 1 < len(args):
            i += 2
            continue
        if flag in ("--no-pager", "-P"):
            i += 1
            continue
        raise CommandRefused(f"git {flag} is not allowed")
    if i >= len(args):
        raise CommandRefused("name a git subcommand")
    sub, rest = args[i], args[i + 1 :]
    if sub not in _GIT_READ:
        raise CommandRefused(f"git {sub} is not a read-only subcommand")
    positional = [a for a in rest if not a.startswith("-")]
    if sub == "branch":
        if _GIT_BRANCH_REFUSED.intersection(rest):
            raise CommandRefused("git branch may only list")
        listing = ("--contains", "--merged", "--no-merged", "--points-at", "--list", "-l")
        if positional and not any(a in rest for a in listing):
            raise CommandRefused("git branch may only list")
    elif sub == "tag":
        if positional and not any(
            a in rest for a in ("-l", "--list", "--contains", "--points-at")
        ):
            raise CommandRefused("git tag may only list")
        if any(a in rest for a in ("-d", "--delete", "-a", "-s", "-f", "-m")):
            raise CommandRefused("git tag may only list")
    elif sub == "stash":
        if not positional or positional[0] not in ("list", "show"):
            raise CommandRefused("git stash may only list or show")
    elif sub == "worktree":
        if positional[:1] != ["list"]:
            raise CommandRefused("git worktree may only list")
    elif sub == "reflog":
        if positional and positional[0] not in ("show",):
            raise CommandRefused("git reflog may only show")
    elif sub == "remote":
        if positional and positional[0] not in ("show", "get-url"):
            raise CommandRefused("git remote may only list or show")
    elif sub == "config":
        if not any(a in rest for a in ("--get", "--get-all", "--get-regexp", "--list", "-l")):
            raise CommandRefused("git config may only read")


def validate(
    command: str, *, cwd: pathlib.Path | None = None, home: pathlib.Path | None = None
) -> list[list[str]]:
    """Parse *command* and refuse it unless every stage only reads.

    Globs and ``~`` are expanded first, so the checks see the arguments the
    program will really get -- a file named ``-delete`` cannot sneak into
    ``find`` through a ``*``.

    :param command: The command as the companion wrote it.
    :param cwd: Directory globs are expanded in.
    :param home: What ``~`` means.
    :returns: The argv of each pipeline stage, expanded.
    :raises CommandRefused: With a reason the model can act on.
    """
    home = home or pathlib.Path.home()
    cwd = cwd or home
    stages: list[list[str]] = []
    for words in _tokenize(command):
        argv = [words[0].text]
        for word in words[1:]:
            argv += _expand(word, cwd=cwd, home=home)
        _check_stage(argv)
        stages.append(argv)
    programs = {argv[0] for argv in stages}
    if programs & _HOST_PIDS and not programs <= (_HOST_PIDS | _FILTERS):
        raise CommandRefused("pipe ps/pgrep only into text filters such as grep or head")
    return stages


# -- the sandbox -------------------------------------------------------------

#: Directories whose contents are credentials, hidden behind an empty tmpfs.
_SECRET_DIRS: Final[tuple[str, ...]] = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store",
    ".config/gh", ".config/gcloud", ".config/op", ".gemini", ".antigravity",
    ".config/Antigravity", ".hermes", ".mozilla", ".config/google-chrome",
    ".config/chromium", ".omnigent/codex-home", ".omnigent/codex-native",
    ".omnigent/opencode-native", ".omnigent/pi-native", ".omnigent/antigravity-native",
)  # fmt: skip

#: Single credential files, hidden behind an unreadable bind.
_SECRET_FILES: Final[tuple[str, ...]] = (
    ".claude/.credentials.json", ".codex/auth.json", ".netrc", ".git-credentials",
    ".npmrc", ".pypirc", ".omnigent/config.yaml",
)  # fmt: skip

#: Names in ~/.omnigent that hold keys or tokens.
_SECRET_NAME: Final[re.Pattern[str]] = re.compile(
    r"key|token|secret|credential|passw|\.env$|\.pem$|auth", re.IGNORECASE
)


def _hidden(home: pathlib.Path) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """Return the credential directories and files that exist under *home*."""
    dirs = [home / d for d in _SECRET_DIRS if (home / d).is_dir()]
    files = [home / f for f in _SECRET_FILES if (home / f).is_file()]
    omni = home / ".omnigent"
    if omni.is_dir():
        for entry in omni.iterdir():
            if _SECRET_NAME.search(entry.name) and entry.is_file():
                files.append(entry)
    return dirs, files


def sandbox_argv(
    stages: list[list[str]], *, cwd: pathlib.Path, home: pathlib.Path | None = None
) -> list[str]:
    """Build the ``bwrap`` command that runs *stages* read-only.

    :param stages: Validated pipeline stages, from :func:`validate`.
    :param cwd: Directory the command runs in.
    :param home: Home directory whose credentials are hidden.
    :returns: argv for :func:`asyncio.create_subprocess_exec`.
    """
    home = home or pathlib.Path.home()
    programs = {argv[0] for argv in stages}
    argv = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        # /tmp stays visible, read-only like the rest; scratch space for
        # tools such as sort is a private tmpfs on /run, which also hides
        # the host's sockets.
        "--tmpfs", "/run",
        "--setenv", "TMPDIR", "/run",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", str(home),
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "TERM", "dumb",
        "--setenv", "PAGER", "cat",
        "--setenv", "GIT_PAGER", "cat",
        "--setenv", "SYSTEMD_PAGER", "cat",
        "--setenv", "GIT_OPTIONAL_LOCKS", "0",
        "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
        "--setenv", "GIT_TERMINAL_PROMPT", "0",
    ]  # fmt: skip
    if not programs & _HOST_PIDS:
        argv.append("--unshare-pid")
    uid = os.getuid()
    if "systemctl" in programs:
        # Only status verbs pass validation, so the buses are safe to reach.
        bus = pathlib.Path(f"/run/user/{uid}/bus")
        if bus.exists():
            argv += ["--dir", f"/run/user/{uid}", "--ro-bind", str(bus), str(bus)]
            argv += ["--setenv", "XDG_RUNTIME_DIR", f"/run/user/{uid}"]
            argv += ["--setenv", "DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}"]
        system_bus = pathlib.Path("/run/dbus/system_bus_socket")
        if system_bus.exists():
            argv += ["--dir", "/run/dbus", "--ro-bind", str(system_bus), str(system_bus)]
        # systemctl refuses to work unless this directory says systemd is running.
        if pathlib.Path("/run/systemd/system").is_dir():
            argv += ["--dir", "/run/systemd/system"]
    if "docker" in programs:
        sock = pathlib.Path("/var/run/docker.sock").resolve()
        if sock.exists():
            argv += ["--ro-bind", str(sock), "/run/docker.sock"]
    if "nvidia-smi" in programs:
        # Queries only (see validation); the driver needs its device nodes.
        for dev in sorted(glob.glob("/dev/nvidia*")):
            if not os.path.isdir(dev):
                argv += ["--dev-bind", dev, dev]
    dirs, files = _hidden(home)
    for d in dirs:
        argv += ["--tmpfs", str(d)]
    for f in files:
        argv += ["--ro-bind", "/dev/null", str(f)]
    argv += ["--chdir", str(cwd)]
    script = " | ".join(shlex.join(_harden(stage)) for stage in stages)
    argv += ["--", "/bin/sh", "-c", script]
    return argv


def _harden(argv: list[str]) -> list[str]:
    """Add the options that keep a reading command from launching anything."""
    if argv[0] == "git":
        hardened = [
            "git", "--no-pager",
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.pager=cat",
        ]  # fmt: skip
        rest = argv[1:]
        # The reader's own -C stays in front of the subcommand.
        while rest and rest[0] in ("-C", "--no-pager", "-P"):
            take = 2 if rest[0] == "-C" else 1
            hardened += [a for a in rest[:take] if a not in ("--no-pager", "-P")]
            rest = rest[take:]
        if rest and rest[0] in ("diff", "log", "show"):
            return [*hardened, rest[0], "--no-ext-diff", "--no-textconv", *rest[1:]]
        return [*hardened, *rest]
    if argv[0] in ("journalctl", "systemctl") and "--no-pager" not in argv:
        return [argv[0], "--no-pager", *argv[1:]]
    return argv


# -- output -------------------------------------------------------------------

_REDACTIONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[private key redacted]"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}"), "[key redacted]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
     "[token redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[key redacted]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[token redacted]"),
    (re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}"), "[token redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
     "[token redacted]"),
    (re.compile(r"(?i)\b([\w.-]*(?:api[_-]?key|token|secret|passw(?:or)?d|credential)[\w.-]*"
                r"[\"']?\s*[:=]\s*)[\"']?[^\s\"',;]{6,}"),
     r"\1[redacted]"),
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)(\s--?[\w-]*(?:key|token|secret|passw(?:or)?d)[\w-]*[\s=]+)(?!\[)\S+"),
     r"\1[redacted]"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),
)  # fmt: skip


def scrub(text: str) -> str:
    """Replace anything shaped like a credential with a placeholder."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _cap(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep the head and tail of long output, marking the cut."""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.75)]
    tail = text[-int(limit * 0.2) :]
    return f"{head}\n[... {len(text) - len(head) - len(tail)} characters cut ...]\n{tail}"


async def run(
    command: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout_s: float = COMMAND_TIMEOUT_S,
    home: pathlib.Path | None = None,
) -> CommandResult:
    """Validate and run one read-only command.

    :param command: What the companion asked to run.
    :param cwd: Where to run it; the home directory when unset or missing.
    :param timeout_s: Wall-clock budget; the whole process group is killed.
    :param home: Home directory (tests point it elsewhere).
    :returns: The result, output scrubbed.
    :raises CommandRefused: When the command is not read-only, or the
        sandbox is unavailable on this machine.
    """
    home = home or pathlib.Path.home()
    where = pathlib.Path(cwd) if cwd else home
    if not where.is_dir():
        where = home
    stages = validate(command, cwd=where, home=home)
    if shutil.which("bwrap") is None:
        raise CommandRefused("the read-only sandbox (bwrap) is not installed")
    argv = sandbox_argv(stages, cwd=where, home=home)
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout_s)
        code: int | None = process.returncode
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await process.wait()
        out, code = b"", None
    text = _cap(scrub(out.decode("utf-8", errors="replace")))
    return CommandResult(
        command=" | ".join(shlex.join(s) for s in stages),
        exit_code=code,
        output=text,
        seconds=time.monotonic() - started,
    )


def describe_allowed() -> str:
    """One paragraph telling the model what it may run."""
    return (
        "Programs you may run (read-only, nothing else): "
        + ", ".join(sorted(ALLOWED_PROGRAMS - {"echo", "printf", "pwd"}))
        + ". Join them with | if needed; no redirections, ;, &&, $(...) or "
        "backticks. git may only read (status, log, diff, show, branch, ...). "
        "systemctl and journalctl only show status and logs (this machine's own "
        "services and benchmark runs are user units: use systemctl --user and "
        "journalctl --user); docker only ps, "
        "images, system df and stats --no-stream; sed only as sed -n 'A,Bp' file; "
        "tail and journalctl cannot follow. Nothing can write files, change code, "
        "start, stop or kill anything, or reach the network."
    )
