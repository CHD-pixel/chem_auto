"""Generic code editing tools — Edit (search/replace) and Write (full overwrite).

Modeled after Claude Code's Edit/Write tools. These operate on the current
candidate driver artifact referenced by state[CURRENT_CANDIDATE_CODE].

Usage by the agent:
  - write_code(content)      — full overwrite, for first generation or complete rewrite
  - edit_code(old, new)      — precise search/replace, for incremental fixes
"""

from __future__ import annotations

import ast
import logging
import re

from google.adk.tools import ToolContext

from app.constants.state_keys import CURRENT_CANDIDATE_CODE
from app.tools._helpers import get_invocation_context

logger = logging.getLogger(__name__)


async def write_code(content: str, tool_context: ToolContext) -> dict:
    """Overwrite the current candidate driver code with new content.

    Use this for complete rewrites or initial code generation.
    For incremental changes, use edit_code() instead.

    Args:
        content: The complete Python source code to write.

    Returns:
        dict with status, code_length, syntax_ok, syntax_error.
    """
    if not content or not content.strip():
        return {"status": "error", "message": "Content is empty."}

    # Syntax validation
    syntax_ok = False
    syntax_error = ""
    try:
        ast.parse(content)
        syntax_ok = True
    except SyntaxError as e:
        syntax_error = f"{e.msg} (line {e.lineno})"
        return {
            "status": "error",
            "message": f"Syntax error: {syntax_error}",
            "hint": "Fix the syntax error and try again.",
        }

    ctx = get_invocation_context(tool_context)
    state = tool_context.state
    artifact_name = state.get(CURRENT_CANDIDATE_CODE, "")

    if not artifact_name:
        return {
            "status": "error",
            "message": "No candidate code exists. Call generate_code() first.",
        }

    from google.genai import types as genai_types
    part = genai_types.Part.from_text(text=content)
    try:
        version = await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename=artifact_name,
            artifact=part,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Failed to save artifact: {exc}"}

    methods = [m for m in re.findall(
        r"^\s*def\s+(\w+)\s*\(self", content, re.MULTILINE
    ) if not m.startswith("_")]

    logger.info("write_code: saved %d chars, %d public methods (v%d)", len(content), len(methods), version)
    return {
        "status": "completed",
        "code_length": len(content),
        "syntax_ok": True,
        "method_count": len(methods),
        "methods": methods[:15],
        "version": version,
    }


async def edit_code(old_string: str, new_string: str, tool_context: ToolContext) -> dict:
    """Apply a precise search/replace edit to the current candidate driver code.

    The old_string must match exactly one location in the code.
    After replacement, the code is validated for syntax errors.

    Use this for incremental changes (adding functions, fixing bugs).
    For complete rewrites, use write_code() instead.

    Args:
        old_string: The exact text to find in the code (must be unique).
        new_string: The replacement text.

    Returns:
        dict with status, syntax_ok, syntax_error.
    """
    if not old_string:
        return {"status": "error", "message": "old_string is empty. Use write_code() for full rewrites."}

    ctx = get_invocation_context(tool_context)
    state = tool_context.state
    artifact_name = state.get(CURRENT_CANDIDATE_CODE, "")

    if not artifact_name:
        return {
            "status": "error",
            "message": "No candidate code exists. Call generate_code() first.",
        }

    # Load current code
    from app.agents.shared.artifact_io import load_text_artifact
    code = await load_text_artifact(ctx, artifact_name)
    if not code:
        return {"status": "error", "message": f"Cannot load artifact: {artifact_name}"}

    # Uniqueness check
    count = code.count(old_string)
    if count == 0:
        # Try normalized matching (handle CRLF/whitespace differences)
        old_norm = old_string.replace("\r\n", "\n").strip()
        code_norm = code.replace("\r\n", "\n")
        if old_norm in code_norm:
            # Find the actual string in the original code
            idx = code_norm.index(old_norm)
            old_string = code[idx:idx + len(old_norm)]
            count = 1
        else:
            return {
                "status": "error",
                "message": "old_string not found in code.",
                "hint": "Ensure the string matches exactly, including indentation and whitespace.",
            }
    if count > 1:
        return {
            "status": "error",
            "message": f"old_string matches {count} locations. Provide more context to make it unique.",
            "hint": "Include surrounding lines to make the match unique.",
        }

    # Apply edit
    new_code = code.replace(old_string, new_string, 1)

    # Syntax validation
    syntax_ok = False
    syntax_error = ""
    try:
        ast.parse(new_code)
        syntax_ok = True
    except SyntaxError as e:
        syntax_error = f"{e.msg} (line {e.lineno})"
        # Revert — don't save broken code
        return {
            "status": "error",
            "message": f"Edit would cause syntax error: {syntax_error}",
            "hint": "Fix the new_string and try again.",
        }

    # Save
    from google.genai import types as genai_types
    part = genai_types.Part.from_text(text=new_code)
    try:
        version = await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            filename=artifact_name,
            artifact=part,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Failed to save artifact: {exc}"}

    # Build diff summary
    old_lines = code.split("\n")
    new_lines = new_code.split("\n")
    line_diff = len(new_lines) - len(old_lines)
    diff_sign = "+" if line_diff >= 0 else ""

    logger.info("edit_code: applied edit, %s%d lines (v%d)", diff_sign, line_diff, version)
    return {
        "status": "completed",
        "syntax_ok": True,
        "lines_changed": line_diff,
        "version": version,
        "message": f"Edit applied successfully. {diff_sign}{line_diff} lines.",
    }
