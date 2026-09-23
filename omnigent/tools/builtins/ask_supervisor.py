"""``sys_ask_supervisor``: a worker asks its supervisor a yes/no or pick-one question."""

from __future__ import annotations

from typing import Any

from omnigent.supervisor import MAX_OPTIONS, MAX_QUESTION_CHARS
from omnigent.tools.base import Tool


class SysAskSupervisorTool(Tool):
    """Schema-only tool; the runner dispatches it (``omnigent.runner.supervisor_tool``)."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "sys_ask_supervisor"

    @classmethod
    def description(cls) -> str:
        """Return the LLM-facing description."""
        return (
            "Ask your supervisor one narrow question and get a calibrated answer in under a "
            "second. It has read what your orchestrator knows about this task (the human's "
            "request, your brief, the orchestrator's latest notes) and everything you have "
            "done so far. It decides; it never writes text or code.\n"
            "You MUST ask it before you guess and before you stop: which file to change, "
            "which approach fits the brief, whether an output means your change worked, "
            "whether you are done, whether to start editing now or keep reading. Asking "
            "costs almost nothing; a wrong guess costs a whole review round.\n"
            "Use kind=yes_no, or kind=choice with the options listed (kind=score for "
            "ordered levels, lowest first). Put the output or snippet it should judge in "
            "`evidence`. If it answers that it is not sure, check the thing yourself and "
            "ask again with the result, or end your turn with the question for your "
            "orchestrator."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["yes_no", "choice", "score"],
                            "description": (
                                "yes_no, choice (pick one option), or score (ordered levels)."
                            ),
                        },
                        "question": {
                            "type": "string",
                            "description": (
                                "One narrow question, e.g. 'Does this test output show my fix "
                                "works?' or 'Which file holds the retry logic?'."
                            ),
                            "maxLength": MAX_QUESTION_CHARS,
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": MAX_OPTIONS,
                            "description": (
                                "The choices (choice) or levels, lowest first (score). "
                                "Omit for yes_no."
                            ),
                        },
                        "evidence": {
                            "type": "string",
                            "description": (
                                "Text for it to judge with this question: a test output, a "
                                "diff hunk, a file excerpt. It already has your history."
                            ),
                        },
                    },
                    "required": ["kind", "question"],
                    "additionalProperties": False,
                },
            },
        }
