"""ManualUnderstandingFlow — PDF ingestion + PP-StructureV3 OCR + context assembly + parallel extraction."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)

from google.adk.agents import BaseAgent, ParallelAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types as genai_types

from app.constants.state_keys import (
    MANUAL_ARTIFACT_NAME,
    MANUAL_ASSEMBLED_CONTEXT,
    MANUAL_INGESTION_HISTORY,
    MANUAL_MIME_TYPE,
    MANUAL_SHA256,
    MANUAL_SOURCE,
)
from app.agents.build.flat_extractors import create_all_flat_extractors, create_flat_extractor
from app.schemas.flat_extraction import FLAT_AGENTS, FlatDeviceIdentity, FlatCommandTable, FlatSerialConnection, FlatFraming
from app.services.pdf_ocr_service import (
    create_pipeline,
    parse_document,
    read_markdown_texts,
)

_ARTIFACT_PREFIX = "session_manuals/"


def _find_pdf_part(events: list[Event], max_events: int = 20) -> genai_types.Part | None:
    """Scan the most recent events for a PDF Part."""
    for event in reversed(events[-max_events:]):
        content = event.content
        if content is None or not content.parts:
            continue
        for part in content.parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "mime_type", None) == "application/pdf":
                return part
    return None


async def _resolve_pdf(
    ctx: InvocationContext,
) -> tuple[genai_types.Part | None, str]:
    """Resolve a PDF Part for extraction.

    Returns:
        (pdf_part, source) — source is "conversation" or "state_reference".
    """
    pdf_part = _find_pdf_part(ctx.session.events)
    if pdf_part is not None:
        return pdf_part, "conversation"

    artifact_name = ctx.session.state.get(MANUAL_ARTIFACT_NAME)
    if artifact_name and ctx.artifact_service is not None:
        try:
            pdf_part = await ctx.artifact_service.load_artifact(
                app_name=ctx.app_name,
                user_id=ctx.user_id,
                session_id=ctx.session.id,
                filename=artifact_name,
            )
            if pdf_part is not None:
                return pdf_part, "state_reference"
        except Exception:
            pass

    # Fallback: search for uploaded PDF artifacts (SaveFilesAsArtifactsPlugin saves
    # web UI uploads with the original filename).  This handles the case where the
    # root agent transferred before calling process_web_upload.
    if ctx.artifact_service is not None:
        try:
            names = await ctx.artifact_service.list_artifact_keys(
                app_name=ctx.app_name,
                user_id=ctx.user_id,
                session_id=ctx.session.id,
            )
        except Exception:
            names = []
        for name in names:
            if not name.lower().endswith(".pdf"):
                continue
            # Skip session-prefixed names (already checked above)
            if name.startswith("session") or name.startswith("user:"):
                continue
            try:
                part = await ctx.artifact_service.load_artifact(
                    app_name=ctx.app_name,
                    user_id=ctx.user_id,
                    session_id=ctx.session.id,
                    filename=name,
                )
                if part is not None and getattr(part, "inline_data", None) is not None:
                    return part, "web_upload_artifact"
            except Exception:
                continue

    return None, ""


def create_manual_understanding_flow() -> ManualUnderstandingFlow:
    """Factory for ManualUnderstandingFlow."""
    return ManualUnderstandingFlow(
        name="manual_understanding_flow",
        description="Manual understanding: ingests PDFs, runs PP-StructureV3 OCR, assembles context, and extracts protocol/safety/function schemas in parallel.",
    )


# ── Baudrate parsing helper ──────────────────────────────────────────────────

def _parse_baudrate(raw: str) -> int:
    """Parse a baudrate value from flat extraction.

    Handles:
    - Single value: "9600" → 9600
    - Comma-separated options: "1200,2400,4800,9600" → 9600 (last value)
    - Options with suffix: "1200,2400,4800,9600可选" → 9600
    - Empty/missing: → 9600 (default)

    Rationale: when a manual lists multiple baud rate options, the last value
    is typically the highest / most commonly used default. Taking the first
    value (e.g. 1200 from "1200,2400,4800,9600") produces a baudrate mismatch
    that causes complete communication failure with the instrument.
    """
    import re as _re
    if not raw or not raw.strip():
        return 9600
    raw = raw.strip()
    if "," in raw:
        # Multiple options — take the last numeric value
        parts = raw.split(",")
        for part in reversed(parts):
            nums = _re.findall(r"\d+", part)
            if nums:
                return int(nums[0])
    # Single value (possibly with non-numeric suffix)
    nums = _re.findall(r"\d+", raw)
    return int(nums[0]) if nums else 9600


def _extract_cmd_section(context_text: str) -> str:
    """Extract sections related to commands/registers from OCR text.

    Returns the most relevant portion of the manual text for command table
    validation. Tries to find sections with keywords like "command", "register",
    "指令", "寄存器", "命令", table-like patterns, etc.
    """
    lines = context_text.split("\n")
    relevant = []
    capturing = False
    capture_count = 0
    max_lines = 200  # limit to avoid token overflow

    keywords = (
        "command", "register", "指令", "寄存器", "命令", "地址",
        "address", "function code", "功能码", "操作", "operation",
        "read", "write", "读", "写", "IN_", "OUT_", "0x",
    )

    for line in lines:
        stripped = line.strip()

        # Start capturing if we see a relevant header/keyword
        if any(kw.lower() in stripped.lower() for kw in keywords):
            capturing = True
            capture_count = 0

        if capturing:
            relevant.append(line)
            capture_count += 1

            # Stop after enough lines without new keywords
            if capture_count > 30 and not any(kw.lower() in stripped.lower() for kw in keywords):
                capturing = False
                capture_count = 0

        if len(relevant) >= max_lines:
            break

    # If we found relevant sections, return them
    if relevant:
        return "\n".join(relevant)

    # Fallback: return last portion of text (often has command tables at the end)
    return "\n".join(lines[-100:])


# ── Flat → legacy state key mapping ─────────────────────────────────────────

def _build_device_spec(state: dict) -> dict:
    """Build a unified device_spec from all flat extractions.

    This is the "ground truth" for all downstream phases:
    - Phase 2 (code generation): code writer reads device_spec
    - Phase 4 (repair): repair agent reads device_spec to understand correct protocol

    Each function includes raw_text from the OCR context so repair agents
    can consult the original manual text.
    """
    fd = state.get("flat_device", {}) or {}
    serial = state.get("flat_serial", {}) or {}
    network = state.get("flat_network", {}) or {}
    ff = state.get("flat_framing", {}) or {}
    timing = state.get("flat_timing", {}) or {}
    fct = state.get("flat_cmd_table", {}) or {}
    raw_table = fct.get("raw_table", "")

    # Parse functions deterministically from raw command table
    from app.agents.build.code_writer_agent import _parse_command_table_to_functions
    protocol_family = (fd.get("protocol_family") or "").strip().upper()
    parsed = _parse_command_table_to_functions(raw_table, protocol_family)
    functions = list(parsed.get("functions", {}).values())

    # Determine transport type
    has_serial = bool(serial.get("baudrate") or serial.get("parity"))
    has_network = bool(network.get("default_port") or network.get("protocol_hint") or network.get("host_hint"))
    if has_network and not has_serial:
        transport_type = "tcp"
    elif has_serial:
        transport_type = "serial"
    else:
        transport_type = "unknown"

    # Build connection spec
    connection: dict[str, Any] = {"type": transport_type, "transport_type": transport_type}
    if has_serial:
        connection.update({
            "baudrate": _parse_baudrate(serial.get("baudrate", "")),
            "databits": int(serial.get("databits") or 8),
            "parity": serial.get("parity", "N"),
            "stopbits": float(serial.get("stopbits") or 1.0),
            "flow_control": serial.get("flow_control", "none"),
        })
    if has_network:
        connection.update({
            "default_port": network.get("default_port", ""),
            "protocol_hint": network.get("protocol_hint", ""),
            "host_hint": network.get("host_hint", ""),
        })
    connection["timeout_ms"] = int(timing.get("response_timeout_ms") or "2000")

    return {
        "device": {
            "manufacturer": fd.get("manufacturer", ""),
            "model": fd.get("model", ""),
            "protocol_family": protocol_family,
        },
        "connection": connection,
        "protocol": {
            "encoding": ff.get("encoding", ""),
            "checksum_type": ff.get("checksum_type", ""),
            "header_hex": ff.get("header_hex", ""),
            "byte_order": ff.get("byte_order", ""),
            "line_ending": ff.get("line_ending", "\\r\\n"),
        },
        "raw_command_table": raw_table,
        "functions": functions,
        "assembled_context": state.get("manual_assembled_context", ""),
    }


class ManualUnderstandingFlow(BaseAgent):
    """CustomAgent: PDF → PP-StructureV3 → context → 3 parallel schema extractors.

    Stage 1a — Ingestion: resolve PDF from events or state, save artifact.
    Stage 1b — OCR: PP-StructureV3 → native markdown context.
    Stage 2  — Extraction: flat agents + legacy extractors consume markdown context in parallel.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        # ── Idempotency: skip if already processed ──────────────────
        if state.get(MANUAL_ASSEMBLED_CONTEXT):
            yield Event(
                author=self.name,
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(
                        text="Manual already processed — OCR context exists."
                    )],
                ),
            )
            return

        # ── Stage 1a: Ingestion ──────────────────────────────────────
        pdf_part, source = await _resolve_pdf(ctx)
        if pdf_part is None:
            yield Event(
                author=self.name,
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part.from_text(
                            text="No PDF manual found. "
                            "Upload a PDF via the web UI or register one first with "
                            "`register_manual_pdf /path/to/manual.pdf instrument_name`."
                        )
                    ],
                ),
            )
            return

        pdf_bytes = pdf_part.inline_data.data
        mime_type = pdf_part.inline_data.mime_type or "application/pdf"
        sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()
        artifact_name = f"{_ARTIFACT_PREFIX}{sha256_hash}.pdf"

        if source == "conversation":
            history: list[dict] = list(state.get(MANUAL_INGESTION_HISTORY, []))
            already_ingested = any(
                entry.get("sha256") == sha256_hash for entry in history
            )

            if not already_ingested:
                if ctx.artifact_service is None:
                    yield Event(
                        author=self.name,
                        content=genai_types.Content(
                            role="model",
                            parts=[genai_types.Part.from_text(
                                text="Artifact service is not available. Cannot save the PDF manual."
                            )],
                        ),
                    )
                    return

                await ctx.artifact_service.save_artifact(
                    app_name=ctx.app_name,
                    user_id=ctx.user_id,
                    session_id=ctx.session.id,
                    filename=artifact_name,
                    artifact=pdf_part,
                )

                entry = {
                    "artifact_name": artifact_name,
                    "mime_type": mime_type,
                    "sha256": sha256_hash,
                    "source": "adk_web_upload",
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                }
                history.append(entry)
                state[MANUAL_INGESTION_HISTORY] = history

            status_text = (
                f"PDF saved as `{artifact_name}` "
                f"(SHA256: {sha256_hash[:16]}…). "
            )
        else:
            status_text = (
                f"Using registered manual: `{artifact_name}` "
                f"(SHA256: {sha256_hash[:16]}…). "
            )

        state[MANUAL_ARTIFACT_NAME] = artifact_name
        state[MANUAL_MIME_TYPE] = mime_type
        state[MANUAL_SHA256] = sha256_hash
        state[MANUAL_SOURCE] = source

        yield Event(
            author=self.name,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part.from_text(text=status_text + "Running PP-StructureV3 document parsing…")],
            ),
        )

        # ── Stage 1b: PP-StructureV3 OCR + markdown ───────────────────
        try:
            pipeline = create_pipeline(device="gpu")
            results = parse_document(pdf_bytes, pipeline)
            context_text = read_markdown_texts(results)

            state[MANUAL_ASSEMBLED_CONTEXT] = context_text

            ocr_artifact_name = f"session_ocr/{sha256_hash}_context.md"
            if ctx.artifact_service is not None:
                try:
                    existing_ocr = await ctx.artifact_service.load_artifact(
                        app_name=ctx.app_name,
                        user_id=ctx.user_id,
                        session_id=ctx.session.id,
                        filename=ocr_artifact_name,
                    )
                except Exception:
                    existing_ocr = None
                if existing_ocr is None:
                    context_part = genai_types.Part.from_text(text=context_text)
                    await ctx.artifact_service.save_artifact(
                        app_name=ctx.app_name,
                        user_id=ctx.user_id,
                        session_id=ctx.session.id,
                        filename=ocr_artifact_name,
                        artifact=context_part,
                    )

            page_count = len(results)
            yield Event(
                author=self.name,
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(
                        text=f"PP-StructureV3 parsed {page_count} page(s). "
                        f"Markdown context: {len(context_text)} chars. "
                        "Starting parallel schema extraction…"
                    )],
                ),
                actions=EventActions(state_delta={
                    MANUAL_ASSEMBLED_CONTEXT: context_text,
                }),
            )
        except Exception as exc:
            yield Event(
                author=self.name,
                content=genai_types.Content(
                    role="model",
                    parts=[genai_types.Part.from_text(
                        text=f"PP-StructureV3 parsing failed: {exc}. "
                        "Please check that your PDF is valid."
                    )],
                ),
            )
            return

        # Release GPU memory — OCR is done, downstream stages are CPU + API.
        del pipeline
        import gc
        gc.collect()

        # ── Stage 2: Flat extraction (9 small agents, 2-6 flat fields each) ──
        flat_agents = create_all_flat_extractors(context_text)
        flat_extractors = ParallelAgent(
            name="flat_extraction",
            description="Parallel flat extraction: 9 small agents, 2-6 flat fields each.",
            sub_agents=flat_agents,
        )
        async for event in flat_extractors.run_async(ctx):
            yield event

        flat_keys = [cfg["key"] for cfg in FLAT_AGENTS]
        flat_present = [k for k in flat_keys if k in ctx.session.state]

        # ── Validate critical flat fields ────────────────────────────
        critical_checks: list[dict] = [
            {
                "key": "flat_device",
                "label": "protocol_family",
                "check": lambda s: (s.get("flat_device", {}) or {}).get("protocol_family", "").strip().upper() not in ("", "UNKNOWN"),
                "schema": FlatDeviceIdentity,
                "desc": next(cfg["desc"] for cfg in FLAT_AGENTS if cfg["key"] == "flat_device"),
            },
            {
                "key": "flat_cmd_table",
                "label": "raw_table",
                "check": lambda s: bool((s.get("flat_cmd_table", {}) or {}).get("raw_table", "").strip()),
                "schema": FlatCommandTable,
                "desc": next(cfg["desc"] for cfg in FLAT_AGENTS if cfg["key"] == "flat_cmd_table"),
            },
            {
                "key": "flat_serial",
                "label": "baudrate",
                "check": lambda s: bool((s.get("flat_serial", {}) or {}).get("baudrate", "").strip()),
                "schema": FlatSerialConnection,
                "desc": next(cfg["desc"] for cfg in FLAT_AGENTS if cfg["key"] == "flat_serial"),
            },
            {
                "key": "flat_framing",
                "label": "encoding",
                "check": lambda s: bool((s.get("flat_framing", {}) or {}).get("encoding", "").strip()),
                "schema": FlatFraming,
                "desc": next(cfg["desc"] for cfg in FLAT_AGENTS if cfg["key"] == "flat_framing"),
            },
        ]

        max_retries = 3
        for check in critical_checks:
            for attempt in range(max_retries):
                if check["check"](ctx.session.state):
                    break
                _logger.warning(
                    "Critical flat field %s/%s is empty — retrying (%d/%d)",
                    check["key"], check["label"], attempt + 1, max_retries,
                )
                retry_agent = create_flat_extractor(
                    context_text=context_text,
                    state_key=check["key"],
                    schema_class=check["schema"],
                    description=check["desc"] + (
                        f" (RETRY {attempt + 1}/{max_retries}: previous extraction was empty or invalid. "
                        "Look more carefully at the manual context above.)"
                    ),
                )
                async for event in retry_agent.run_async(ctx):
                    pass

        # ── Deep validation: command table ────────────────────────────
        fct = ctx.session.state.get("flat_cmd_table", {}) or {}
        raw_table = fct.get("raw_table", "")
        fd = ctx.session.state.get("flat_device", {}) or {}
        protocol_family = (fd.get("protocol_family") or "UNKNOWN").strip().upper()

        if raw_table.strip():
            from app.codegen.cmd_table_validator import validate_cmd_table, build_llm_validation_prompt
            cmd_result = validate_cmd_table(raw_table, protocol_family, context_text)

            if not cmd_result.ok:
                _logger.warning(
                    "cmd_table validation failed: %d issues, %d warnings",
                    len(cmd_result.issues), len(cmd_result.warnings),
                )
                for issue in cmd_result.issues:
                    _logger.warning("  issue: %s", issue)

                # Layer 3: LLM cross-validation (max 2 attempts)
                if cmd_result.needs_llm_fix:
                    for fix_attempt in range(2):
                        _logger.info("cmd_table LLM fix attempt %d/2", fix_attempt + 1)

                        # Extract relevant section from OCR text
                        relevant_section = _extract_cmd_section(context_text)

                        fix_prompt = build_llm_validation_prompt(
                            raw_table=raw_table,
                            issues=cmd_result.issues,
                            relevant_section=relevant_section,
                        )

                        # Run a single LLM call to fix the raw_table
                        fix_agent = create_flat_extractor(
                            context_text=fix_prompt,
                            state_key="flat_cmd_table",
                            schema_class=FlatCommandTable,
                            description="Fix the command table based on validation issues.",
                        )
                        async for event in fix_agent.run_async(ctx):
                            pass

                        # Re-validate
                        fct = ctx.session.state.get("flat_cmd_table", {}) or {}
                        raw_table = fct.get("raw_table", "")
                        if raw_table.strip():
                            cmd_result = validate_cmd_table(raw_table, protocol_family, context_text)
                            if cmd_result.ok:
                                _logger.info("cmd_table fixed after %d LLM attempts", fix_attempt + 1)
                                break

                    if not cmd_result.ok:
                        _logger.warning("cmd_table still has issues after LLM fix attempts")
            else:
                _logger.info(
                    "cmd_table validation passed: %d functions, %d warnings",
                    len(cmd_result.functions), len(cmd_result.warnings),
                )
                _logger.info("cmd_table validation passed: %d functions stored", len(cmd_result.functions))
        else:
            _logger.warning("cmd_table is empty, skipping deep validation")

        # ── Build validation summary ──────────────────────────────────
        validation_issues: list[str] = []
        fd = ctx.session.state.get("flat_device", {}) or {}
        if fd.get("protocol_family", "").strip().upper() in ("", "UNKNOWN"):
            validation_issues.append("protocol_family missing or UNKNOWN")
        fct = ctx.session.state.get("flat_cmd_table", {}) or {}
        if not fct.get("raw_table", "").strip():
            validation_issues.append("command table (raw_table) is empty")
        fs = ctx.session.state.get("flat_serial", {}) or {}
        if not fs.get("baudrate", "").strip():
            validation_issues.append("serial baudrate is empty")
        ff = ctx.session.state.get("flat_framing", {}) or {}
        if not ff.get("encoding", "").strip():
            validation_issues.append("framing encoding is empty")
        validation_status = "PASSED" if not validation_issues else f"ISSUES: {'; '.join(validation_issues)}"

        yield Event(
            author=self.name,
            content=genai_types.Content(
                role="model",
                parts=[
                    genai_types.Part.from_text(
                        text=f"Flat extraction: {len(flat_present)}/{len(flat_keys)} succeeded. "
                        f"Missing: {[k for k in flat_keys if k not in ctx.session.state]}. "
                        f"Critical validation: {validation_status}."
                    )
                ],
            ),
        )

        # ── Build device_spec (unified ground truth) ──────────────
        device_spec = _build_device_spec(ctx.session.state)
        # Write directly to session state so downstream tools can read
        # device_spec in the SAME turn (append_event only persists to storage).
        ctx.session.state["device_spec"] = device_spec

        # ── Persist flat keys + device_spec for downstream invocations ──
        flat_delta: dict = {"device_spec": device_spec}
        for key in flat_keys:
            val = ctx.session.state.get(key)
            if val is not None:
                flat_delta[key] = val
        await ctx.session_service.append_event(
            session=ctx.session,
            event=Event(author=self.name, actions=EventActions(state_delta=flat_delta)),
        )

        # ── Save device_spec as artifact for cross-invocation access ──
        import json as _json
        spec_json = _json.dumps(device_spec, ensure_ascii=False, indent=2)
        spec_part = genai_types.Part.from_text(text=spec_json)
        try:
            await ctx.artifact_service.save_artifact(
                app_name=ctx.app_name, user_id=ctx.user_id,
                session_id=ctx.session.id,
                filename=f"session_specs/{sha256_hash}_device_spec.json",
                artifact=spec_part,
            )
        except Exception:
            pass
