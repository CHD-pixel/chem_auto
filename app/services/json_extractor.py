"""Shared after_model_callback factory for JSON extraction + Pydantic validation.

Replaces ADK's built-in output_schema mechanism, which sends
``response_format: json_schema`` — unsupported by DeepSeek V4.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse

_logger = logging.getLogger(__name__)


def _example_value(field: FieldInfo, field_name: str) -> Any:
    """Generate a plausible example value for a Pydantic field."""
    from pydantic.fields import PydanticUndefined

    anno = field.annotation
    default = field.default
    if default is not None and default is not PydanticUndefined:
        if isinstance(default, (str, int, float, bool)) and default not in ("", 0, 0.0):
            return default
        if isinstance(default, list) and default:
            return []
        if isinstance(default, dict) and default:
            return {}

    origin = getattr(anno, "__origin__", None)
    is_list_origin = origin is list
    if origin is not None:
        args = getattr(anno, "__args__", ())
        for a in args:
            if a is not type(None):
                anno = a
                break

    if anno is str:
        desc = field.description or ""
        if desc and "e.g." in desc:
            eg = desc.split("e.g.")[-1].strip().rstrip(".")
            eg = eg.strip("'\"")
            if eg and len(eg) < 30:
                return eg
        return field_name
    elif anno is int:
        return 0
    elif anno is float:
        return 0.0
    elif anno is bool:
        return False
    elif anno is list:
        return []
    elif anno is dict:
        return {}
    # For nested Pydantic models, return empty dict
    if isinstance(anno, type) and issubclass(anno, BaseModel):
        inner = {n: _example_value(f, n) for n, f in anno.model_fields.items()}
        # list[Model] → wrap in a one-element list
        if is_list_origin:
            return [inner]
        return inner
    return ""


def generate_json_example(schema_class: type[BaseModel]) -> str:
    """Generate a JSON example dict from a Pydantic model's fields and defaults."""
    example: dict[str, Any] = {}
    for name, field in schema_class.model_fields.items():
        example[name] = _example_value(field, name)
    return json.dumps(example, ensure_ascii=False, indent=2)


def inject_json_example_into_instruction(instruction: str, schema_class: type[BaseModel]) -> str:
    """Append a JSON format example to an instruction string."""
    example = generate_json_example(schema_class)
    return (
        f"{instruction}\n\n"
        f"You MUST output exactly one JSON object matching this structure:\n"
        f"```json\n{example}\n```\n"
        f"Do NOT wrap in markdown. Do NOT add explanation. JSON only."
    )


def _format_validation_error(exc: ValidationError) -> str:
    """Format a Pydantic ValidationError with field paths and error types."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) if err["loc"] else "(root)"
        msg = err.get("msg", "unknown")
        etype = err.get("type", "?")
        inp = repr(err.get("input", ""))
        if len(inp) > 120:
            inp = inp[:120] + "..."
        parts.append(f"  {loc}: {msg} [type={etype}, input={inp}]")
    return "\n".join(parts)


def _extract_balanced_json(text: str) -> str | None:
    """Find the first balanced ``{ ... }`` span in *text*."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _fix_control_chars(text: str) -> str:
    """Fix unescaped control characters inside JSON strings.

    LLMs often output raw newlines/tabs inside JSON string values instead
    of \\n / \\t escape sequences.  This breaks json.loads().  We fix by
    replacing control chars that appear inside quoted strings.
    """
    result: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == "\\" and in_string:
            result.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch in ("\n", "\r", "\t"):
            # Escape the control character
            if ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            continue
        result.append(ch)
    return "".join(result)


def make_json_extractor(schema_class, state_key: str):
    """Return an after_model_callback that extracts JSON and writes to state."""

    async def _extract(
        callback_context: CallbackContext, llm_response: LlmResponse,
    ) -> None:
        text = ""
        if llm_response.content and llm_response.content.parts:
            for part in llm_response.content.parts:
                if getattr(part, "text", None):
                    text += part.text

        if not text.strip():
            return None

        data = None

        # Strategy 1: raw parse (works when LLM outputs pure JSON)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            _logger.debug(
                "make_json_extractor(%s): plain json.loads failed: %s (pos %d)",
                state_key, exc, exc.pos,
            )

        # Strategy 2: balanced brace extraction (most robust — handles nested
        # code fences, triple-quoted strings, and other content that confuses
        # regex-based fence stripping).
        if data is None:
            candidate = _extract_balanced_json(text)
            if candidate is not None:
                try:
                    data = json.loads(candidate)
                except json.JSONDecodeError:
                    # Try lenient parse: fix unescaped control characters
                    # (LLMs often output raw newlines/tabs inside JSON strings)
                    try:
                        data = json.loads(_fix_control_chars(candidate))
                    except json.JSONDecodeError as exc:
                        _logger.debug(
                            "make_json_extractor(%s): balanced brace found but invalid JSON: %s "
                            "(at pos %d, candidate len=%d)",
                            state_key, exc, exc.pos, len(candidate),
                        )

        # Strategy 3: strip markdown fences (fallback — can fail when code
        # contains triple-quoted strings that look like fence boundaries).
        if data is None:
            stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
            stripped = re.sub(r"\n?```\s*$", "", stripped)
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                # Fallback: regex fence extraction — use LAST closing fence
                # to avoid matching triple-quoted strings inside code
                match = re.search(r"```(?:json)?\s*\n?(.*)\n?\s*```\s*$", text, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                    except json.JSONDecodeError:
                        try:
                            data = json.loads(_fix_control_chars(match.group(1)))
                        except json.JSONDecodeError as exc:
                            _logger.debug(
                                "make_json_extractor(%s): fenced json.loads failed: %s (pos %d)",
                                state_key, exc, exc.pos,
                            )

        if data is None:
            _logger.warning(
                "make_json_extractor(%s): no valid JSON found in response (len=%d, preview=%r)",
                state_key,
                len(text),
                text[:300],
            )
            return None

        try:
            validated = schema_class.model_validate(data)
            callback_context.state[state_key] = validated.model_dump()
        except ValidationError as exc:
            # Fallback: if the schema has a single required dict field and
            # the data doesn't have it, try wrapping.  This handles the common
            # LLM mistake of outputting the inner dict directly instead of the
            # expected {"field_name": inner_dict} wrapper.
            _fields = schema_class.model_fields
            _required_dict_fields = [
                k for k, v in _fields.items()
                if v.is_required() and getattr(v.annotation, "__origin__", None) is dict
            ]
            if len(_required_dict_fields) == 1 and isinstance(data, dict):
                _wrapper_key = _required_dict_fields[0]
                if _wrapper_key not in data:
                    try:
                        validated = schema_class.model_validate({_wrapper_key: data})
                        callback_context.state[state_key] = validated.model_dump()
                        _logger.info(
                            "make_json_extractor(%s): auto-wrapped data in {%s: ...}",
                            state_key, _wrapper_key,
                        )
                        return None
                    except ValidationError:
                        pass  # fall through to original error
            _logger.warning(
                "make_json_extractor(%s): Pydantic validation failed (%d errors):\n%s",
                state_key,
                exc.error_count(),
                _format_validation_error(exc),
            )
            return None
        except Exception as exc:
            _logger.warning(
                "make_json_extractor(%s): unexpected error: %s",
                state_key,
                exc,
            )
            return None

        return None

    return _extract
