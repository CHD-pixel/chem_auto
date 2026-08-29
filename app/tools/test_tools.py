"""TEST-stage tools — connect, run tests, fix code, confirm results.

Architecture: tools do I/O, the agent decides what to do next.
No embedded LLM calls, no hardcoded routing.
"""

from __future__ import annotations

import asyncio
import inspect
import traceback

from google.adk.tools import ToolContext

from app.constants.state_keys import (
    CURRENT_CANDIDATE_CODE,
    SELECTED_SERIAL_PORT,
    FUNCTION_TEST_RESULTS,
    TEST_STATUS,
)
from app.device_comm_tool import list_available_serial_ports
from app.testing.phase7_runtime import _make_placeholder_value
from app.tools._helpers import get_invocation_context


def _error_result(exc: Exception) -> dict:
    return {
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc()[-500:],
    }


# ── Serial port tools ─────────────────────────────────────────────────


def list_serial_ports(tool_context: ToolContext) -> dict:
    """List available serial ports on this machine."""
    ports = list_available_serial_ports()
    port_list = [p["port"] for p in ports]
    return {
        "status": "ok",
        "ports": ports,
        "count": len(ports),
        "message": f"Found {len(ports)} port(s): {', '.join(port_list) if port_list else 'none'}.",
    }


def verify_serial_port(port: str, tool_context: ToolContext) -> dict:
    """Verify that a specific serial port is accessible by trying to open it."""
    from app.device_comm_tool import verify_serial_port as _verify
    return _verify(port)


# ── Real device tests ─────────────────────────────────────────────────


def connect_device(port: str, tool_context: ToolContext) -> dict:
    """Configure the serial port for device testing.

    Does not actually connect — the driver connects internally when run_tests() is called.

    Args:
        port: Serial port name (e.g. "COM11", "/dev/ttyUSB0").

    Returns:
        dict with status and port info.
    """
    if not port:
        return {"status": "error", "message": "Port is required."}
    tool_context.state[SELECTED_SERIAL_PORT] = port
    return {
        "status": "ok",
        "port": port,
        "message": f"Port set to {port}. Use run_tests() to test functions.",
    }


def _check_needs_confirmation(func_meta: dict) -> bool:
    """Check if a function requires user confirmation after execution."""
    if func_meta.get("user_confirmation_required_after_call"):
        return True
    category = func_meta.get("function_category", "")
    side_effect = func_meta.get("side_effect_level", "none")
    if category in ("control", "setup", "cleanup", "safety"):
        return True
    if side_effect in ("medium", "high"):
        return True
    return False


def _store_test_result(state: dict, function_name: str, result: dict) -> None:
    """Store test result in state for fix_driver_code() to read."""
    results = dict(state.get(FUNCTION_TEST_RESULTS, {}))
    results[function_name] = {
        "success": result.get("status") == "pass",
        "status": result.get("status"),
        "return_value": result.get("return_value"),
        "error_message": result.get("error", ""),
        "traceback": result.get("traceback", ""),
        "arguments": result.get("arguments", {}),
    }
    state[FUNCTION_TEST_RESULTS] = results


