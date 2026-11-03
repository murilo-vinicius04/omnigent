"""Tools for taking turns on shared resources (the robot, a camera, a GPU) with other agents."""

from __future__ import annotations

from typing import Any

from omnigent.tools.base import Tool

_SAFETY = (
    "Having the turn only says whose turn it is. It is never permission to move real "
    "hardware: that still needs the person's explicit go. The board is advisory and "
    "does not stop external operators or processes; verify the hardware is actually free."
)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


class SysResourceAcquireTool(Tool):
    """Schema-only tool: claim a shared resource, or queue for it."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "sys_resource_acquire"

    @classmethod
    def description(cls) -> str:
        """Return the LLM-facing description."""
        return (
            "Claim a resource shared with other agents and people (the real robot, a camera, a "
            "GPU, a lab machine, a network port) before you use it. If another lane holds it you "
            "are put in a first-come queue and told who has it and for how long; you are also "
            "notified (read it with sys_duet_inbox) when it becomes yours, and then you must "
            "confirm by calling this again within a few minutes or the turn passes on. Call it "
            "again to renew a claim you hold. Do this on your own, without being asked, before "
            "any test that uses such a resource. Check sys_resource_status first and reuse the "
            "names it lists. " + _SAFETY
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format schema."""
        return _schema(
            self.name(),
            self.description(),
            {
                "resource": {
                    "type": "string",
                    "description": "Short name, e.g. 'franka-robot' or 'lab-gpu'.",
                    "maxLength": 48,
                },
                "purpose": {
                    "type": "string",
                    "description": "One line on what you will do with it.",
                    "maxLength": 160,
                },
                "ttl_minutes": {
                    "type": "integer",
                    "description": "How long you expect to need it, up to 240 (default 30).",
                    "minimum": 1,
                    "maximum": 240,
                },
            },
            ["resource", "purpose"],
        )


class SysResourceReleaseTool(Tool):
    """Schema-only tool: give a resource back, or leave its queue."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "sys_resource_release"

    @classmethod
    def description(cls) -> str:
        """Return the LLM-facing description."""
        return (
            "Release a shared resource you hold as soon as you are done (the next lane in "
            "line is told), or leave its queue if you no longer need it. Always release when "
            "your test ends, fails or is abandoned, without waiting to be asked. If you hold "
            "it but are not ready and another lane is waiting, you may set yield_to_next=true "
            "to give them the turn and remain at the end of the queue. Only yield after "
            "your processes have stopped using the resource."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format schema."""
        return _schema(
            self.name(),
            self.description(),
            {
                "resource": {"type": "string", "maxLength": 48},
                "yield_to_next": {"type": "boolean"},
            },
            ["resource"],
        )


class SysResourceStatusTool(Tool):
    """Schema-only tool: see who holds and who waits for shared resources."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "sys_resource_status"

    @classmethod
    def description(cls) -> str:
        """Return the LLM-facing description."""
        return (
            "Show who holds each shared resource, for how long, and who is waiting. Call it "
            "before planning a test that needs the robot, a camera, a GPU or a lab machine, "
            "and omit 'resource' to list all of them."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format schema."""
        return _schema(
            self.name(),
            self.description(),
            {"resource": {"type": "string", "maxLength": 48}},
            [],
        )
