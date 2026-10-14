"""The hub as a regular conversation: one per user, pinned, opened like any chat.

The hub has no harness and no runner. Messages typed into its conversation
are taken by the events route before any dispatch and answered by the hub
(:mod:`omnigent.server.hub`); what the hub says is written back as ordinary
assistant messages and published on the session stream, so the normal chat
page renders it, the normal composer sends to it, and the chat's voice
button talks to the hub.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import pathlib
import re
import tarfile
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace
from typing import Any, Final
from urllib.parse import quote

from omnigent.server import hub as hub_module
from omnigent.server import hub_place

_logger = logging.getLogger(__name__)

#: Label marking a conversation as a user's hub.
HUB_LABEL: Final[str] = "omnigent.hub"

#: Title of the hub conversation in the sidebar.
HUB_TITLE: Final[str] = "Hub"

#: Name of the hub's session-scoped agent; the chat header shows it.
HUB_AGENT_NAME: Final[str] = "hub"

#: Request headers a send carries on, so it acts as the caller.
FORWARDED_HEADERS: Final[tuple[str, ...]] = (
    "cookie",
    "authorization",
    "x-forwarded-email",
    "origin",
)

#: Where a reply names a file: a markdown link or image, or an inline code span.
_FILE_REF: Final[re.Pattern[str]] = re.compile(r"(!?)\[([^\]\n]*)\]\(<?([^)\s>]+)>?\)|`([^`\n]+)`")

#: A file path written as plain text, not in a link or code: absolute, under
#: home, or relative with a folder, ending in a file name with an extension.
_BARE_PATH: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w/.~`\[(<:@-])(~?/?(?:[\w.@+-]+/)+[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/])"
)

#: What a line's bare paths are looked for outside of: links, code spans, URLs.
_NOT_BARE: Final[re.Pattern[str]] = re.compile(
    r"!?\[[^\]\n]*\]\([^)\n]*\)|`[^`\n]+`|<[^>\n]+>|[a-zA-Z][\w+.-]*://\S+"
)

#: A line that opens or closes a code fence.
_FENCE: Final[re.Pattern[str]] = re.compile(r"^\s*(```|~~~)", re.MULTILINE)

#: In the hub's own words, a file name or path a project's reply named.
_NAMED_FILE: Final[re.Pattern[str]] = re.compile(
    r"\[([^\]\n]*)\]\(([^)\n]*)\)"  # a link already: only a host path is relinked
    r"|`([^`\n]+)`"  # inline code
    r"|(?<![\w/.~-])(~?/?[\w.-]+(?:/[\w.-]+)*\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/-])"  # a bare name
)

#: How many of the latest reports the hub looks in for the files projects named.
_KNOWN_FILE_REPORTS: Final[int] = 40

#: A web link in a reply: a markdown link, or a bare address (also in a code span).
_WEB_LINK: Final[re.Pattern[str]] = re.compile(
    r"(!?)\[([^\]\n]*)\]\((https?://[^)\s]+)\)"
    r"|(?<![\w/])(https?://[^\s<>()\[\]`*\"']+[^\s<>()\[\]`*\"'.,;:!?])"
)

#: At most this many of a reply's web links ride along on its report.
_MAX_WEB_LINKS: Final[int] = 8

#: A cited position after a path, e.g. ``src/app.ts:42:7``.
_POSITION_SUFFIX: Final[re.Pattern[str]] = re.compile(r":\d+(?::\d+)?$")

_HUB_SPEC: Final[str] = """\
spec_version: 1
name: hub
description: One chat for all of your projects.
executor:
  type: omnigent
  config:
    # Never launched: the hub's messages never reach a runner. Named so the
    # chat shows the model that really answers (the router, on Gemini).
    harness: agy
    model: {model}
"""


def is_hub(conv: Any) -> bool:
    """Whether a conversation is a user's hub."""
    labels = getattr(conv, "labels", None) or {}
    return labels.get(HUB_LABEL) == "1"


def _hub_bundle() -> bytes:
    """A gzipped tarball holding the hub's one-file spec."""
    data = _HUB_SPEC.format(model=hub_module.hub_model()).encode("utf-8")
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo("config.yaml")
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _id_path(user_id: str | None):
    return hub_module.HUB_DIR / hub_module.user_key(user_id) / "conversation.json"


