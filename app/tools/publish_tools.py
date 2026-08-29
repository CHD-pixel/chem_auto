"""PUBLISH-stage tools — deterministic driver publishing.

No embedded LLM calls. Saves tested driver to cross-session registry.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone

from google.adk.tools import ToolContext
from google.genai import types as genai_types

from app.constants.artifact_names import (
    REGISTRY_ARTIFACT,
    published_build_blueprint_artifact,
    published_driver_artifact,
    published_manifest_artifact,
)
from app.constants.state_keys import (
    ACTIVE_DEVICE,
    CURRENT_CANDIDATE_CODE,
    PUBLISH_MANIFEST,
    TEST_STATUS,
)
from app.schemas.publish_manifest import PublishManifest, PublishedFunctionInfo
from app.schemas.registry_schema import RegistryDeviceEntry, RegistrySchema
from app.tools._helpers import get_invocation_context


def _error_result(exc: Exception) -> dict:
    return {
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc()[-500:],
    }


def _determine_risk_level(side_effect_level: str, function_category: str, user_confirmation: bool) -> str:
    if side_effect_level in ("high",):
        return "critical"
    if side_effect_level in ("medium",):
        return "high"
    if function_category in ("control", "setup", "cleanup", "safety"):
        return "medium"
    if user_confirmation:
        return "medium"
    return "low"


def _next_version(existing: dict | None) -> str:
    if existing is None:
        return "0.1.0"
    latest = existing.get("latest_version", "0.1.0")
    parts = latest.split(".")
    if len(parts) == 3:
        try:
            patch = int(parts[2]) + 1
            return f"{parts[0]}.{parts[1]}.{patch}"
        except (ValueError, TypeError):
            pass
    return "0.1.0"


async def publish_current_driver(tool_context: ToolContext) -> dict:
    """Publish the tested driver as a cross-session reusable asset.

    Saves driver code, manifest, safety schema, function catalog, and
    build blueprint to the user-scoped registry.

    Requires test_status == 'passed'.
    """
    try:
        state = tool_context.state
        ctx = get_invocation_context(tool_context)

        # Guards
        test_status = state.get(TEST_STATUS, "")
        if test_status != "passed":
            return {"status": "blocked", "reason": f"test_status is '{test_status}', must be 'passed'."}

        device_id = state.get(ACTIVE_DEVICE, "")
        if not device_id:
            return {"status": "error", "message": "No active_device in session state."}

        candidate_artifact_name = state.get(CURRENT_CANDIDATE_CODE, "")
        if not candidate_artifact_name:
            return {"status": "error", "message": "No candidate code artifact found."}

        # Load candidate code
        from app.agents.shared.artifact_io import load_text_artifact
        candidate_code = await load_text_artifact(ctx, candidate_artifact_name)
        if not candidate_code:
            return {"status": "error", "message": f"Cannot load artifact: {candidate_artifact_name}"}

        # Build blueprint from device_spec
        device_spec = state.get("device_spec", {})
        if not device_spec:
            return {"status": "error", "message": "No device_spec found. Run build_agent first."}
        from app.tools._blueprint_utils import device_spec_to_blueprint
        bp_model = device_spec_to_blueprint(device_spec)
        blueprint = bp_model.model_dump()

        ds_functions = device_spec.get("functions", [])
        action_methods = {f["function_name"]: f for f in ds_functions if f.get("function_name")}
        instrument_type = bp_model.instrument_type
        protocol_layer = bp_model.protocol_layer
        device_name = instrument_type or device_id.replace("_", " ").title()

        # Version
        from app.services.registry_service import load_device_registry
        registry = await load_device_registry(tool_context)
        existing_entry = registry.devices.get(device_id)
        version = _next_version(existing_entry.model_dump() if existing_entry else None)

        # Save artifacts
        driver_artifact = published_driver_artifact(device_id, version)
        await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session.id,
            filename=driver_artifact, artifact=genai_types.Part.from_text(text=candidate_code),
        )

        bb_artifact = published_build_blueprint_artifact(device_id, version)
        await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session.id,
            filename=bb_artifact,
            artifact=genai_types.Part.from_text(text=json.dumps(blueprint, ensure_ascii=False, indent=2)),
        )

        # Assemble manifest
        available_functions: dict[str, PublishedFunctionInfo] = {}
        for func_name, func_data in action_methods.items():
            if not isinstance(func_data, dict):
                continue
            side_effect = func_data.get("side_effect_level", "none")
            func_cat = func_data.get("function_category", "unknown")
            user_conf = bool(func_data.get("user_confirmation_required_after_call", False))
            available_functions[func_name] = PublishedFunctionInfo(
                function_name=func_name,
                signature=func_data.get("signature", f"{func_name}()"),
                function_category=func_cat,
                side_effect_level=side_effect,
                risk_level=_determine_risk_level(side_effect, func_cat, user_conf),
                requires_guardrail=bool(
                    func_data.get("parameter_guard_required", False)
                    or func_data.get("call_sequence_guard_required", False)
                ),
                requires_user_confirmation_after_call=user_conf,
                description=func_data.get("purpose"),
            )

        now = datetime.now(timezone.utc).isoformat()
        manifest = PublishManifest(
            manifest_version="0.1.0",
            device_id=device_id,
            device_name=device_name,
            instrument_type=instrument_type,
            protocol_layer=protocol_layer,
            driver_version=version,
            driver_artifact_name=driver_artifact,
            safety_artifact_name="",
            function_catalog_artifact_name="",
            build_blueprint_artifact_name=bb_artifact,
            available_functions=available_functions,
            published_at=now,
            test_status="passed",
            is_active=True,
        )

        manifest_artifact = published_manifest_artifact(device_id, version)
        await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session.id,
            filename=manifest_artifact,
            artifact=genai_types.Part.from_text(text=json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)),
        )

        # Update registry
        func_names = sorted(available_functions.keys())
        registry_entry = RegistryDeviceEntry(
            device_id=device_id,
            device_name=device_name,
            instrument_type=instrument_type,
            protocol_layer=protocol_layer,
            latest_version=version,
            available_versions=[version],
            active_manifest_artifact_name=manifest_artifact,
            active_driver_artifact_name=driver_artifact,
            active_safety_artifact_name="",
            available_functions=func_names,
            status="active",
        )
        if existing_entry is not None:
            merged_versions = set(existing_entry.available_versions)
            merged_versions.add(existing_entry.latest_version)
            merged_versions.add(version)
            registry_entry.available_versions = sorted(merged_versions)
            if existing_entry.created_at:
                registry_entry.created_at = existing_entry.created_at
            else:
                registry_entry.created_at = now
        else:
            registry_entry.created_at = now
        registry_entry.updated_at = now

        registry.devices[device_id] = registry_entry
        registry.updated_at = now
        await ctx.artifact_service.save_artifact(
            app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session.id,
            filename=REGISTRY_ARTIFACT,
            artifact=genai_types.Part.from_text(text=json.dumps(registry.model_dump(), ensure_ascii=False, indent=2)),
        )

        # Persist state
        state[PUBLISH_MANIFEST] = manifest.model_dump()

        return {
            "status": "completed",
            "published": True,
            "device_id": device_id,
            "device_name": device_name,
            "version": version,
            "function_count": len(func_names),
            "functions": func_names,
            "message": f"Driver published. device_id={device_id}, version={version}, {len(func_names)} functions.",
        }

    except Exception as exc:
        return _error_result(exc)
