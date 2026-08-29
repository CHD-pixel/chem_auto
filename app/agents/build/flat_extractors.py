"""Flat extraction agents — each agent has 2-8 flat fields, no nesting.

15-20 small agents replace the 6 deeply nested extraction agents.
All run in parallel via ParallelAgent. Each uses a simple flat Pydantic schema
for maximum LLM extraction accuracy.
"""

from __future__ import annotations

from collections.abc import Callable

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from app.llm.client_factory import build_llm
from app.runtime.config import MULTIMODAL_MODEL
from app.schemas.flat_extraction import FLAT_AGENTS
from app.services.json_extractor import inject_json_example_into_instruction, make_json_extractor


def _make_context_injector(context_text: str) -> Callable:
    """Inject OCR context before the LLM call. Also strips non-text parts."""

    async def _inject(
        callback_context: CallbackContext, llm_request: LlmRequest,
    ) -> LlmResponse | None:
        # Strip non-text parts (PDF inline_data, file_data) that text-only models reject
        from app.callbacks.file_filter import filter_file_parts
        await filter_file_parts(callback_context, llm_request)

        llm_request.contents.insert(
            0,
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(
                    text=(
                        "The following is OCR-extracted text from an instrument manual.\n"
                        "Extract the information described in your instructions.\n\n"
                        "=== MANUAL CONTEXT ===\n" + context_text
                    ),
                )],
            ),
        )
        return None

    return _inject


def create_flat_extractor(context_text: str, state_key: str,
                          schema_class, description: str) -> Agent:
    """Create a single flat-extraction LlmAgent.

    Args:
        context_text: Assembled OCR context.
        state_key: session.state key for the output.
        schema_class: Flat Pydantic model (3-8 fields, no nesting).
        description: One-line description of what to extract.
    """
    is_cmd_table = (state_key == "flat_cmd_table")
    max_tokens = 8192 if is_cmd_table else 4096
    instruction = inject_json_example_into_instruction(
        f"{description}\n\n"
        "Output a flat JSON object with only the fields described above. "
        "Do NOT nest objects. Do NOT add extra fields. "
        "If information is not found, use the default value or leave empty as instructed above.",
        schema_class,
    )
    return Agent(
        name=f"flat_{state_key}",
        model=build_llm(MULTIMODAL_MODEL),
        description=description,
        instruction=instruction,
        before_model_callback=_make_context_injector(context_text),
        after_model_callback=make_json_extractor(schema_class, state_key),
        generate_content_config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=max_tokens,
        ),
    )


def create_all_flat_extractors(context_text: str) -> list[Agent]:
    """Create all 6 flat extraction agents."""
    agents: list[Agent] = []
    for cfg in FLAT_AGENTS:
        agent = create_flat_extractor(
            context_text=context_text,
            state_key=cfg["key"],
            schema_class=cfg["schema"],
            description=cfg["desc"],
        )
        agents.append(agent)
    return agents
