"""File filter callback — strips non-text Parts from LLM input.

DeepSeek and other text-only models reject non-text parts.
This callback replaces them with text placeholders so the LLM can still
see that a file was uploaded. PDF data is saved as an artifact so
process_web_upload() can find it later.
"""

from __future__ import annotations

import hashlib
import logging

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.constants.state_keys import (
    MANUAL_ARTIFACT_NAME,
    MANUAL_MIME_TYPE,
    MANUAL_SHA256,
    MANUAL_SOURCE,
)

_logger = logging.getLogger(__name__)


def _is_file_part(part: types.Part) -> bool:
    """Check if a part is a non-text file/media part that text models can't handle."""
    if getattr(part, "inline_data", None) is not None:
        return True
    if getattr(part, "file_data", None) is not None:
        return True
    return False


def _part_label(part: types.Part) -> str:
    """Get a human-readable label for a file part."""
    inline = getattr(part, "inline_data", None)
    if inline is not None:
        return getattr(inline, "display_name", None) or getattr(inline, "mime_type", "file")
    file_data = getattr(part, "file_data", None)
    if file_data is not None:
        return getattr(file_data, "display_name", None) or getattr(file_data, "mime_type", "file")
    return "file"


async def filter_file_parts(
    callback_context: CallbackContext, llm_request: LlmRequest,
) -> LlmResponse | None:
    """before_model_callback: strip non-text Parts, save PDFs for process_web_upload."""
    has_file_parts = False

    for content in llm_request.contents:
        if not content.parts:
            continue

        new_parts = []
        for part in content.parts:
            if not _is_file_part(part):
                new_parts.append(part)
                continue

            # Save PDF inline_data so process_web_upload() can find it later
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "mime_type", None) == "application/pdf":
                try:
                    pdf_data = inline.data
                    sha = hashlib.sha256(pdf_data).hexdigest()
                    artifact_name = f"session_manuals/{sha}.pdf"
                    await callback_context.save_artifact(artifact_name, types.Part(
                        inline_data=types.Blob(mime_type="application/pdf", data=pdf_data),
                    ))
                    callback_context.state[MANUAL_ARTIFACT_NAME] = artifact_name
                    callback_context.state[MANUAL_MIME_TYPE] = "application/pdf"
                    callback_context.state[MANUAL_SHA256] = sha
                    callback_context.state[MANUAL_SOURCE] = "web_upload"
                except Exception as exc:
                    _logger.warning("filter_file_parts: failed to save PDF artifact: %s", exc)

            # Replace non-text part with text placeholder
            label = _part_label(part)
            new_parts.append(types.Part.from_text(text=f'[Uploaded file: "{label}"]'))
            has_file_parts = True

        if has_file_parts:
            content.parts.clear()
            content.parts.extend(new_parts)

    if has_file_parts:
        _logger.info("filter_file_parts: stripped non-text parts from LLM request")

    return None
