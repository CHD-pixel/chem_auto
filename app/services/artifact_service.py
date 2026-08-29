from __future__ import annotations

import json
from typing import Any

from google.genai import types


def make_text_part(text: str) -> types.Part:
    return types.Part(text=text)


def make_json_part(payload: Any) -> types.Part:
    return types.Part(text=json.dumps(payload, ensure_ascii=True, indent=2))


def make_blob_part(data: bytes, mime_type: str) -> types.Part:
    return types.Part(inline_data=types.Blob(mime_type=mime_type, data=data))


def build_artifact_summary(
    artifact_name: str,
    summary: str,
    *,
    version: int | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_name": artifact_name,
        "summary": summary,
    }
    if version is not None:
        payload["version"] = version
    if mime_type is not None:
        payload["mime_type"] = mime_type
    return payload


async def save_text_artifact(context: Any, artifact_name: str, text: str) -> int:
    return await context.save_artifact(artifact_name, make_text_part(text))


async def save_json_artifact(context: Any, artifact_name: str, payload: Any) -> int:
    return await context.save_artifact(artifact_name, make_json_part(payload))


async def save_blob_artifact(
    context: Any, artifact_name: str, data: bytes, mime_type: str
) -> int:
    return await context.save_artifact(artifact_name, make_blob_part(data, mime_type))


async def load_text_artifact(context: Any, artifact_name: str) -> str | None:
    part = await context.load_artifact(artifact_name)
    if part is None:
        return None
    text = getattr(part, "text", None)
    if text is not None:
        return text
    inline_data = getattr(part, "inline_data", None)
    if inline_data is not None and getattr(inline_data, "data", None) is not None:
        return inline_data.data.decode("utf-8", errors="replace")
    return None


async def load_json_artifact(context: Any, artifact_name: str, default: Any) -> Any:
    text = await load_text_artifact(context, artifact_name)
    if text is None:
        return default
    return json.loads(text)


async def load_blob_artifact(context: Any, artifact_name: str) -> bytes | None:
    part = await context.load_artifact(artifact_name)
    if part is None:
        return None
    inline_data = getattr(part, "inline_data", None)
    if inline_data is not None and getattr(inline_data, "data", None) is not None:
        return inline_data.data
    text = getattr(part, "text", None)
    if text is not None:
        return text.encode("utf-8")
    return None
