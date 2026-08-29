"""Manual registration tools — PDF upload, local path registration, listing.

Available from BUILD stage onwards (registered as always-visible in get_tools_for_stage).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from google.adk.tools import ToolContext

from app.constants.artifact_names import REGISTRY_ARTIFACT
from app.constants.state_keys import (
    MANUAL_ARTIFACT_NAME,
    MANUAL_INGESTION_HISTORY,
    MANUAL_MIME_TYPE,
    MANUAL_SHA256,
    MANUAL_SOURCE,
)
from app.services.artifact_service import save_blob_artifact
from app.services.registry_service import append_manual_registry_entry, load_manual_registry

_logger = logging.getLogger(__name__)

_ARTIFACT_PREFIX = "session_manuals/"


async def _deduplicate_history(state: dict, sha256: str, entry: dict) -> None:
    """Append entry to MANUAL_INGESTION_HISTORY if sha256 not already present."""
    history: list[dict] = list(state.get(MANUAL_INGESTION_HISTORY, []))
    if not any(e.get("sha256") == sha256 for e in history):
        history.append(entry)
        state[MANUAL_INGESTION_HISTORY] = history


async def _deduplicate_registry(tool_context: ToolContext, sha256: str, registry_entry: dict) -> dict:
    """Append to manual registry if sha256 not already registered. Returns the entry."""
    existing_registry = await load_manual_registry(tool_context)
    already_registered = any(e.get("sha256") == sha256 for e in existing_registry)
    if not already_registered:
        await append_manual_registry_entry(tool_context, registry_entry)
        return registry_entry
    return next(e for e in existing_registry if e.get("sha256") == sha256)


async def register_manual_pdf(
    manual_path: str, instrument_name: str, tool_context: ToolContext,
) -> dict:
    """Register a local PDF manual for later ChemAutoAgent pipeline work.

    Validates the file, saves it as an ADK session artifact, records the entry
    in the user-scoped manual registry, and sets session.state metadata so
    downstream agents (e.g. ManualUnderstandingFlow) can load the PDF.

    Args:
        manual_path: Local filesystem path to the PDF manual.
        instrument_name: Short human-readable instrument name.

    Returns:
        A JSON-serializable result with the registry entry or a validation error.
    """
    source = Path(manual_path).expanduser().resolve()

    if not source.exists():
        return {"status": "error", "message": f"Manual file does not exist: {source}"}
    if source.suffix.lower() != ".pdf":
        return {"status": "error", "message": f"Only PDF manuals are supported in V1: {source.name}"}
    if not instrument_name.strip():
        return {"status": "error", "message": "instrument_name must not be empty."}

    manual_id = uuid4().hex
    pdf_data = source.read_bytes()
    sha256_hash = hashlib.sha256(pdf_data).hexdigest()
    mime_type = "application/pdf"

    artifact_name = f"{_ARTIFACT_PREFIX}{sha256_hash}.pdf"

    try:
        existing = await tool_context.load_artifact(artifact_name)
    except Exception:
        existing = None
    if existing is None:
        await save_blob_artifact(tool_context, artifact_name, pdf_data, mime_type)

    tool_context.state[MANUAL_ARTIFACT_NAME] = artifact_name
    tool_context.state[MANUAL_MIME_TYPE] = mime_type
    tool_context.state[MANUAL_SHA256] = sha256_hash
    tool_context.state[MANUAL_SOURCE] = "local_path"

    await _deduplicate_history(tool_context.state, sha256_hash, {
        "artifact_name": artifact_name, "mime_type": mime_type,
        "sha256": sha256_hash, "source": "local_path",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    })

    registry_entry = await _deduplicate_registry(tool_context, sha256_hash, {
        "manual_id": manual_id, "instrument_name": instrument_name.strip(),
        "source_path": str(source), "artifact_name": artifact_name,
        "sha256": sha256_hash, "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "registered",
    })

    return {
        "status": "success",
        "manual": registry_entry,
        "message": f"Manual registered. Artifact: {artifact_name}.",
    }


async def process_web_upload(instrument_name: str, tool_context: ToolContext) -> dict:
    """Process a PDF uploaded via the ADK web UI.

    When a user uploads a file through the web interface, SaveFilesAsArtifactsPlugin
    saves it as an artifact and replaces the file data with a text placeholder.
    This tool finds the uploaded PDF artifact, processes it, and registers it.

    Args:
        instrument_name: Short human-readable instrument name.

    Returns:
        A JSON-serializable result confirming the upload was processed.
    """
    if not instrument_name.strip():
        return {"status": "error", "message": "instrument_name must not be empty."}

    existing_artifact = tool_context.state.get(MANUAL_ARTIFACT_NAME)
    if existing_artifact:
        _logger.info("process_web_upload: using already-registered artifact %s", existing_artifact)
        return {
            "status": "success",
            "message": f"PDF already registered as {existing_artifact}. Ready to build.",
        }

    artifact_names = await tool_context.list_artifacts()
    pdf_artifact: str | None = None
    for name in artifact_names:
        if name.lower().endswith(".pdf") and not name.startswith("session_") and not name.startswith("user:"):
            pdf_artifact = name
            break

    if pdf_artifact is None:
        return {
            "status": "no_pdf",
            "message": (
                "No uploaded PDF found in this message. "
                "Please use the web UI upload button or drag-and-drop a PDF file, "
                "and include the instrument name in the same message. "
                "For example: 'IKA RCT Basic' + upload the PDF."
            ),
        }

    part = await tool_context.load_artifact(pdf_artifact)
    if part is None or part.inline_data is None:
        return {"status": "error", "message": f"Could not load uploaded artifact: {pdf_artifact}"}

    pdf_data = part.inline_data.data
    sha256_hash = hashlib.sha256(pdf_data).hexdigest()
    mime_type = part.inline_data.mime_type or "application/pdf"
    artifact_name = f"{_ARTIFACT_PREFIX}{sha256_hash}.pdf"

    try:
        existing = await tool_context.load_artifact(artifact_name)
    except Exception:
        existing = None
    if existing is None:
        await save_blob_artifact(tool_context, artifact_name, pdf_data, mime_type)

    tool_context.state[MANUAL_ARTIFACT_NAME] = artifact_name
    tool_context.state[MANUAL_MIME_TYPE] = mime_type
    tool_context.state[MANUAL_SHA256] = sha256_hash
    tool_context.state[MANUAL_SOURCE] = "web_upload"

    await _deduplicate_history(tool_context.state, sha256_hash, {
        "artifact_name": artifact_name, "mime_type": mime_type,
        "sha256": sha256_hash, "source": "web_upload",
        "original_filename": pdf_artifact,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    })

    registry_entry = await _deduplicate_registry(tool_context, sha256_hash, {
        "manual_id": uuid4().hex, "instrument_name": instrument_name.strip(),
        "source_path": f"web_upload:{pdf_artifact}", "artifact_name": artifact_name,
        "sha256": sha256_hash, "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "registered",
    })

    return {
        "status": "success",
        "manual": registry_entry,
        "message": f"PDF uploaded and registered ({len(pdf_data)} bytes).",
    }


async def list_registered_manuals(tool_context: ToolContext) -> dict:
    """List PDF manuals that have already been registered locally.

    Returns:
        A JSON-serializable result with the list of registered manuals.
    """
    rows = await load_manual_registry(tool_context)
    return {"status": "success", "count": len(rows), "manuals": rows}