async def run_tests(
    functions: list, skip_confirmations: bool,
    port: str, tool_context: ToolContext,
    arguments: dict | None = None,
) -> dict:
    """Run multiple functions on the real device with a single connection.

    Creates one driver instance, connects once, runs all functions, disconnects once.
    Use connect_device() first, or pass port to auto-connect.

    Args:
        functions: List of function names to test. If empty, tests all functions.
        skip_confirmations: If True, skip confirmation prompts.
        port: Serial port (sets connect_device if provided).
        arguments: Optional dict of {function_name: {param: value}} for explicit
            parameter control. If not provided for a function, arguments are
            auto-generated from parameter constraints.

    Returns:
        dict with per-function results, pass/fail counts, overall verdict.
    """
    import json as _json
    state = tool_context.state

    if port:
        connect_device(port=port, tool_context=tool_context)

    if not state.get(SELECTED_SERIAL_PORT):
        return {"status": "error", "message": "No serial port set. Call connect_device(port) first."}

    # Get function list from device_spec
    if not functions:
        device_spec = state.get("device_spec", {})
        if not device_spec:
            from app.agents.build.manual_understanding_flow import _build_device_spec
            device_spec = _build_device_spec(state)
            state["device_spec"] = device_spec
        ds_functions = device_spec.get("functions", [])
        if ds_functions:
            functions = [f.get("function_name") for f in ds_functions if f.get("function_name")]
        else:
            return {"status": "error", "message": "No functions specified and no device_spec found."}

    # Load code and build driver once
    artifact_name = state.get(CURRENT_CANDIDATE_CODE, "")
    if not artifact_name:
        return {"status": "error", "message": "No candidate code found. Call generate_code() first."}

    ctx = get_invocation_context(tool_context)
    from app.agents.shared.artifact_io import load_text_artifact
    code = await load_text_artifact(ctx, artifact_name)
    if not code:
        return {"status": "error", "message": f"Cannot load artifact: {artifact_name}"}

    device_spec = state.get("device_spec", {})
    from app.tools._blueprint_utils import device_spec_to_blueprint
    blueprint = device_spec_to_blueprint(device_spec)

    from app.testing.phase7_runtime import _build_module_from_code
    try:
        module = _build_module_from_code(code)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to compile code: {exc}"}

    driver_class_name = blueprint.driver_blueprint.driver_class_name
    driver_class = getattr(module, driver_class_name, None)
    if driver_class is None:
        return {"status": "error", "message": f"Class '{driver_class_name}' not found in code."}

    # Build driver instance once
    from app.device_comm_tool import resolve_real_serial_settings
    from app.services.test_argument_service import build_test_arguments
    raw_table = (state.get("flat_cmd_table", {}) or {}).get("raw_table", "")
    serial_settings = {}
    selected_port = state.get(SELECTED_SERIAL_PORT, "")
    if selected_port:
        serial_settings = resolve_real_serial_settings(
            blueprint=blueprint, selected_port=selected_port,
            protocol_spec_payload=state.get("protocol_spec"),
        )

    driver = None
    try:
        init_sig = inspect.signature(driver_class.__init__)
        constructor_kwargs, _ = build_test_arguments(
            function_name="__init__", signature=init_sig,
            build_blueprint=blueprint, safety_schema_payload=state.get("safety_schema"),
            fallback_factory=_make_placeholder_value,
            flat_cmd_table_raw=raw_table,
        )
        from app.agents.test.shared import merge_serial_settings
        merge_serial_settings(constructor_kwargs, serial_settings)
        driver = driver_class(**constructor_kwargs)

        # Connect once
        if hasattr(driver, "connect"):
            try:
                driver.connect()
            except Exception as exc:
                return {
                    "status": "error",
                    "message": f"connect() failed: {exc}",
                    "traceback": traceback.format_exc()[-500:],
                }

        # Run all functions with shared connection
        results = {}
        passed = []
        failed = []
        needs_confirm = []

        for i, fn in enumerate(functions):
            external_args = (arguments or {}).get(fn)
            result = _run_single_on_driver(
                driver, fn, blueprint, state, raw_table,
                external_args=external_args,
            )
            results[fn] = result
            if result.get("status") == "pass":
                passed.append(fn)
            else:
                failed.append(fn)
            if result.get("needs_confirmation"):
                needs_confirm.append(fn)

            # Wait 3 seconds after receiving feedback before next command
            # (last function doesn't need to wait)
            if i < len(functions) - 1:
                await asyncio.sleep(3)

        verdict = "pass" if not failed else "fail"
        state[TEST_STATUS] = "passed" if verdict == "pass" else "failed"

        return {
            "status": "completed",
            "verdict": verdict,
            "passed_count": len(passed),
            "failed_count": len(failed),
            "total_count": len(functions),
            "passed_functions": passed,
            "failed_functions": failed,
            "needs_confirmation": needs_confirm,
            "results": results,
            "message": (
                f"{len(passed)}/{len(functions)} passed. "
                f"Failed: {', '.join(failed[:5]) if failed else 'none'}."
                + (f" Need confirmation: {', '.join(needs_confirm)}." if needs_confirm else "")
            ),
        }

    except Exception as exc:
        return {
            "status": "error",
            "message": f"Test execution failed: {exc}",
            "traceback": traceback.format_exc()[-500:],
        }
    finally:
        if driver is not None and hasattr(driver, "disconnect"):
            try:
                driver.disconnect()
            except Exception:
                pass


