"""Extractor for to-do checklist blocks embedded in assistant messages.

Pure parser: finds a ``## To-do`` heading (case-insensitive, tolerating variants like
``## To-do, updated``), parses markdown checklist items into two states (``"pending"``
and ``"completed"``), and strips markdown bold and links into plain text.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Regex for matching the start of a to-do section: ## To-do, ## To-do, updated, ## Todo, etc.
_TODO_HEADER_RE = re.compile(r"^\s{0,3}##\s+to[- ]?do\b.*$", re.IGNORECASE)

# Regex for matching any level-2 heading (or level-1) that terminates the to-do section
_NEXT_HEADING_RE = re.compile(r"^\s{0,3}#{1,2}\s+[^#]")

# Regex for matching a Done subsection: ### Done, ### Done: etc.
_DONE_SECTION_RE = re.compile(r"^\s{0,3}###\s+done\b.*$", re.IGNORECASE)

# Regex for matching other subsection headings: ### ...
_SUBSECTION_RE = re.compile(r"^\s{0,3}###\s+.*$")

# Regex for matching a checklist item: - [ ], - [x], - [X], * [ ], + [ ], with optional indentation
_CHECKLIST_ITEM_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s*(.*)$")

# Regex to strip markdown links: [text](url) -> text
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")

# Regex to strip markdown bold: **text** or __text__ -> text
_BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1")


class TodoExtractResult(NamedTuple):
    """Result of extracting todos from an assistant message.

    :param todos: List of todo dicts, each with ``content`` and ``status``.
    :param found: True if a ``## To-do`` block was found in the message.
    """

    todos: list[dict[str, str]]
    found: bool


def _clean_item_text(text: str) -> str:
    """Strip markdown bold and link syntax into plain text."""
    # First replace links: [anchor](url) -> anchor
    cleaned = _LINK_RE.sub(r"\1", text)
    # Then strip bold: **bold** or __bold__ -> bold
    cleaned = _BOLD_RE.sub(r"\2", cleaned)
    return cleaned.strip()


def extract_todos(text: str) -> TodoExtractResult:
    """Extract checklist items from a ``## To-do`` markdown section.

    :param text: Markdown text of an assistant message.
    :returns: A ``TodoExtractResult(todos, found)`` tuple.
    """
    if not text:
        return TodoExtractResult(todos=[], found=False)

    lines = text.splitlines()
    in_todo_block = False
    in_done_section = False
    todos: list[dict[str, str]] = []

    for line in lines:
        if not in_todo_block:
            if _TODO_HEADER_RE.match(line):
                in_todo_block = True
                in_done_section = False
            continue

        # If we are in the to-do block and hit another level 1 or 2 heading, the block ends
        if _NEXT_HEADING_RE.match(line):
            break

        # Check for subsection headings (###)
        if _DONE_SECTION_RE.match(line):
            in_done_section = True
            continue
        if _SUBSECTION_RE.match(line):
            in_done_section = False
            continue

        # Check for checklist items
        match = _CHECKLIST_ITEM_RE.match(line)
        if match:
            mark = match.group(1)
            raw_content = match.group(2)
            content = _clean_item_text(raw_content)

            if mark.lower() == "x" or in_done_section:
                status = "completed"
            else:
                status = "pending"

            todos.append({"content": content, "status": status})

    return TodoExtractResult(todos=todos, found=in_todo_block)


def strip_todo_block(text: str) -> str:
    """Remove the ``## To-do`` block and its items from markdown text.

    Used by the spoken-summary generator so to-do checklist items are not read aloud.

    :param text: Raw assistant markdown text.
    :returns: Markdown text with the to-do block removed.
    """
    if not text:
        return text

    lines = text.splitlines()
    output_lines: list[str] = []
    in_todo_block = False
    found = False

    for line in lines:
        if not in_todo_block:
            if _TODO_HEADER_RE.match(line):
                in_todo_block = True
                found = True
                continue
            output_lines.append(line)
        else:
            if _NEXT_HEADING_RE.match(line):
                in_todo_block = False
                output_lines.append(line)

    if not found:
        return text

    return "\n".join(output_lines)
