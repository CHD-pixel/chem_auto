"""Tools for the InvokePipeline — device discovery and function execution."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

# Per-session driver cache: module-level dict keyed by session_key -> {cache_key -> driver_instance}.
# NOT stored in tool_context.state because driver objects are not JSON-serializable
# and ADK's session service would crash trying to persist them to SQLite.
_MODULE_IMPORT_LOCK = asyncio.Lock()
_MODULE_NAME_LOCKS: dict[str, asyncio.Lock] = {}
_DRIVER_CACHES: dict[str, dict[str, Any]] = {}


def _get_session_key(tool_context: ToolContext) -> str:
    """Get or create a unique session key for cache isolation."""
    key = "_driver_session_key"
    if key not in tool_context.state:
        tool_context.state[key] = uuid.uuid4().hex[:12]
    return tool_context.state[key]


def _get_driver_cache(tool_context: ToolContext) -> dict[str, Any]:
    """Get the per-session driver cache (module-level, non-serializable)."""
    session_key = _get_session_key(tool_context)
    if session_key not in _DRIVER_CACHES:
        _DRIVER_CACHES[session_key] = {}
    return _DRIVER_CACHES[session_key]

from google.adk.agents.invocation_context import InvocationContext
from google.adk.tools import ToolContext
from google.genai import types as genai_types

from app.constants.artifact_names import (
    REGISTRY_ARTIFACT,
    published_build_blueprint_artifact,
    published_driver_artifact,
    published_manifest_artifact,
    published_safety_artifact,
)
from app.constants.state_keys import (
    ACTIVE_DEVICE,
    CALL_HISTORY,
    INVOKE_REQUEST,
)
from app.schemas.publish_manifest import PublishManifest
from app.schemas.registry_schema import RegistrySchema


async def list_published_devices(tool_context: ToolContext) -> dict:
    """List all published (active) devices from the device registry.

    Returns:
        dict with 'devices' list containing device_id, device_name, instrument_type,
        available_functions, and latest_version for each active device.
    """
    try:
        existing = await tool_context.load_artifact(REGISTRY_ARTIFACT)
    except Exception:
        return {"status": "success", "devices": [], "note": "Registry not found."}

    if existing is None:
        return {"status": "success", "devices": []}

    text = getattr(existing, "text", None)
    if text is None:
        inline = getattr(existing, "inline_data", None)
        if inline is not None and getattr(inline, "data", None) is not None:
            text = inline.data.decode("utf-8", errors="replace")

    if text is None:
        return {"status": "success", "devices": []}

    import json
    try:
        registry = RegistrySchema(**json.loads(text))
    except Exception:
        return {"status": "success", "devices": []}

    active = [
        {
            "device_id": e.device_id,
            "device_name": e.device_name,
            "instrument_type": e.instrument_type,
            "protocol_layer": e.protocol_layer,
            "available_functions": e.available_functions,
            "latest_version": e.latest_version,
        }
        for e in registry.devices.values()
        if e.status == "active"
    ]
    return {"status": "success", "devices": active}


async def _load_registry_and_manifest(tool_context: ToolContext, device_id: str) -> tuple[dict, "PublishManifest"] | tuple[dict, None]:
    """Load registry entry and manifest for a device. Returns (error_dict, None) or ({}, manifest)."""
    import json as _json

    try:
        existing = await tool_context.load_artifact(REGISTRY_ARTIFACT)
    except Exception:
        return {"status": "error", "message": "Registry not found."}, None

    if existing is None:
        return {"status": "error", "message": f"Device '{device_id}' not found in registry."}, None

    text = _artifact_text(existing)
    if text is None:
        return {"status": "error", "message": "Registry is empty."}, None

    try:
        registry = RegistrySchema(**_json.loads(text))
    except Exception:
        return {"status": "error", "message": "Registry is corrupted."}, None

    entry = registry.devices.get(device_id)
    if entry is None:
        return {"status": "error", "message": f"Device '{device_id}' not found."}, None

    manifest_artifact = entry.active_manifest_artifact_name
    try:
        manifest_part = await tool_context.load_artifact(manifest_artifact)
    except Exception:
        return {"status": "error", "message": f"Manifest not found for '{device_id}'."}, None

    if manifest_part is None:
        return {"status": "error", "message": f"Manifest artifact missing for '{device_id}'."}, None

    manifest_text = _artifact_text(manifest_part)
    if manifest_text is None:
        return {"status": "error", "message": f"Manifest is empty for '{device_id}'."}, None

    try:
        manifest = PublishManifest(**_json.loads(manifest_text))
    except Exception:
        return {"status": "error", "message": f"Manifest is corrupted for '{device_id}'."}, None

    return {}, manifest


async def _load_safety_schema(tool_context: ToolContext, entry: "RegistryDeviceEntry") -> dict | None:
    """Load safety schema for a device entry, returning None if unavailable."""
    import json as _json

    safety_artifact = entry.active_safety_artifact_name
    if not safety_artifact:
        return None
    try:
        safety_part = await tool_context.load_artifact(safety_artifact)
    except Exception:
        return None
    if safety_part is None:
        return None
    safety_text = _artifact_text(safety_part)
    if safety_text is None:
        return None
    try:
        return _json.loads(safety_text)
    except Exception:
        return None


def _format_param_constraints(safety_data: dict | None, function_name: str) -> list[dict]:
    """Extract parameter constraints from safety schema for a given function."""
    if not safety_data:
        return []
    func_safety = safety_data.get("function_safety", {}).get(function_name, {})
    constraints = func_safety.get("parameter_constraints", {})
    result = []
    for param_name, param_data in constraints.items():
        if not isinstance(param_data, dict):
            continue
        entry = {"parameter_name": param_name}
        for key in ("type_hint", "min_value", "max_value", "unit", "default_value", "description", "required"):
            if key in param_data and param_data[key] is not None:
                entry[key] = param_data[key]
        allowed = param_data.get("allowed_values", [])
        if allowed:
            entry["allowed_values"] = allowed
        result.append(entry)
    return result


def _format_func_safety_info(safety_data: dict | None, function_name: str) -> dict:
    """Extract preconditions, forbidden states, postconditions from safety schema."""
    if not safety_data:
        return {}
    func_safety = safety_data.get("function_safety", {}).get(function_name, {})
    if not func_safety:
        return {}
    info = {}
    for key in ("preconditions", "required_states", "forbidden_states", "postconditions"):
        val = func_safety.get(key, [])
        if val:
            info[key] = val
    return info


async def get_device_function_info(
    device_id: str,
    function_name: str,
    tool_context: ToolContext,
) -> dict:
    """Get detailed information about a specific function on a published device.

    Args:
        device_id: The published device ID.
        function_name: The function name to look up.

    Returns:
        dict with function signature, description, parameter constraints,
        guardrail requirements, and confirmation requirements.
    """
    import json as _json

    # Load registry entry
    try:
        existing = await tool_context.load_artifact(REGISTRY_ARTIFACT)
    except Exception:
        return {"status": "error", "message": "Registry not found."}
    if existing is None:
        return {"status": "error", "message": f"Device '{device_id}' not found in registry."}

    text = _artifact_text(existing)
    if text is None:
        return {"status": "error", "message": "Registry is empty."}
    try:
        registry = RegistrySchema(**_json.loads(text))
    except Exception:
        return {"status": "error", "message": "Registry is corrupted."}

    entry = registry.devices.get(device_id)
    if entry is None:
        return {"status": "error", "message": f"Device '{device_id}' not found."}

    # Load manifest
    error, manifest = await _load_registry_and_manifest(tool_context, device_id)
    if error:
        return error

    func_info = manifest.available_functions.get(function_name)
    if func_info is None:
        return {
            "status": "error",
            "message": f"Function '{function_name}' not found on device '{device_id}'.",
            "available_functions": sorted(manifest.available_functions.keys()),
        }

    # Load safety schema for parameter constraints
    safety_data = await _load_safety_schema(tool_context, entry)
    param_constraints = _format_param_constraints(safety_data, function_name)
    safety_info = _format_func_safety_info(safety_data, function_name)

    result = {
        "status": "success",
        "device_id": device_id,
        "function_name": function_name,
        "signature": func_info.signature,
        "description": func_info.description,
        "function_category": func_info.function_category,
        "side_effect_level": func_info.side_effect_level,
        "risk_level": func_info.risk_level,
        "requires_guardrail": func_info.requires_guardrail,
        "requires_user_confirmation_after_call": func_info.requires_user_confirmation_after_call,
    }
    if param_constraints:
        result["parameter_constraints"] = param_constraints
    if safety_info:
        result.update(safety_info)
    return result


async def get_all_device_info(
    device_id: str,
    tool_context: ToolContext,
) -> dict:
    """Get detailed information about ALL functions on a published device.

    Returns function metadata including descriptions, categories, risk levels,
    and parameter constraints for every function. Use this to understand a
    device's full capability before planning an experiment.

    Args:
        device_id: The published device ID.

    Returns:
        dict with device_name, instrument_type, and functions list.
    """
    import json as _json

    # Load registry entry
    try:
        existing = await tool_context.load_artifact(REGISTRY_ARTIFACT)
    except Exception:
        return {"status": "error", "message": "Registry not found."}
    if existing is None:
        return {"status": "error", "message": f"Device '{device_id}' not found in registry."}

    text = _artifact_text(existing)
    if text is None:
        return {"status": "error", "message": "Registry is empty."}
    try:
        registry = RegistrySchema(**_json.loads(text))
    except Exception:
        return {"status": "error", "message": "Registry is corrupted."}

    entry = registry.devices.get(device_id)
    if entry is None:
        return {"status": "error", "message": f"Device '{device_id}' not found."}

    # Load manifest
    error, manifest = await _load_registry_and_manifest(tool_context, device_id)
    if error:
        return error

    # Load safety schema
    safety_data = await _load_safety_schema(tool_context, entry)

    functions = []
    for func_name, func_info in manifest.available_functions.items():
        entry_dict = {
            "function_name": func_name,
            "signature": func_info.signature,
            "description": func_info.description,
            "function_category": func_info.function_category,
            "side_effect_level": func_info.side_effect_level,
            "risk_level": func_info.risk_level,
        }
        param_constraints = _format_param_constraints(safety_data, func_name)
        if param_constraints:
            entry_dict["parameter_constraints"] = param_constraints
        safety_info = _format_func_safety_info(safety_data, func_name)
        if safety_info:
            entry_dict.update(safety_info)
        functions.append(entry_dict)

    return {
        "status": "success",
        "device_id": device_id,
        "device_name": entry.device_name,
        "instrument_type": entry.instrument_type,
        "protocol_layer": entry.protocol_layer,
        "function_count": len(functions),
        "functions": functions,
    }


async def execute_published_function(
    device_id: str,
    function_name: str,
    arguments: dict[str, Any],
    tool_context: ToolContext,
    port: str | None = None,
) -> dict:
    """Execute a function on a published device by dynamically importing its driver.

    This is the actual device execution tool. It is guarded by the
    before_invoke_guardrail callback which performs safety checks before
    execution.

    Args:
        device_id: The published device ID.
        function_name: The function to call.
        arguments: Keyword arguments to pass to the function.

    Returns:
        dict with 'success', 'return_value', 'error' (if any).
    """
    # Load registry entry
    try:
        existing = await tool_context.load_artifact(REGISTRY_ARTIFACT)
    except Exception:
        return {"status": "error", "success": False, "error": "Registry not found."}

    if existing is None:
        return {"status": "error", "success": False, "error": f"Device '{device_id}' not in registry."}

    text = _artifact_text(existing)
    if text is None:
        return {"status": "error", "success": False, "error": "Registry is empty."}

    import json
    try:
        registry = RegistrySchema(**json.loads(text))
    except Exception:
        return {"status": "error", "success": False, "error": "Registry corrupted."}

    entry = registry.devices.get(device_id)
    if entry is None:
        return {"status": "error", "success": False, "error": f"Device '{device_id}' not found."}

    # Load driver artifact
    driver_artifact_name = entry.active_driver_artifact_name
    version = entry.latest_version
    if not driver_artifact_name:
        driver_artifact_name = published_driver_artifact(device_id, version)

    try:
        driver_part = await tool_context.load_artifact(driver_artifact_name)
    except Exception:
        return {"status": "error", "success": False, "error": f"Driver artifact not found: {driver_artifact_name}"}

    if driver_part is None:
        return {"status": "error", "success": False, "error": f"Driver artifact missing: {driver_artifact_name}"}

    driver_code = _artifact_text(driver_part)
    if driver_code is None:
        return {"status": "error", "success": False, "error": "Driver code is empty."}

    # Load build_blueprint for transport config
    bb_artifact = published_build_blueprint_artifact(device_id, version)
    build_blueprint: dict = {}
    try:
        bb_part = await tool_context.load_artifact(bb_artifact)
        if bb_part is not None:
            bb_text = _artifact_text(bb_part)
            if bb_text:
                build_blueprint = json.loads(bb_text)
    except Exception:
        pass

    transport_config = build_blueprint.get("driver_blueprint", {}).get("transport_config", {}) or {}

    # Dynamic import of driver module
    driver_instance = None
    tmp_path = None
    session_key = _get_session_key(tool_context)
    driver_cache = _get_driver_cache(tool_context)
    try:
        # Write to temp file for importlib
        tmp = tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False,
        )
        tmp.write(driver_code)
        tmp_path = tmp.name
        tmp.close()

        # Unique module name per session to avoid sys.modules collisions
        module_name = f"chem_auto_driver_{session_key}_{device_id}_{version.replace('.', '_')}"

        # Serialize module import per module_name to avoid race conditions
        async with _MODULE_IMPORT_LOCK:
            if module_name not in _MODULE_NAME_LOCKS:
                _MODULE_NAME_LOCKS[module_name] = asyncio.Lock()
        async with _MODULE_NAME_LOCKS[module_name]:
            spec = importlib.util.spec_from_file_location(module_name, tmp_path)
            if spec is None or spec.loader is None:
                return {"status": "error", "success": False, "error": f"Failed to load driver module for '{device_id}'."}

            module = importlib.util.module_from_spec(spec)
            previous_module = sys.modules.get(module_name)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                # Restore previous module on failure
                if previous_module is not None:
                    sys.modules[module_name] = previous_module
                else:
                    sys.modules.pop(module_name, None)
                raise

        # Find driver class
        driver_class_name = build_blueprint.get(
            "driver_blueprint", {},
        ).get("driver_class_name", "")
        driver_class = None
        if driver_class_name:
            driver_class = getattr(module, driver_class_name, None)
        if driver_class is None:
            # Fallback: find first class that looks like a driver
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if isinstance(obj, type) and attr_name.endswith("Driver"):
                    driver_class = obj
                    break

        if driver_class is None:
            return {"status": "error", "success": False, "error": "Could not find driver class in module."}

        # Check for cached driver instance (per-session, persists connection across calls)
        cache_key = f"{device_id}:{version}"
        cached = driver_cache.get(cache_key)
        if cached is not None:
            driver_instance = cached
            try:
                if not driver_instance.is_connected():
                    driver_instance.connect()
            except Exception:
                driver_instance = None
                driver_cache.pop(cache_key, None)

        if driver_instance is None:
            # Build transport kwargs from transport_config
            transport_kwargs: dict = {}
            transport_type = transport_config.get("transport_type", "serial")
            if transport_type == "serial":
                optional_args = transport_config.get("optional_constructor_args", {}) or {}
                # Priority: explicit port arg > session state > build_blueprint
                port = (
                    port
                    or tool_context.state.get("selected_serial_port")
                    or transport_config.get("port")
                    or ""
                )
                baudrate = optional_args.get("baudrate", 9600)
                timeout = transport_config.get("default_timeout_ms", 2000) / 1000.0
                transport_kwargs = {
                    "port": port,
                    "baudrate": baudrate,
                    "timeout": timeout,
                }
                # Pass optional serial settings (databits, parity, stopbits)
                # from transport_config — critical for non-8N1 instruments
                # like IKA RCT Basic (7E1).
                for k, v in optional_args.items():
                    if k not in transport_kwargs:
                        transport_kwargs[k] = v

            # Filter kwargs to only what the driver's __init__ accepts
            import inspect as _inspect
            sig = _inspect.signature(driver_class.__init__)
            accepted = set(sig.parameters.keys()) - {"self"}
            has_var_keyword = any(
                p.kind == _inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not has_var_keyword:
                transport_kwargs = {k: v for k, v in transport_kwargs.items() if k in accepted}

            # Instantiate driver and connect
            driver_instance = driver_class(**transport_kwargs)
            driver_instance.connect()

            # Cache for subsequent calls (per-session)
            driver_cache[cache_key] = driver_instance

        # Get and call the function
        func = getattr(driver_instance, function_name, None)
        if func is None:
            return {
                "status": "error", "success": False,
                "error": f"Function '{function_name}' not found on driver '{driver_class_name}'.",
            }

        return_value = func(**arguments)

        return {
            "status": "success",
            "success": True,
            "return_value": str(return_value) if return_value is not None else None,
            "function_name": function_name,
            "device_id": device_id,
        }

    except Exception as exc:
        # On error, evict the cached driver so the next call gets a fresh instance
        cache_key = f"{device_id}:{version}"
        if cache_key in driver_cache:
            cached = driver_cache.pop(cache_key)
            try:
                if hasattr(cached, "close"):
                    cached.close()
            except Exception:
                pass
        return {
            "status": "error",
            "success": False,
            "error": f"Execution failed: {type(exc).__name__}: {exc}",
        }
    finally:
        # Clean up temp file only — keep driver instance alive in cache
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


# ── Experiment Plan Tools ──────────────────────────────────────────────


async def save_experiment_plan(
    plan_id: str,
    name: str,
    description: str,
    steps: list[dict[str, Any]],
    tool_context: ToolContext,
) -> dict:
    """Save an experiment plan to the plan book.

    Args:
        plan_id: Unique identifier for the plan (e.g. "plan_001").
        name: Short name for the plan.
        description: What this plan does.
        steps: List of step dicts with keys: step_number, device_id,
               function_name, arguments, description.

    Returns:
        dict with status and plan_id.
    """
    from datetime import datetime, timezone
    from app.agents.shared.artifact_io import tc_load_json_artifact, tc_save_json_artifact
    from app.constants.artifact_names import EXPERIMENT_PLANS_INDEX, experiment_plan_artifact
    from app.schemas.experiment import ExperimentPlan, ExperimentStep, PlanIndexEntry

    now = datetime.now(timezone.utc).isoformat()

    parsed_steps = []
    for s in steps:
        parsed_steps.append(ExperimentStep(
            step_number=s.get("step_number", 0),
            device_id=s.get("device_id", ""),
            function_name=s.get("function_name", ""),
            arguments=s.get("arguments", {}),
            description=s.get("description", ""),
        ))

    plan = ExperimentPlan(
        plan_id=plan_id,
        name=name,
        description=description,
        steps=parsed_steps,
        created_at=now,
        updated_at=now,
    )

    # Check if plan already exists — preserve created_at
    existing = await tc_load_json_artifact(tool_context, experiment_plan_artifact(plan_id))
    if existing:
        old_plan = ExperimentPlan(**existing)
        plan.created_at = old_plan.created_at

    # Save plan
    await tc_save_json_artifact(tool_context, experiment_plan_artifact(plan_id), plan.model_dump())

    # Update index
    index = await tc_load_json_artifact(tool_context, EXPERIMENT_PLANS_INDEX, default=[])
    index = [e for e in index if e.get("plan_id") != plan_id]
    index.append(PlanIndexEntry(
        plan_id=plan_id,
        name=name,
        description=description,
        step_count=len(parsed_steps),
        created_at=plan.created_at,
        updated_at=now,
    ).model_dump())
    await tc_save_json_artifact(tool_context, EXPERIMENT_PLANS_INDEX, index)

    return {
        "status": "success",
        "plan_id": plan_id,
        "name": name,
        "step_count": len(parsed_steps),
        "message": f"Plan '{name}' saved with {len(parsed_steps)} step(s).",
    }


async def load_experiment_plan(
    plan_id: str,
    tool_context: ToolContext,
) -> dict:
    """Load a saved experiment plan from the plan book.

    Args:
        plan_id: The plan ID to load.

    Returns:
        dict with plan details.
    """
    from app.agents.shared.artifact_io import tc_load_json_artifact
    from app.constants.artifact_names import experiment_plan_artifact
    from app.schemas.experiment import ExperimentPlan

    data = await tc_load_json_artifact(tool_context, experiment_plan_artifact(plan_id))
    if data is None:
        return {"status": "error", "message": f"Plan '{plan_id}' not found."}

    plan = ExperimentPlan(**data)
    return {
        "status": "success",
        "plan_id": plan.plan_id,
        "name": plan.name,
        "description": plan.description,
        "steps": [s.model_dump() for s in plan.steps],
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


async def list_experiment_plans(tool_context: ToolContext) -> dict:
    """List all saved experiment plans.

    Returns:
        dict with list of plan summaries.
    """
    from app.agents.shared.artifact_io import tc_load_json_artifact
    from app.constants.artifact_names import EXPERIMENT_PLANS_INDEX

    index = await tc_load_json_artifact(tool_context, EXPERIMENT_PLANS_INDEX, default=[])
    return {
        "status": "success",
        "plans": index,
        "count": len(index),
        "message": f"Found {len(index)} saved plan(s)." if index else "No saved plans.",
    }


async def delete_experiment_plan(
    plan_id: str,
    tool_context: ToolContext,
) -> dict:
    """Delete a saved experiment plan.

    Args:
        plan_id: The plan ID to delete.

    Returns:
        dict with status.
    """
    from app.agents.shared.artifact_io import tc_load_json_artifact, tc_save_json_artifact
    from app.constants.artifact_names import EXPERIMENT_PLANS_INDEX, experiment_plan_artifact
    from app.tools._helpers import get_invocation_context

    # Remove from index
    index = await tc_load_json_artifact(tool_context, EXPERIMENT_PLANS_INDEX, default=[])
    new_index = [e for e in index if e.get("plan_id") != plan_id]
    if len(new_index) == len(index):
        return {"status": "error", "message": f"Plan '{plan_id}' not found."}
    await tc_save_json_artifact(tool_context, EXPERIMENT_PLANS_INDEX, new_index)

    # Delete artifact
    ctx = get_invocation_context(tool_context)
    try:
        await ctx.artifact_service.delete_artifact(
            app_name=ctx.app_name, user_id=ctx.user_id,
            session_id=ctx.session.id, filename=experiment_plan_artifact(plan_id),
        )
    except Exception:
        pass

    return {"status": "success", "plan_id": plan_id, "message": f"Plan '{plan_id}' deleted."}


# ── Experiment Log Tools ───────────────────────────────────────────────


async def start_experiment_log(
    experiment_name: str,
    tool_context: ToolContext,
    plan_id: str | None = None,
) -> dict:
    """Start a new experiment log.

    Call this before executing an experiment to begin recording.

    Args:
        experiment_name: Name for this experiment run.
        plan_id: Optional — link to a saved plan.

    Returns:
        dict with log_id (use this to log steps and finish).
    """
    from datetime import datetime, timezone
    from app.agents.shared.artifact_io import tc_save_json_artifact
    from app.constants.artifact_names import EXPERIMENT_LOGS_INDEX, experiment_log_artifact
    from app.schemas.experiment import ExperimentLog, LogIndexEntry

    log_id = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()

    log = ExperimentLog(
        log_id=log_id,
        plan_id=plan_id,
        experiment_name=experiment_name,
        started_at=now,
    )

    await tc_save_json_artifact(tool_context, experiment_log_artifact(log_id), log.model_dump())

    # Update index
    from app.agents.shared.artifact_io import tc_load_json_artifact
    index = await tc_load_json_artifact(tool_context, EXPERIMENT_LOGS_INDEX, default=[])
    index.append(LogIndexEntry(
        log_id=log_id,
        experiment_name=experiment_name,
        plan_id=plan_id,
        started_at=now,
    ).model_dump())
    await tc_save_json_artifact(tool_context, EXPERIMENT_LOGS_INDEX, index)

    return {
        "status": "success",
        "log_id": log_id,
        "experiment_name": experiment_name,
        "message": f"Experiment log started: {log_id}",
    }


async def log_experiment_step(
    log_id: str,
    step_number: int,
    device_id: str,
    function_name: str,
    arguments: dict[str, Any],
    description: str,
    input_params: dict[str, Any],
    output_value: Any,
    tool_context: ToolContext,
    status: str = "success",
    error_message: str | None = None,
) -> dict:
    """Record a step execution in the experiment log.

    Call this after each invoke_function call to log what happened.

    Args:
        log_id: The log ID from start_experiment_log.
        step_number: Step number in the experiment.
        device_id: Which device was used.
        function_name: Which function was called.
        arguments: Arguments passed to the function.
        description: Human-readable description of this step.
        input_params: Actual parameters sent to the instrument.
        output_value: Return value from the instrument.
        status: "success", "failed", or "skipped".
        error_message: Error message if failed.

    Returns:
        dict with status.
    """
    from datetime import datetime, timezone
    from app.agents.shared.artifact_io import tc_load_json_artifact, tc_save_json_artifact
    from app.constants.artifact_names import experiment_log_artifact
    from app.schemas.experiment import ExperimentLog, StepExecution

    data = await tc_load_json_artifact(tool_context, experiment_log_artifact(log_id))
    if data is None:
        return {"status": "error", "message": f"Log '{log_id}' not found."}

    log = ExperimentLog(**data)
    now = datetime.now(timezone.utc).isoformat()

    step = StepExecution(
        step_number=step_number,
        device_id=device_id,
        function_name=function_name,
        arguments=arguments,
        description=description,
        input_params=input_params,
        output_value=str(output_value) if output_value is not None else None,
        status=status,
        error_message=error_message,
        started_at=now,
        finished_at=now,
    )
    log.steps.append(step)

    await tc_save_json_artifact(tool_context, experiment_log_artifact(log_id), log.model_dump())

    return {
        "status": "success",
        "log_id": log_id,
        "step_recorded": step_number,
        "message": f"Step {step_number} recorded ({status}).",
    }


async def finish_experiment_log(
    log_id: str,
    tool_context: ToolContext,
    overall_status: str = "completed",
) -> dict:
    """Mark an experiment log as finished.

    Args:
        log_id: The log ID to finish.
        overall_status: "completed", "failed", or "aborted".

    Returns:
        dict with status and summary.
    """
    from datetime import datetime, timezone
    from app.agents.shared.artifact_io import tc_load_json_artifact, tc_save_json_artifact
    from app.constants.artifact_names import EXPERIMENT_LOGS_INDEX, experiment_log_artifact
    from app.schemas.experiment import ExperimentLog

    data = await tc_load_json_artifact(tool_context, experiment_log_artifact(log_id))
    if data is None:
        return {"status": "error", "message": f"Log '{log_id}' not found."}

    log = ExperimentLog(**data)
    now = datetime.now(timezone.utc).isoformat()
    log.overall_status = overall_status
    log.finished_at = now

    await tc_save_json_artifact(tool_context, experiment_log_artifact(log_id), log.model_dump())

    # Update index
    index = await tc_load_json_artifact(tool_context, EXPERIMENT_LOGS_INDEX, default=[])
    for entry in index:
        if entry.get("log_id") == log_id:
            entry["overall_status"] = overall_status
            entry["finished_at"] = now
            entry["step_count"] = len(log.steps)
            break
    await tc_save_json_artifact(tool_context, EXPERIMENT_LOGS_INDEX, index)

    return {
        "status": "success",
        "log_id": log_id,
        "overall_status": overall_status,
        "total_steps": len(log.steps),
        "message": f"Experiment {overall_status}. {len(log.steps)} step(s) recorded.",
    }


async def list_experiment_logs(tool_context: ToolContext) -> dict:
    """List all experiment logs.

    Returns:
        dict with list of log summaries.
    """
    from app.agents.shared.artifact_io import tc_load_json_artifact
    from app.constants.artifact_names import EXPERIMENT_LOGS_INDEX

    index = await tc_load_json_artifact(tool_context, EXPERIMENT_LOGS_INDEX, default=[])
    return {
        "status": "success",
        "logs": index,
        "count": len(index),
        "message": f"Found {len(index)} experiment log(s)." if index else "No experiment logs.",
    }


async def load_experiment_log(
    log_id: str,
    tool_context: ToolContext,
) -> dict:
    """Load a full experiment log with all step details.

    Args:
        log_id: The log ID to load.

    Returns:
        dict with full log details.
    """
    from app.agents.shared.artifact_io import tc_load_json_artifact
    from app.constants.artifact_names import experiment_log_artifact
    from app.schemas.experiment import ExperimentLog

    data = await tc_load_json_artifact(tool_context, experiment_log_artifact(log_id))
    if data is None:
        return {"status": "error", "message": f"Log '{log_id}' not found."}

    log = ExperimentLog(**data)
    return {
        "status": "success",
        **log.model_dump(),
    }


async def delete_published_device(
    device_id: str,
    tool_context: ToolContext,
) -> dict:
    """Delete a published device from the registry and clean up its artifacts.

    Removes the device entry from the cross-session registry and deletes
    all associated artifacts (driver, manifest, safety, function_catalog,
    build_blueprint) for every published version.

    Args:
        device_id: The published device ID to delete.

    Returns:
        dict with 'status', 'device_id', 'device_name', 'versions_deleted',
        and 'artifacts_deleted' on success.
    """
    from app.services.registry_service import delete_device_registry_entry
    from app.constants.artifact_names import (
        published_build_blueprint_artifact,
        published_driver_artifact,
        published_function_catalog_artifact,
        published_manifest_artifact,
        published_safety_artifact,
    )
    from app.tools._helpers import get_invocation_context

    # Remove from registry
    entry = await delete_device_registry_entry(tool_context, device_id)
    if entry is None:
        return {"status": "error", "message": f"Device '{device_id}' not found in registry."}

    # Collect all versions to clean up
    versions = set(entry.available_versions)
    versions.add(entry.latest_version)

    # Delete artifacts for each version
    ctx = get_invocation_context(tool_context)
    artifact_fns = [
        published_driver_artifact,
        published_manifest_artifact,
        published_safety_artifact,
        published_function_catalog_artifact,
        published_build_blueprint_artifact,
    ]
    artifacts_deleted = 0
    for ver in versions:
        for fn in artifact_fns:
            artifact_name = fn(device_id, ver)
            try:
                await ctx.artifact_service.delete_artifact(
                    app_name=ctx.app_name,
                    user_id=ctx.user_id,
                    filename=artifact_name,
                    session_id=ctx.session.id,
                )
                artifacts_deleted += 1
            except Exception:
                pass  # Artifact may not exist

    return {
        "status": "success",
        "device_id": device_id,
        "device_name": entry.device_name,
        "versions_deleted": sorted(versions),
        "artifacts_deleted": artifacts_deleted,
        "message": f"Deleted '{entry.device_name}' ({len(versions)} version(s), {artifacts_deleted} artifact(s)).",
    }


def _artifact_text(part: Any) -> str | None:
    """Extract text from an artifact Part."""
    text = getattr(part, "text", None)
    if text is not None:
        return text
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None) is not None:
        return inline.data.decode("utf-8", errors="replace")
    return None
