"""INVOKE-stage tools — list and call published device drivers.

Migrated from stage_tools/invoke_tools.py.
"""

from __future__ import annotations

import traceback
from typing import Any

from google.adk.tools import ToolContext

from app.device_comm_tool import list_available_serial_ports, verify_serial_port as _verify_serial_port

from app.agents.invoke.invoke_tools import (
    delete_published_device as _delete_published_device,
    delete_experiment_plan as _delete_experiment_plan,
    execute_published_function as _execute_published_function,
    finish_experiment_log as _finish_experiment_log,
    get_all_device_info as _get_all_device_info,
    get_device_function_info as _get_device_function_info,
    list_experiment_logs as _list_experiment_logs,
    list_experiment_plans as _list_experiment_plans,
    list_published_devices as _list_published_devices,
    load_experiment_log as _load_experiment_log,
    load_experiment_plan as _load_experiment_plan,
    log_experiment_step as _log_experiment_step,
    save_experiment_plan as _save_experiment_plan,
    start_experiment_log as _start_experiment_log,
)


def _error_result(exc: Exception) -> dict:
    return {
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc()[-500:],
    }


async def list_devices(tool_context: ToolContext) -> dict:
    """List all published, active devices from the cross-session registry."""
    try:
        result = await _list_published_devices(tool_context)
        if result.get("status") == "success" and result.get("devices"):
            device_ids = [d.get("device_id", "?") for d in result["devices"]]
            result["message"] = f"Found {len(result['devices'])} device(s): {', '.join(device_ids)}."
        elif result.get("status") == "success":
            result["message"] = "No published devices found."
        return result
    except Exception as exc:
        return _error_result(exc)


def list_serial_ports(tool_context: ToolContext) -> dict:
    """List available serial ports on this machine."""
    try:
        ports = list_available_serial_ports()
        port_list = [p["port"] for p in ports]
        return {
            "status": "ok",
            "ports": ports,
            "count": len(ports),
            "message": f"Found {len(ports)} port(s): {', '.join(port_list) if port_list else 'none'}.",
        }
    except Exception as exc:
        return _error_result(exc)


def verify_serial_port(port: str, tool_context: ToolContext) -> dict:
    """Verify that a specific serial port is accessible by trying to open it."""
    try:
        return _verify_serial_port(port)
    except Exception as exc:
        return _error_result(exc)


async def get_device_info(device_id: str, function_name: str, tool_context: ToolContext) -> dict:
    """Get detailed information about a specific function on a published device."""
    try:
        return await _get_device_function_info(device_id, function_name, tool_context)
    except Exception as exc:
        return _error_result(exc)


async def invoke_function(device_id: str, function_name: str, arguments: dict[str, Any],
                          tool_context: ToolContext, port: str | None = None) -> dict:
    """Execute a published driver function on a real instrument.

    Safety guardrails (parameter range, call sequence, forbidden sequences)
    are checked automatically before execution.

    Args:
        device_id: Published device ID.
        function_name: Function to call.
        arguments: Keyword arguments for the function.
        port: Optional — override the serial port (e.g. "COM11").
              If not provided, uses the port from the build blueprint.
    """
    try:
        return await _execute_published_function(device_id, function_name, arguments, tool_context, port=port)
    except Exception as exc:
        return _error_result(exc)


async def get_all_device_functions(device_id: str, tool_context: ToolContext) -> dict:
    """Get detailed information about ALL functions on a published device.

    Returns function metadata including descriptions, categories, risk levels,
    and parameter constraints. Use this to understand a device's full capability
    before planning an experiment.
    """
    try:
        return await _get_all_device_info(device_id, tool_context)
    except Exception as exc:
        return _error_result(exc)


async def delete_device(device_id: str, tool_context: ToolContext) -> dict:
    """Delete a published device from the registry and clean up its artifacts.

    Removes the device entry and all associated driver/manifest/safety artifacts.
    """
    try:
        result = await _delete_published_device(device_id, tool_context)
        return result
    except Exception as exc:
        return _error_result(exc)


# ── Experiment Plan Tools ──────────────────────────────────────────────


async def save_plan(plan_id: str, name: str, description: str, steps: list[dict],
                    tool_context: ToolContext) -> dict:
    """Save an experiment plan to the plan book for later reuse."""
    try:
        return await _save_experiment_plan(plan_id, name, description, steps, tool_context)
    except Exception as exc:
        return _error_result(exc)


async def load_plan(plan_id: str, tool_context: ToolContext) -> dict:
    """Load a saved experiment plan by ID."""
    try:
        return await _load_experiment_plan(plan_id, tool_context)
    except Exception as exc:
        return _error_result(exc)


async def list_plans(tool_context: ToolContext) -> dict:
    """List all saved experiment plans."""
    try:
        return await _list_experiment_plans(tool_context)
    except Exception as exc:
        return _error_result(exc)


async def delete_plan(plan_id: str, tool_context: ToolContext) -> dict:
    """Delete a saved experiment plan."""
    try:
        return await _delete_experiment_plan(plan_id, tool_context)
    except Exception as exc:
        return _error_result(exc)


# ── Experiment Log Tools ───────────────────────────────────────────────


async def start_log(experiment_name: str, tool_context: ToolContext,
                    plan_id: str | None = None) -> dict:
    """Start recording a new experiment log. Returns log_id for subsequent calls."""
    try:
        return await _start_experiment_log(experiment_name, tool_context, plan_id)
    except Exception as exc:
        return _error_result(exc)


async def log_step(log_id: str, step_number: int, device_id: str, function_name: str,
                   arguments: dict[str, Any], description: str, input_params: dict[str, Any],
                   output_value: Any, tool_context: ToolContext,
                   status: str = "success", error_message: str | None = None) -> dict:
    """Record a single step execution in the experiment log."""
    try:
        return await _log_experiment_step(
            log_id, step_number, device_id, function_name, arguments,
            description, input_params, output_value, tool_context, status, error_message,
        )
    except Exception as exc:
        return _error_result(exc)


async def finish_log(log_id: str, tool_context: ToolContext,
                     overall_status: str = "completed") -> dict:
    """Mark an experiment log as finished."""
    try:
        return await _finish_experiment_log(log_id, tool_context, overall_status)
    except Exception as exc:
        return _error_result(exc)


async def list_logs(tool_context: ToolContext) -> dict:
    """List all experiment logs."""
    try:
        return await _list_experiment_logs(tool_context)
    except Exception as exc:
        return _error_result(exc)


async def load_log(log_id: str, tool_context: ToolContext) -> dict:
    """Load a full experiment log with all step details."""
    try:
        return await _load_experiment_log(log_id, tool_context)
    except Exception as exc:
        return _error_result(exc)