def _run_single_on_driver(
    driver, function_name: str, blueprint, state: dict, raw_table: str,
    external_args: dict | None = None,
) -> dict:
    """Run a single function on an already-connected driver instance."""
    # Check confirmation requirement
    func_meta = {}
    for fname, finfo in blueprint.driver_blueprint.action_methods.items():
        if fname == function_name:
            func_meta = finfo if isinstance(finfo, dict) else finfo.model_dump()
            break
    needs_confirmation = _check_needs_confirmation(func_meta)

    # Extract parameter constraints for this function
    constraints = {}
    for fname, finfo in blueprint.driver_blueprint.action_methods.items():
        if fname == function_name:
            meta = finfo if isinstance(finfo, dict) else finfo.model_dump()
            constraints = meta.get("parameter_constraints", {})
            break

    try:
        method = getattr(driver, function_name, None)
        if method is None:
            result = {"status": "error", "function_name": function_name,
                      "error": f"Method '{function_name}' not found."}
            _store_test_result(state, function_name, result)
            return result

        # Build arguments: use external if provided, otherwise auto-generate
        kwargs = {}
        if external_args:
            kwargs = external_args
        elif callable(method):
            from app.services.test_argument_service import build_test_arguments
            sig = inspect.signature(method)
            kwargs, _ = build_test_arguments(
                function_name=function_name, signature=sig,
                build_blueprint=blueprint, safety_schema_payload=state.get("safety_schema"),
                fallback_factory=_make_placeholder_value,
                flat_cmd_table_raw=raw_table,
            )

        # Call the function
        if callable(method):
            result_value = method(**kwargs)
        else:
            result_value = method  # @property

        result = {
            "status": "pass",
            "function_name": function_name,
            "return_value": repr(result_value) if result_value is not None else None,
            "arguments": kwargs,
            "constraints": constraints,
        }

        if needs_confirmation:
            expected_effect = func_meta.get("purpose", "")
            result["needs_confirmation"] = True
            result["confirmation_question"] = (
                f"Executed `{function_name}`.\n"
                f"Expected effect: {expected_effect or 'device should respond'}\n"
                f"Return value: {result['return_value']}\n\n"
                "Please check the device and reply: yes / no / uncertain"
            )

        _store_test_result(state, function_name, result)
        return result

    except Exception as exc:
        result = {
            "status": "fail",
            "function_name": function_name,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-500:],
        }
        _store_test_result(state, function_name, result)
        return result


# ── Confirmation ──────────────────────────────────────────────────────


async def confirm_result(function_name: str, reply: str, tool_context: ToolContext) -> dict:
    """Submit user confirmation for a function's physical effect.

    After run_test() returns needs_confirmation=True, the agent should ask
    the user to check the device and call this tool with their reply.

    Args:
        function_name: The function that was tested.
        reply: User's reply ("yes", "no", "uncertain", or natural language).

    Returns:
        dict with verdict (pass/fail/manual_review) and next action.
    """
    from app.services.confirmation_service import (
        normalize_user_confirmation,
        merge_tool_and_user_confirmation,
    )

    state = tool_context.state
    fn_results = state.get(FUNCTION_TEST_RESULTS, {})
    fn_result = fn_results.get(function_name, {})
    tool_success = fn_result.get("success", False)

    user_confirmation = normalize_user_confirmation(reply)
    merged = merge_tool_and_user_confirmation(tool_success, user_confirmation)

    # Update stored result
    updated_results = dict(fn_results)
    updated_results[function_name] = {
        **fn_result,
        "user_confirmation": user_confirmation,
        "final_verdict": merged["final_verdict"],
    }
    state[FUNCTION_TEST_RESULTS] = updated_results

    return {
        "status": "ok",
        "function_name": function_name,
        "verdict": merged["final_verdict"],
        "user_reply": user_confirmation,
        "reason": merged["reason"],
        "should_continue_testing": merged["should_continue_testing"],
        "should_trigger_repair": merged["should_trigger_repair"],
        "message": f"Confirmation result: {merged['final_verdict']}. {merged['reason']}",
    }


