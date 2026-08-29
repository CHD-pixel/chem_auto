"""Internal helpers for tool functions — not exposed as ADK tools.

Extracted from tools.py to avoid circular imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.tools.tool_context import ToolContext


# ── InvocationContext access ──────────────────────────────────────────


def get_invocation_context(tool_context: "ToolContext") -> "InvocationContext":
    """Get the InvocationContext from a ToolContext.

    Wraps the private `_invocation_context` attribute access so that if ADK
    changes its internal API, only this function needs updating.
    """
    return tool_context._invocation_context
