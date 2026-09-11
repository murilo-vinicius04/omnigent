"""Parts of an answer worth showing under its summary, rather than describing.

The rewrite is prose meant to be heard, so everything that is not prose is
stripped before it (see :func:`~omnigent.server.spoken_summary.strip_markdown_for_speech`)
and the reader loses it: the results table they asked for, the chart, the file
that was the whole point of the turn. Describing those aloud is the worst of
both -- long to listen to, and still no numbers.

So the summary carries a second channel. This module finds the candidates and
formats them for the rewriter to choose from; the chosen ones ride on the
summary as ``show`` blocks, rendered under the text and never spoken.

Code is a candidate like any other, but the reader decides whether they ever
want to see it: for most turns a block of source under a spoken summary is
noise, and for some it is the answer. The choice lives in their settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Longest fenced block still treated as quotable output. Past this it is a
#: listing, which belongs in the original rather than under a summary.
_MAX_OUTPUT_LINES = 8

#: Languages that mark a fence as source rather than output.
_CODE_FENCES = frozenset(
    {
        "bash",
        "c",
        "cpp",
        "cs",
        "css",
        "diff",
        "go",
        "html",
        "java",
        "js",
        "json",
        "jsx",
        "kotlin",
        "lua",
        "make",
        "php",
        "py",
        "python",
        "r",
        "ruby",
        "rust",
        "scala",
        "sh",
        "shell",
        "sql",
        "swift",
        "toml",
        "ts",
        "tsx",
        "typescript",
        "yaml",
        "yml",
        "zsh",
    }
)

#: Most blocks offered to the rewriter. Beyond this the choice stops being a
#: choice, and the turn is a listing rather than an answer with a highlight.
MAX_CANDIDATES = 8


@dataclass(frozen=True)
class ShowBlock:
    """One candidate for display under the summary.

    :param id: Stable within one turn; what the rewriter selects by.
    :param kind: ``"table"``, ``"image"``, ``"link"``, ``"output"``, ``"code"``
        or ``"file"``.
    :param label: Short human description, shown to the rewriter and used as
        the block's caption when it carries no text of its own.
    :param content: The block itself, verbatim: markdown for a table, a URL for
        an image or link, the quoted lines for output, a file id for a file.
    :param meta: Extra fields the renderer needs, e.g. a file's name and type.
    """

    id: int
    kind: str
    label: str
    content: str
    meta: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the ``show`` list on a spoken-summary part."""
        return {"kind": self.kind, "label": self.label, "content": self.content, **self.meta}


def _table_label(table: str) -> str:
    header = table.splitlines()[0]
    columns = [c.strip() for c in header.strip().strip("|").split("|") if c.strip()][:6]
    rows = max(0, len([line for line in table.splitlines() if line.strip().startswith("|")]) - 2)
    columns_text = ", ".join(columns) if columns else "unlabelled"
    return f"table of {rows} row{'s' if rows != 1 else ''} ({columns_text})"


def extract_show_candidates(text: str) -> list[ShowBlock]:
    """Find the parts of *text* that could be shown instead of described.

    :param text: The turn's raw assistant text, before stripping.
    :returns: Candidates in the order they appear, capped at
        :data:`MAX_CANDIDATES`.
    """
    if not text:
        return []
    found: list[tuple[int, str, str, str]] = []  # (position, kind, label, content)

    table_re = re.compile(
        r"(?:^[ \t]*\|.+\|[ \t]*$\n)+", re.M
    )  # consecutive pipe rows: header, delimiter, body
    for match in table_re.finditer(text):
        block = match.group(0).strip()
        lines = block.splitlines()
        if len(lines) < 3 or not re.match(r"^[ \t]*\|[ \t:|-]+\|[ \t]*$", lines[1]):
            continue  # a lone pipe row is not a table
        found.append((match.start(), "table", _table_label(block), block))

    for match in re.finditer(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", text):
        alt = match.group(1).strip() or "image"
        found.append((match.start(), "image", f"image ({alt})", match.group(2)))

    for match in re.finditer(r"(?<!!)\[([^\]]+)\]\((https?://[^)\s]+)[^)]*\)", text):
        title = match.group(1).strip()
        found.append((match.start(), "link", f"link ({title})", match.group(2)))

    for match in re.finditer(
        r"^[ \t]*```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)^[ \t]*```", text, re.M | re.S
    ):
        language, body = match.group(1).lower(), match.group(2).rstrip()
        lines = body.splitlines()
        if not lines:
            continue
        if language in _CODE_FENCES:
            count = len(lines)
            label = f"code in {language}, {count} line{'s' if count != 1 else ''}"
            found.append((match.start(), "code", label, body))
            continue
        if len(lines) > _MAX_OUTPUT_LINES:
            continue  # a listing: the original is the place for it
        first = lines[0].strip()[:60]
        found.append((match.start(), "output", f"output ({first})", body))

    found.sort(key=lambda item: item[0])
    return [
        ShowBlock(id=index + 1, kind=kind, label=label, content=content)
        for index, (_, kind, label, content) in enumerate(found[:MAX_CANDIDATES])
    ]


def file_blocks(files: list[dict[str, Any]]) -> list[ShowBlock]:
    """Turn a turn's attached files into blocks, which are always shown.

    A file was attached deliberately, so the reader gets it: that judgement was
    already made when it was sent, and the failure the reader reported is a
    file that never surfaced at all.

    :param files: ``output_file`` content blocks from the turn's items.
    :returns: One block per file, numbered after the text candidates.
    """
    blocks: list[ShowBlock] = []
    for index, raw in enumerate(files):
        file_id = str(raw.get("file_id") or "").strip()
        if not file_id:
            continue
        name = str(raw.get("filename") or "file").strip()
        mime = str(raw.get("mime_type") or "").strip()
        blocks.append(
            ShowBlock(
                id=-(index + 1),  # negative: never offered as a choice
                kind="file",
                label=f"file ({name})",
                content=file_id,
                meta={"filename": name, **({"mime_type": mime} if mime else {})},
            )
        )
    return blocks


def describe_candidates(blocks: list[ShowBlock]) -> str:
    """Render the candidate list for the rewriter's prompt.

    :param blocks: Candidates from :func:`extract_show_candidates`.
    :returns: One line per candidate, ``"1. table of 3 rows (engine, WER)"``.
    """
    return "\n".join(f"{block.id}. {block.label}" for block in blocks)


def parse_show_selection(raw: str, blocks: list[ShowBlock]) -> tuple[str, list[ShowBlock]]:
    """Split a rewrite into its prose and the blocks it asked to show.

    The rewriter answers with the summary and, when something is worth showing,
    a final ``SHOW: 1, 3`` line. A sentinel line rather than structured output
    so every backend can produce it and a model that ignores it still yields a
    usable summary.

    :param raw: The rewriter's reply.
    :param blocks: The candidates it was offered.
    :returns: ``(prose, chosen blocks)``; unknown ids are dropped.
    """
    by_id = {block.id: block for block in blocks}
    chosen: list[ShowBlock] = []
    kept: list[str] = []
    for line in (raw or "").splitlines():
        match = re.match(r"^\s*SHOW:\s*(.*)$", line, re.I)
        if not match:
            kept.append(line)
            continue
        for token in re.findall(r"-?\d+", match.group(1)):
            block = by_id.get(int(token))
            if block is not None and block not in chosen:
                chosen.append(block)
    return "\n".join(kept).strip(), chosen