# ── Diagnostics ───────────────────────────────────────────────────────


async def fix_driver_code(errors: str, tool_context: ToolContext) -> dict:
    """Show current code + errors for the agent to fix using edit_code().

    This tool gathers diagnostic context (code, errors, protocol info) and
    returns it for the agent to analyze. The agent should then call
    edit_code(old_string, new_string) to apply targeted fixes.

    Args:
        errors: JSON string with error list (optional — auto-discovered from state).

    Returns:
        dict with current_code, errors, protocol_context.
    """
    try:
        import json
        state = tool_context.state
        ctx = get_invocation_context(tool_context)

        from app.agents.shared.artifact_io import load_text_artifact
        current_code = await load_text_artifact(ctx, state.get(CURRENT_CANDIDATE_CODE, ""))
        if not current_code:
            return {"status": "error", "message": "No driver code found. Call generate_code() first."}

        # Collect errors
        if errors:
            try:
                error_list = json.loads(errors)
            except json.JSONDecodeError:
                error_list = [{"function": "unknown", "error": errors}]
        else:
            error_list = []
            fn_results = state.get(FUNCTION_TEST_RESULTS, {})
            for fn_name, fn_result in fn_results.items():
                if not fn_result.get("success", False):
                    error_list.append({
                        "function": fn_name,
                        "error": fn_result.get("error_message", "unknown error"),
                        "traceback": fn_result.get("traceback", ""),
                    })
            if not error_list:
                from app.constants.state_keys import FAILURE_SUMMARY
                failure = state.get(FAILURE_SUMMARY, {})
                if failure:
                    error_list.append({
                        "function": failure.get("function_name", "__connect__"),
                        "error": failure.get("error_message", "bootstrap failure"),
                        "traceback": failure.get("traceback_summary", ""),
                    })

        if not error_list:
            return {
                "status": "completed",
                "message": "No errors found. All tests passed.",
            }

        # Filter invalid function names
        _invalid_names = {"__driver__", "__connect__", "__init__", "unknown", ""}
        error_list = [e for e in error_list if e.get("function", "") not in _invalid_names]

        # Build error summary
        def _fmt_err(e: dict) -> str:
            fn = e.get('function', '?')
            err = e.get('error', '?')
            tb = e.get('traceback', '')
            line = f"- {fn}: {err}"
            if tb:
                tb_lines = [l.rstrip() for l in tb.strip().split('\n') if l.strip()]
                if tb_lines:
                    line += "\n  Traceback: " + " | ".join(tb_lines[-3:])
            return line

        error_table = "\n".join(_fmt_err(e) for e in error_list[:5])

        # Build protocol context (compact)
        device_spec = state.get("device_spec", {}) or {}
        protocol_summary = ""
        ds_raw_table = device_spec.get("raw_command_table", "")
        if ds_raw_table:
            protocol_summary += "Command table:\n" + ds_raw_table[:2000] + "\n"
        ds_protocol = device_spec.get("protocol", {})
        if ds_protocol:
            protocol_summary += f"Framing: {json.dumps(ds_protocol)}\n"
        ds_connection = device_spec.get("connection", {})
        if ds_connection:
            protocol_summary += f"Connection: {json.dumps(ds_connection)}\n"

        return {
            "status": "completed",
            "errors": error_table,
            "error_count": len(error_list),
            "current_code": current_code,
            "protocol_context": protocol_summary[:3000],
            "message": f"Found {len(error_list)} error(s). See errors, code, and protocol context above.",
        }

    except Exception as exc:
        return _error_result(exc)
