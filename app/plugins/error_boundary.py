"""Layer-1 error boundary: catches model and tool errors at the ADK plugin level.

Returns fallback responses for transient errors; propagates after
MAX_CONSECUTIVE_ERRORS so that layer 2 (root agent try/except) can handle
persistent failures.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

_logger = logging.getLogger(__name__)

_ERROR_COUNTER_KEY = "_eb_consecutive_errors"
_MAX_CONSECUTIVE = 3


class ErrorBoundaryPlugin(BasePlugin):
    """Catches model and tool errors and returns safe fallback responses.

    Tracks consecutive errors per-session to prevent infinite fallback loops.
    """

    def __init__(self) -> None:
        super().__init__(name="error_boundary")

    async def on_model_error_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
        error: Exception,
    ) -> Optional[LlmResponse]:
        _logger.error(
            "Model error [plugin:%s]: %s\n%s",
            self.name, error, traceback.format_exc(),
        )
        self._increment(callback_context)

        if self._count(callback_context) >= _MAX_CONSECUTIVE:
            _logger.critical("Max consecutive model errors; propagating to layer 2")
            return None

        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(
                    text="I encountered a temporary error processing your request. "
                         "Please try again or rephrase your question."
                )],
            )
        )

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> Optional[dict[str, Any]]:
        _logger.error(
            "Tool error [plugin:%s] tool=%s: %s\n%s",
            self.name, getattr(tool, "name", str(tool)),
            error, traceback.format_exc(),
        )
        self._increment(tool_context)

        if self._count(tool_context) >= _MAX_CONSECUTIVE:
            _logger.critical("Max consecutive tool errors; propagating to layer 2")
            return None

        return {
            "status": "error",
            "error_type": type(error).__name__,
            "message": f"Tool encountered an error: {error}",
        }

    def _increment(self, ctx) -> None:
        if hasattr(ctx, "state") and isinstance(ctx.state, dict):
            ctx.state[_ERROR_COUNTER_KEY] = (
                ctx.state.get(_ERROR_COUNTER_KEY, 0) + 1
            )

    def _count(self, ctx) -> int:
        if hasattr(ctx, "state") and isinstance(ctx.state, dict):
            return int(ctx.state.get(_ERROR_COUNTER_KEY, 0))
        return 0
