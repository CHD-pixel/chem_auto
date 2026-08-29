"""Always-visible tools — available in every pipeline stage.

Includes: ask_user, load_state_values.
"""

from __future__ import annotations

from google.adk.tools import ToolContext

from app.services.session_service import load_required_state_values


def ask_user(question: str, kind: str,
             tool_context: ToolContext) -> dict:
    """Pause execution and ask the user a question. Returns their reply on next turn.

    Use this for confirmations, selections, or clarifications.
    - kind='free_text': user types any response
    - kind='yes_no': user replies yes/no/uncertain
    - kind='selection': user picks from a list (provide options in the question)

    The agent will pause and wait for the user's response before continuing.
    """
    return {"status": "waiting", "question": question, "kind": kind}


async def load_state_values(required_keys: list[str], tool_context: ToolContext) -> dict:
    """Load lightweight session.state values and report any missing required keys.

    Args:
        required_keys: State keys that should exist in the current session.

    Returns:
        A JSON-serializable payload containing found values and missing keys.
    """
    values, missing = load_required_state_values(tool_context.state, required_keys)
    return {
        "status": "success" if not missing else "partial",
        "values": values,
        "missing_keys": missing,
    }
