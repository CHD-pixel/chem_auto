"""Unified artifact I/O — single source of truth for loading/saving artifacts.

Consolidates duplicate implementations from:
  - code_test_agent.py (_load_text_artifact, _save_text_artifact)
  - publish_agent.py (_load_text_artifact, _save_text_artifact, _save_json_artifact, etc.)
  - invoke_pipeline.py (_load_json_from_artifact)
  - services/artifact_service.py (load_text_artifact, save_text_artifact)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.agents.invocation_context import InvocationContext
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

_logger = logging.getLogger(__name__)


# ── InvocationContext-based (for use inside BaseAgent._run_async_impl) ────


async def load_text_artifact(ctx: InvocationContext, artifact_name: str) -> str | None:
    """Load a text artifact by name. Returns None if not found."""
    if ctx.artifact_service is None:
        return None
    try:
        part = await ctx.artifact_service.load_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename=artifact_name,
        )
    except Exception:
        return None
    if part is None:
        return None
    text = getattr(part, "text", None)
    if text is not None:
        return text
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None) is not None:
        return inline.data.decode("utf-8", errors="replace")
    return None


async def save_text_artifact(ctx: InvocationContext, artifact_name: str, text: str) -> None:
    """Save text as an artifact. Falls back to disk if artifact_service is None."""
    if ctx.artifact_service is not None:
        await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename=artifact_name,
            artifact=genai_types.Part.from_text(text=text),
        )
        return
    # Fallback: write to disk directly
    import pathlib
    _logger.warning("artifact_service is None, writing %s to disk", artifact_name)
    dest = pathlib.Path(".adk/artifacts") / artifact_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


async def load_json_artifact(
    ctx: InvocationContext, artifact_name: str, default: Any = None,
) -> Any:
    """Load and parse a JSON artifact. Returns default if not found or invalid."""
    text = await load_text_artifact(ctx, artifact_name)
    if text is None:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


async def save_json_artifact(ctx: InvocationContext, artifact_name: str, data: Any) -> None:
    """Save a JSON-serializable object as an artifact."""
    await save_text_artifact(ctx, artifact_name, json.dumps(data, ensure_ascii=False, indent=2))


# ── ToolContext-based (for use inside ADK tool functions) ─────────────────


async def tc_load_json_artifact(
    tool_context: ToolContext, artifact_name: str, default: Any = None,
) -> Any:
    """Load and parse a JSON artifact via ToolContext."""
    from app.tools._helpers import get_invocation_context
    ctx = get_invocation_context(tool_context)
    return await load_json_artifact(ctx, artifact_name, default)


async def tc_save_json_artifact(tool_context: ToolContext, artifact_name: str, data: Any) -> None:
    """Save a JSON-serializable object as an artifact via ToolContext."""
    from app.tools._helpers import get_invocation_context
    ctx = get_invocation_context(tool_context)
    await save_json_artifact(ctx, artifact_name, data)
