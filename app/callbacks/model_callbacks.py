"""Model response callbacks — sanitizes LLM output before returning to user.

Extracted from agent.py to reduce file size and clarify ownership.
"""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents.shared.response_cleaner import strip_reasoning_preamble, looks_like_reasoning_preamble


async def sanitize_model_response(
    callback_context: CallbackContext, llm_response: LlmResponse,
) -> LlmResponse | None:
    """after_model_callback: strip reasoning preambles from LLM output."""
    del callback_context

    content = llm_response.content
    if not content or not content.parts:
        return None

    changed = False
    updated_parts: list[types.Part] = []
    seen_user_facing_text = False
    for part in content.parts:
        if getattr(part, "text", None):
            original_text = part.text
            cleaned_text = strip_reasoning_preamble(original_text)

            if not seen_user_facing_text and looks_like_reasoning_preamble(cleaned_text):
                changed = True
                continue

            if cleaned_text.strip():
                seen_user_facing_text = True

            if cleaned_text != part.text:
                changed = True
                updated_parts.append(types.Part.from_text(text=cleaned_text))
                continue
        updated_parts.append(part)

    if not updated_parts:
        return None

    if not changed:
        return None

    llm_response.content = types.Content(role=content.role, parts=updated_parts)
    return llm_response