def known_hub_id(user_id: str | None) -> str | None:
    """The user's hub conversation id, if one was made."""
    try:
        raw = json.loads(_id_path(user_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


def ensure_hub_conversation(
    user_id: str | None,
    *,
    conversation_store: Any,
    artifact_store: Any,
    permission_store: Any = None,
) -> str:
    """Return the user's hub conversation id, creating it when missing.

    Blocking (store calls); run it in a thread from async code. A hub the
    user archived or deleted is replaced by a fresh one.
    """
    from omnigent.db.utils import generate_agent_id
    from omnigent.stores.conversation_store import pinned_label_key

    existing = known_hub_id(user_id)
    if existing is not None:
        conv = conversation_store.get_conversation(existing)
        if conv is not None and not getattr(conv, "archived", False):
            return existing
    bundle = _hub_bundle()
    agent_id = generate_agent_id()
    location = f"{agent_id}/{hashlib.sha256(bundle).hexdigest()}"
    artifact_store.put(location, bundle)
    created = conversation_store.create_session_with_agent(
        agent_id=agent_id,
        agent_name=HUB_AGENT_NAME,
        agent_bundle_location=location,
        agent_description="One chat for all of your projects.",
        title=HUB_TITLE,
        labels={HUB_LABEL: "1", pinned_label_key(user_id): str(int(time.time() * 1000))},
    )
    session_id = created.conversation.id
    if permission_store is not None and user_id:
        from omnigent.server.auth import LEVEL_OWNER

        permission_store.grant(user_id, session_id, LEVEL_OWNER)
    path = _id_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": session_id}), encoding="utf-8")
    _logger.info("hub conversation created | user=%s session=%s", user_id, session_id)
    return session_id


#: The link title that marks a project's name as a chip (the page draws a pill).
PROJECT_CHIP_TITLE: Final[str] = "omnigent-project"

#: The chip on a message that went to a project: it shows the send, in no words.
PROJECT_SENT_CHIP_TITLE: Final[str] = "omnigent-project-sent"

#: The link title that marks where a message for a busy lane went: the page
#: draws it as a small control for correcting that.
PLACE_TITLE: Final[str] = "omnigent-hub-place"

#: Entries about one project: they open with its chip.
_PROJECT_KINDS: Final[frozenset[str]] = frozenset(
    {"sent", "held", "lane", "finished", "attention", "status", "check", "model", "project"}
)


def own_words(entry: hub_module.HubEntry) -> str:
    """An entry's words without the ``"<project> finished:"`` label its chip replaces."""
    if entry.kind == "finished" and entry.session_name:
        return hub_module._without_label(entry.text, f"{entry.session_name} finished:")
    return entry.text


def _chip(entry: hub_module.HubEntry, base_url: str) -> str:
    """The project an entry is about, as the chip the page draws at its top."""
    if entry.kind not in _PROJECT_KINDS or not entry.session_id or not entry.session_name:
        return ""
    name = entry.session_name.replace("[", "\\[").replace("]", "\\]")
    if not base_url:
        return f"**{name}**"
    title = PROJECT_SENT_CHIP_TITLE if entry.kind == "sent" else PROJECT_CHIP_TITLE
    chip = f'[{name}]({base_url}/c/{entry.session_id} "{title}")'
    place = entry.place or {}
    picked = str(place.get("picked") or "")
    if place.get("id") and picked in hub_place.PLACE_LABELS:
        # Where it went, beside the chip; the page lets them say it was wrong.
        href = f"{base_url}/hub/placements/{quote(str(place['id']), safe='')}?picked={picked}"
        if place.get("guess") in hub_place.PLACES:
            href += f"&guess={place['guess']}"
        chip += f' [{hub_place.PLACE_LABELS[picked]}]({href} "{PLACE_TITLE}")'
    return chip


def render(
    entry: hub_module.HubEntry,
    base_url: str = "",
    files: dict[str, tuple[str, str]] | None = None,
) -> str:
    """One hub entry as the markdown its chat message carries.

    :param base_url: The app's address as the reader opens it, e.g.
        ``"http://localhost:6767"``. The chat links only absolute URLs (a bare
        ``/c/...`` reads as a file path), so without it there is no link.
    :param files: Files projects' replies named, by name or path: ``(chat,
        workspace path)``. Wherever the hub mentions one, it links to it.
    """
    entry = replace(entry, text=own_words(entry))
    if files and base_url:
        entry = replace(entry, text=link_known_files(entry.text, files, base_url))
    # The project is a chip on top, which opens its chat; a project with no
    # chat yet gets a link to start one.
    chip = _chip(entry, base_url)
    link = ""
    if base_url and entry.link and not entry.session_id:
        link = f"[Start a chat in {entry.session_name} ↗]({base_url}{entry.link})"
    if entry.kind in ("sent", "held") and entry.detail:
        # The sent chip says it went; the quote is what went. Without a link
        # there is no chip to say it, so the words stay. A held message keeps
        # its words: they say it waits for the lane to finish.
        said = "" if entry.kind == "sent" and chip.startswith("[") else entry.text
        parts = [chip, said, f"> {_quote(entry.detail)}", link]
        return "\n\n".join(part for part in parts if part)
    if entry.kind == "finished" and (entry.detail or entry.show):
        # What the project's own chat shows under its summary of the reply: the
        # tables, images, links and output its summary picked, and the files the
        # reply names. Never the reply itself: that is one click away.
        chat = f"{base_url}/c/{entry.session_id}" if base_url and entry.session_id else ""
        # Without the app's address there is no link to make, so no folder to look in.
        workspace = entry.workspace if chat else None

        def url_for(rel: str) -> str:
            return f"{chat}?file={quote(rel, safe='/')}"

        anchors = entry.anchors or ()
        shown = (
            show_markdown(entry.show, workspace, url_for, anchors)
            if entry.show
            else "\n\n".join(reply_tables(entry.detail or ""))
        )
        handed = entry_files(entry, workspace) if workspace else []
        shown = link_workspace_files(
            shown, workspace, url_for, anchors, only={rel for _, rel in handed}
        )
        file_links = " · ".join(
            f"[`{label}`]({url_for(rel)})" if "/" in label else f"[{label}]({url_for(rel)})"
            for label, rel in handed
            if f"({url_for(rel)})" not in shown + entry.text
        )
        web_links = " · ".join(
            f"[{label}]({url})"
            for label, url in web_links_of(entry.detail or "")
            if url not in shown
        )
        parts = [chip, entry.text, shown, file_links, web_links, link]
        return "\n\n".join(part for part in parts if part)
    if entry.kind in _PROJECT_KINDS:
        return "\n\n".join(part for part in (chip, entry.text, link) if part)
    return entry.text


def _quote(text: str) -> str:
    return "\n> ".join(text.strip().splitlines())


#: Hub messages still being read aloud (written and recorded), kept from collection.
_narrations: set[asyncio.Task[None]] = set()


def speech_text(entry: hub_module.HubEntry) -> str:
    """What the hub says aloud for an entry: its own words, never what it quotes or shows.

    Not the project's name or "finished" either: the chip shows which project it
    is. Nothing for a send or a new lane: the chip shows it went, and where.
    """
    from omnigent.server.spoken_summary import strip_markdown_for_speech

    if entry.kind in ("sent", "lane"):
        return ""
    return strip_markdown_for_speech(own_words(entry))


def file_url(base_url: str, session_id: str, relative: str) -> str:
    """A link that opens a file in its chat's file viewer."""
    return f"{base_url}/c/{session_id}?file={quote(relative, safe='/')}"


def link_known_files(text: str, files: dict[str, tuple[str, str]], base_url: str) -> str:
    """The hub's words with each file a project named linked to it in that project's chat."""

    def linked(match: re.Match[str]) -> str:
        label, target, code, bare = match.groups()
        if target is not None:
            # A link to a host path opens nothing in the hub, which has no folder.
            on_host = target if target.startswith(("/", "~/")) else ""
            found = _known_host_path(on_host, files) if on_host else None
            return f"[{label}]({file_url(base_url, *found)})" if found else match.group(0)
        named = code if code is not None else bare
        if named is None:
            return match.group(0)
        named_path = _POSITION_SUFFIX.sub("", named.strip())
        known = files.get(named_path)
        if known is None and "/" in named_path:
            known = _known_tail(named_path, files)
        if known is None:
            known = _known_spelling(named_path, files)
        if known is None:
            return match.group(0)
        url = file_url(base_url, *known)
        return f"[`{named}`]({url})" if code is not None else f"[{named}]({url})"

    lines, fenced = [], False
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            fenced = not fenced
        lines.append(line if fenced or _FENCE.match(line) else _NAMED_FILE.sub(linked, line))
    return "".join(lines)


def _known_tail(named: str, files: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """The one known file whose path ends in *named*, a path given short; ``None`` if several."""
    tail = "/" + named.lstrip("/")
    keys = [key for key in files if "/" in key and ("/" + key.lstrip("/")).endswith(tail)]
    targets = {files[key] for key in keys}
    return targets.pop() if len(targets) == 1 else None


def _spelling(name: str) -> str:
    """A file name without case, separators or folders: ``a/B_c-d.md`` -> ``bcdmd``."""
    return re.sub(r"[^a-z0-9]", "", name.rsplit("/", 1)[-1].lower())


def _known_spelling(named: str, files: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """The one known file whose name differs from *named* only in case or separators.

    A model retelling a report can drop a name's underscores or folders.
    """
    wanted = _spelling(named)
    if not wanted:
        return None
    targets = {target for key, target in files.items() if _spelling(key) == wanted}
    return targets.pop() if len(targets) == 1 else None


def _known_host_path(path: str, files: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """The known file a host path ends in, matched on its longest known relative path."""
    path = _POSITION_SUFFIX.sub("", path)
    ends = [key for key in files if "/" in key and path.endswith("/" + key.lstrip("/"))]
    return files[max(ends, key=len)] if ends else None


def known_files(
    entries: list[hub_module.HubEntry], workspace_of: Callable[[str], str | None]
) -> dict[str, tuple[str, str]]:
    """The files the latest reports handed over: by path, as written, and by name.

    The newest report wins a name two of them share.
    """
    found: dict[str, tuple[str, str]] = {}
    reports = [e for e in reversed(entries) if e.kind == "finished" and e.detail and e.session_id]
    for entry in reports[:_KNOWN_FILE_REPORTS]:
        assert entry.session_id is not None
        workspace = entry.workspace or workspace_of(entry.session_id)
        since = _sent_before(entries, entry) if entry.links is None else None
        for label, relative in entry_files(entry, workspace, since=since):
            for key in (relative, label, relative.rsplit("/", 1)[-1]):
                found.setdefault(key, (entry.session_id, relative))
    return found


def show_markdown(
    blocks: list[dict[str, Any]],
    workspace: str | None,
    url_for: Callable[[str], str],
    anchors: Sequence[str] = (),
) -> str:
    """A summary's ``show`` blocks as the chat draws them, in markdown.

    Images stored in the project's chat travel as copied files instead.
    """
    out = []
    for block in blocks:
        kind, content = block.get("kind"), str(block.get("content") or "").strip()
        label = str(block.get("label") or "").strip()
        if not content:
            continue
        if kind == "table":
            out.append(content)
        elif kind == "output":
            out.append(f"```\n{content}\n```")
        elif kind in ("image", "link"):
            if content.startswith("/v1/sessions/"):
                continue
            relative = workspace_file(content, workspace, anchors)
            if relative is not None:
                out.append(f"[{label or relative.rsplit('/', 1)[-1]}]({url_for(relative)})")
            elif content.startswith(("http://", "https://")):
                out.append(f"{'!' if kind == 'image' else ''}[{label or content}]({content})")
    return "\n\n".join(out)


def reply_tables(markdown: str) -> list[str]:
    """The markdown tables in a reply, outside code fences."""
    tables, current, fenced = [], [], False
    for line in [*markdown.splitlines(), ""]:
        if _FENCE.match(line):
            fenced = not fenced
        is_row = not fenced and line.lstrip().startswith("|")
        if is_row:
            current.append(line.strip())
            continue
        if len(current) >= 2 and re.fullmatch(r"\|?[\s:|-]+\|?", current[1]):
            tables.append("\n".join(current))
        current = []
    return tables


def web_links_of(markdown: str) -> list[tuple[str, str]]:
    """``(label, url)`` of each web address a reply gives, outside code fences.

    A summary seldom picks a link to show, so the report carries them all: a
    link the project handed over is something to open, not to read about.
    """
    found: list[tuple[str, str]] = []
    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _WEB_LINK.finditer(line):
            bang, title, target, bare = match.groups()
            if bang:
                continue
            url = target or bare
            if any(url == seen for _, seen in found):
                continue
            label = (title or "").strip() or url.split("://", 1)[1].rstrip("/")
            found.append((label.replace("[", "\\[").replace("]", "\\]"), url))
    return found[:_MAX_WEB_LINKS]


def _named(
    markdown: str, workspace: str | None, anchors: Sequence[str] = ()
) -> list[tuple[str, str, bool]]:
    """``(label, workspace path, linked)`` of each file a reply names; *linked* when as a link."""
    found: list[tuple[str, str, bool]] = []
    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _FILE_REF.finditer(line):
            bang, label, target, code = match.groups()
            if bang or (code is not None and "/" not in code):
                continue
            relative = workspace_file(code if code is not None else target, workspace, anchors)
            if relative is not None and all(relative != r for _, r, _l in found):
                found.append(
                    (code if code is not None else label or relative, relative, code is None)
                )
        prose = _NOT_BARE.sub(lambda m: " " * len(m.group(0)), line)
        for match in _BARE_PATH.finditer(prose):
            relative = workspace_file(match.group(1), workspace, anchors)
            if relative is not None and all(relative != r for _, r, _l in found):
                found.append((relative, relative, False))
    return found


def named_files(
    markdown: str, workspace: str | None, anchors: Sequence[str] = ()
) -> list[tuple[str, str]]:
    """``(label, workspace path)`` of each file a reply links or names as a path.

    As the chat's friendly view lifts them: markdown links to files,
    inline-code paths with a folder in them, and paths written as plain text;
    only files that exist.
    """
    return [(label, relative) for label, relative, _linked in _named(markdown, workspace, anchors)]


#: A file changed this long before the send still counts as the turn's work (clock skew).
_CHANGED_SLACK_S: Final[float] = 5.0


def handed_files(
    markdown: str,
    workspace: str | None,
    anchors: Sequence[str] = (),
    *,
    since: float | None = None,
) -> list[tuple[str, str]]:
    """``(label, workspace path)`` of the files a reply hands over, not every one it names.

    A file counts when the reply links to it, or names it and the turn that
    began at *since* changed it. A path only cited (code it read, a key it
    left alone) does not, nor app state in a hidden folder that changes on its
    own (logs, transcripts) unless linked. Without *since*, only links count.
    """
    handed: list[tuple[str, str]] = []
    root = pathlib.Path(workspace).expanduser() if workspace else None
    for label, relative, linked in _named(markdown, workspace, anchors):
        changed = False
        hidden = any(part.startswith(".") for part in relative.split("/")[:-1])
        if not linked and not hidden and since is not None and root is not None:
            try:
                changed = (root / relative).stat().st_mtime >= since - _CHANGED_SLACK_S
            except OSError:
                changed = False
        if linked or changed:
            handed.append((label, relative))
    return handed


def entry_files(
    entry: hub_module.HubEntry, workspace: str | None, *, since: float | None = None
) -> list[tuple[str, str]]:
    """The files a report handed over, as found when it came in.

    A report from before that was kept is judged now, its turn starting at *since*.
    """
    if entry.links is not None:
        return [(str(label), str(relative)) for label, relative in entry.links]
    return handed_files(entry.detail or "", workspace, entry.anchors or (), since=since)


def _sent_before(entries: list[hub_module.HubEntry], report: hub_module.HubEntry) -> float | None:
    """When the hub last sent work to the report's chat before it came in."""
    sends = [
        e.at
        for e in entries
        if e.kind == "sent" and e.session_id == report.session_id and e.at <= report.at
    ]
    return max(sends) if sends else None


def workspace_file(target: str, workspace: str | None, anchors: Sequence[str] = ()) -> str | None:
    """The workspace-relative path *target* names, when it is a file there.

    The chat page's rule: an absolute or ``~`` path counts only inside the
    workspace, and the file must exist. A relative path not found at the
    workspace's root is looked for in *anchors*, the folders the turn worked
    in: agents write paths relative to the repository they are in.
    """
    target = _POSITION_SUFFIX.sub("", target.strip())
    if not workspace or not target or any(c.isspace() for c in target):
        return None
    if "://" in target or target.startswith(("#", "mailto:", "/v1/", "//")):
        return None
    root = pathlib.Path(workspace).expanduser()
    path = pathlib.Path(target).expanduser()
    bases = [] if path.is_absolute() else [root, *(pathlib.Path(a) for a in anchors)]
    for candidate in [base / path for base in bases] if bases else [path]:
        try:
            relative = candidate.resolve().relative_to(root.resolve())
            found = candidate.is_file()
        except (ValueError, OSError):
            continue
        if found and relative.parts:
            return relative.as_posix()
    return None


def link_workspace_files(
    markdown: str,
    workspace: str | None,
    url_for: Callable[[str], str],
    anchors: Sequence[str] = (),
    only: Collection[str] | None = None,
) -> str:
    """The reply with each file it names linked to that file in its project's chat.

    Paths in code fences stay as written, as the chat page leaves them.
    :param only: When given, the workspace paths that may be linked; others stay text.
    """
    if not workspace:
        return markdown

    def linked(match: re.Match[str]) -> str:
        _bang, label, target, code = match.groups()
        relative = workspace_file(code if code is not None else target, workspace, anchors)
        if relative is None or (only is not None and relative not in only):
            return match.group(0)
        if code is not None:
            return f"[`{code}`]({url_for(relative)})"
        return f"[{label or relative.rsplit('/', 1)[-1]}]({url_for(relative)})"

    lines, fenced = [], False
    for line in markdown.splitlines(keepends=True):
        if _FENCE.match(line):
            fenced = not fenced
        lines.append(line if fenced or _FENCE.match(line) else _FILE_REF.sub(linked, line))
    return "".join(lines)


class HubConversation:
    """Writes what the hub says into its conversation, and shows it working."""

    def __init__(
        self, conversation_store: Any, session_id: str, state: hub_module.HubState | None = None
    ) -> None:
        self.conversation_store = conversation_store
        self.session_id = session_id
        #: The app's address as the reader last opened it, for links.
        self.base_url = ""
        #: The hub's log, for the files its reports named.
        self.state = state
        self._workspaces: dict[str, str | None] = {}

    def _workspace(self, session_id: str) -> str | None:
        """A chat's folder; reports written before they kept it need it looked up."""
        if session_id not in self._workspaces:
            conv = self.conversation_store.get_conversation(session_id)
            self._workspaces[session_id] = getattr(conv, "workspace", None)
        return self._workspaces[session_id]

    def known_files(self) -> dict[str, tuple[str, str]]:
        """The files projects' replies named, for linking wherever the hub mentions one."""
        if self.state is None or not self.base_url:
            return {}
        try:
            return known_files(self.state.entries, self._workspace)
        except Exception:  # noqa: BLE001 - unlinked text beats no message
            _logger.warning("hub could not gather the files projects named", exc_info=True)
            return {}

    def post(self, entry: hub_module.HubEntry) -> None:
        """Store one hub entry as an assistant message and publish it."""
        from omnigent.db.utils import generate_task_id
        from omnigent.entities import NewConversationItem
        from omnigent.entities.conversation import parse_item_data
        from omnigent.server.routes._sessions.common import session_stream
        from omnigent.server.schemas import OutputItemDoneEvent

        try:
            item = NewConversationItem(
                type="message",
                response_id=generate_task_id(),
                data=parse_item_data(
                    "message",
                    {
                        "type": "message",
                        "role": "assistant",
                        "agent": HUB_AGENT_NAME,
                        "content": [
                            {
                                "type": "output_text",
                                "text": render(entry, self.base_url, self.known_files()),
                            },
                            *(entry.files or []),
                        ],
                    },
                ),
            )
            persisted = self.conversation_store.append(self.session_id, [item])
        except Exception:  # noqa: BLE001 - the hub's own log still has it
            _logger.warning("hub message not stored in %s", self.session_id, exc_info=True)
            return
        if persisted:
            session_stream.publish(
                self.session_id,
                OutputItemDoneEvent(
                    type="response.output_item.done", item=persisted[0].to_api_dict()
                ).model_dump(),
            )
            self._narrate(entry, persisted[0].response_id)

    def _narrate(self, entry: hub_module.HubEntry, response_id: str | None) -> None:
        """Have the page read the message aloud, as it reads a project's summary."""
        text = speech_text(entry)
        if not text or not response_id:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._post_speech(text, response_id))
        except RuntimeError:  # no event loop, so nothing to record it with
            return
        _narrations.add(task)
        task.add_done_callback(_narrations.discard)

    async def _post_speech(self, text: str, response_id: str) -> None:
        """Attach the words to speak to a message, then its recording when one is made.

        A spoken summary the page only reads aloud (``speak_only``): the message
        itself, with its tables and links, stays what the chat shows.
        """
        from omnigent.entities import NewConversationItem
        from omnigent.entities.conversation import parse_item_data
        from omnigent.server.routes._sessions.common import session_stream
        from omnigent.server.routes._sessions.helpers import (
            _schedule_summary_audio_if_enabled,
        )
        from omnigent.server.schemas import OutputItemDoneEvent
        from omnigent.server.spoken_summary import (
            detect_bcp47_language,
            resolve_spoken_summary_settings_async,
        )

        try:
            enabled, language, _conv = await resolve_spoken_summary_settings_async(
                self.session_id, self.conversation_store
            )
            if not enabled:
                return
            if not language or language == "auto":
                language = detect_bcp47_language(text)
            file_store, artifact_store = hub_module._file_stores or (None, None)
            part, record = await _schedule_summary_audio_if_enabled(
                self.conversation_store,
                file_store,
                artifact_store,
                self.session_id,
                response_id,
                {"type": "spoken_summary", "text": text, "lang": language, "speak_only": True},
            )
            item = NewConversationItem(
                type="message",
                response_id=response_id,
                data=parse_item_data(
                    "message",
                    {
                        "type": "message",
                        "role": "assistant",
                        "agent": "spoken_summary",
                        "content": [part],
                    },
                ),
            )
            persisted = await asyncio.to_thread(
                self.conversation_store.append, self.session_id, [item]
            )
            if persisted:
                session_stream.publish(
                    self.session_id,
                    OutputItemDoneEvent(
                        type="response.output_item.done", item=persisted[0].to_api_dict()
                    ).model_dump(),
                )
            record()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the written message already landed
            _logger.warning("hub message not read aloud in %s", self.session_id, exc_info=True)

    def post_user(self, text: str) -> None:
        """Store what they said on a voice call as their message, and publish it."""
        from omnigent.db.utils import generate_task_id
        from omnigent.entities import NewConversationItem
        from omnigent.entities.conversation import parse_item_data
        from omnigent.server.routes._sessions.helpers import _publish_input_consumed

        if not text.strip():
            return
        try:
            item = NewConversationItem(
                type="message",
                response_id=generate_task_id(),
                data=parse_item_data(
                    "message",
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"🎙 {text.strip()}"}],
                    },
                ),
            )
            persisted = self.conversation_store.append(self.session_id, [item])
        except Exception:  # noqa: BLE001 - the hub's own log still has it
            _logger.warning("hub voice line not stored in %s", self.session_id, exc_info=True)
            return
        if persisted:
            _publish_input_consumed(self.session_id, persisted[0])

    async def language(self) -> str:
        """This chat's language setting, e.g. ``"pt-BR"``; ``""`` when unset."""
        from omnigent.server.spoken_summary import resolve_spoken_summary_settings_async

        _on, language, _conv = await resolve_spoken_summary_settings_async(
            self.session_id, self.conversation_store
        )
        return "" if language.strip().lower() in ("", "auto") else language.strip()

    def busy(self, working: bool) -> None:
        """Show the chat as thinking while the hub works on a message."""
        from omnigent.server.routes._sessions.helpers import _publish_status

        try:
            _publish_status(self.session_id, "running" if working else "idle", None)
        except Exception:  # noqa: BLE001 - a status edge is cosmetic
            _logger.debug("hub status not published", exc_info=True)


def base_url(headers: Any) -> str:
    """The app's address as this caller sees it: their Origin, else their Host."""
    origin = str(headers.get("origin") or "").rstrip("/")
    if origin.startswith(("http://", "https://")):
        return origin
    host = str(headers.get("host") or "")
    if not host:
        return ""
    scheme = str(headers.get("x-forwarded-proto") or "http").split(",")[0].strip()
    return f"{scheme}://{host}"


def attach(hub: hub_module.Hub, conversation_store: Any, session_id: str | None) -> None:
    """Point the hub at its conversation, so what it says lands there."""
    if session_id is None:
        return
    if hub.chat is None or hub.chat.session_id != session_id:
        hub.chat = HubConversation(conversation_store, session_id, hub.state)


def adopt_address(chat: HubConversation, headers: Any) -> None:
    """Link to the app at this caller's address, if it is a page the reader opened.

    A script calling the API has no Origin, and its Host (``127.0.0.1``) is not
    the address the reader's browser uses, so it only fills an address not yet known.
    """
    base = base_url(headers)
    if base and (headers.get("origin") or not chat.base_url):
        chat.base_url = base


def bind_request(
    hub: hub_module.Hub,
    *,
    conversation_store: Any,
    user_id: str | None,
    app: Any,
    headers: Any,
    companions: Any,
) -> None:
    """Bind the hub to this caller: sends act as them, through the app's routes."""
    if hub.chat is not None:
        adopt_address(hub.chat, headers)
    hub.bind(
        hub_module.store_backend(
            conversation_store,
            user_id=user_id,
            app=app,
            headers={k: v for k, v in headers.items() if k in FORWARDED_HEADERS},
            companions=companions,
            exclude={hub.chat.session_id} if hub.chat is not None else set(),
        )
    )


def resume_after_restart(*, conversation_store: Any, app: Any, companions: Any) -> int:
    """Resume the report-backs every user's hub owed when the server stopped.

    Called once at startup. The watches only read stores, so the backend is
    bound without a caller's credentials; the first request binds its own.

    :returns: How many watches were started again.
    """
    resumed = 0
    for path in sorted(hub_module.HUB_DIR.glob("*/watches.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or not raw.get("watches"):
            continue
        user_id = raw.get("user_id")
        hub = hub_module.hub_for(user_id)
        attach(hub, conversation_store, known_hub_id(user_id))
        bind_request(
            hub,
            conversation_store=conversation_store,
            user_id=user_id,
            app=app,
            headers={},
            companions=companions,
        )
        if hub.chat is not None and raw.get("base_url"):
            hub.chat.base_url = str(raw["base_url"])
        resumed += hub.resume_watches()
    return resumed


async def accept_message(
    *,
    conv: Any,
    body: Any,
    conversation_store: Any,
    user_id: str | None,
    created_by: str | None,
    app: Any,
    headers: Any,
) -> dict[str, bool | str]:
    """Take a message typed into the hub conversation: store it, then answer it.

    Stands in for the runner dispatch: nothing is forwarded anywhere. The
    hub's reply arrives as later assistant messages on the stream.
    """
    from omnigent.db.utils import generate_task_id
    from omnigent.server.discussion import registry
    from omnigent.server.routes._sessions.helpers import (
        _build_new_item,
        _message_text,
        _publish_input_consumed,
    )

    content = body.data.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    text = (_message_text(blocks) or "").strip()
    user_item = _build_new_item(body, generate_task_id(), created_by=created_by)
    persisted = await asyncio.to_thread(conversation_store.append, conv.id, [user_item])
    consumed = persisted[0]
    _publish_input_consumed(conv.id, consumed)
    hub = hub_module.hub_for(user_id)
    attach(hub, conversation_store, conv.id)
    bind_request(
        hub,
        conversation_store=conversation_store,
        user_id=user_id,
        app=app,
        headers=headers,
        companions=registry,
    )
    files = hub_module.attached_files(blocks, conv.id)
    if text or files:
        hub.submit(text, via="typed", files=files)
    return {"queued": True, "item_id": consumed.id}
