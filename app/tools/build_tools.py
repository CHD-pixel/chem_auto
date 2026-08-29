"""BUILD-stage tools — pure I/O for code generation, coverage, merge.

Tools:
  - run_manual_understanding: OCR + parallel data extraction from PDF
  - generate_code: build blueprint + run CodeWriterAgent (LLM)
  - check_function_coverage: verify coverage against device_spec (deterministic)
  - merge_code: AST-merge new functions into existing code (deterministic)

Agent decides the retry loop — tools do not hardcode control flow.
"""

from __future__ import annotations

import ast
import logging
import re
import textwrap
import traceback
from typing import Any

from google.adk.tools import ToolContext

from app.agents.build.manual_understanding_flow import create_manual_understanding_flow
from app.constants.artifact_names import candidate_driver_artifact
from app.constants.state_keys import (
    ACTIVE_DEVICE,
    CURRENT_CANDIDATE_CODE,
    SELECTED_SKILL,
)
from app.services.skill_cache import SKILL_CACHE
from app.tools._helpers import get_invocation_context

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _error_result(exc: Exception) -> dict:
    return {
        "status": "error",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc()[-500:],
    }


def _stem(name: str) -> str:
    """Strip verb prefixes and normalize special characters for fuzzy matching."""
    n = name
    for _ in range(3):
        for pfx in ("get_get_", "set_set_", "get_set_", "set_get_",
                     "get_", "set_", "read_", "write_", "start_", "stop_"):
            if n.startswith(pfx):
                n = n[len(pfx):]
                break
        else:
            break
    n = re.sub(r'\(([^)]+)\)', r'_\1', n)
    n = n.replace(",", "_")
    n = re.sub(r'_+', '_', n)
    return n.strip("_")


def _to_pascal_case(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_") if w)


def _parse_range_to_constraint(range_str: str) -> dict[str, Any] | None:
    """Parse a range string from the command table into parameter constraints."""
    r = range_str.strip()
    if not r or r.startswith("对应参数见") or r.startswith("参见"):
        return None

    unit = ""
    unit_match = re.search(r'(?:[a-zA-Z/°℃℉]+(?:/[a-zA-Z]+)?|[一-鿿]+)\s*$', r)
    if unit_match:
        unit = unit_match.group().strip()
        r = r[:unit_match.start()].strip()

    r = re.sub(r'\s*[±]\s*\d+(\.\d+)?\s*$', '', r)

    if "," in r:
        vals: list[int | float | str] = []
        for token in r.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                vals.append(int(token))
            except ValueError:
                try:
                    vals.append(float(token))
                except ValueError:
                    if token:
                        vals.append(token)
        if len(vals) >= 2:
            return {"allowed_values": vals, "unit": unit}
        for token in r.split(","):
            token = token.strip()
            dm = re.match(r'^(-?[\d.]+)\s*-\s*(-?[\d.]+)$', token)
            if dm:
                return {"min_value": float(dm.group(1)), "max_value": float(dm.group(2)), "unit": unit}
        return None

    dash_match = re.match(r'^(-?[\d.]+)\s*-\s*(-?[\d.]+)$', r)
    if dash_match:
        lo = float(dash_match.group(1))
        hi = float(dash_match.group(2))
        return {"min_value": lo if lo == int(lo) else lo, "max_value": hi if hi == int(hi) else hi, "unit": unit}

    colon_nums = re.findall(r'(\d+)\s*[：:]', r)
    if colon_nums:
        return {"allowed_values": [int(n) for n in colon_nums], "unit": unit}

    return None


def _parse_command_table_to_functions(raw_table: str, protocol_family: str = "") -> dict:
    """Parse raw command table into function definitions."""
    functions: dict = {}
    if not raw_table.strip():
        return {"functions": functions}

    is_modbus = protocol_family.upper().replace("_", "-") in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP")

    for line in raw_table.strip().replace("\\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|---" in line or "| --" in line:
            continue
        if line.startswith("| Address") or line.startswith("| 地址") or line.startswith("| Register"):
            continue

        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            while parts and not parts[0]:
                parts.pop(0)
            while parts and not parts[-1]:
                parts.pop()
        else:
            parts = line.split()

        if len(parts) < 2:
            continue

        cmd_id = parts[0]
        name = parts[1]
        rw_hint = parts[2] if len(parts) > 2 else ""

        if not cmd_id or cmd_id.lower() in ("command", "register", "address"):
            continue
        if name.lower() in ("name", "名称", "description", "说明", "function", "purpose", ""):
            continue

        safe_name = name.lower().replace(" ", "_").replace("-", "_").replace("/", "_").replace(".", "_")
        if not safe_name or safe_name == "_":
            continue

        safe_name = re.sub(r'\(([^)]+)\)', r'_\1', safe_name)
        safe_name = re.sub(r'_+', '_', safe_name).strip("_")

        _verb_prefixes = ("get_", "set_", "read_", "write_", "start_", "stop_")
        for _vp in _verb_prefixes:
            if safe_name.startswith(_vp):
                safe_name = safe_name[len(_vp):]
                break

        if not all(ord(c) < 128 for c in safe_name):
            eng_hint = ""
            for pi in range(3, len(parts)):
                tok = parts[pi]
                if all(ord(c) < 128 for c in tok) and len(tok) > 2:
                    eng_hint = tok.lower().replace(" ", "_").replace("-", "_").replace("/", "_")
                    break
            if eng_hint and not eng_hint.startswith(("unsigned", "float", "int", "0x")):
                safe_name = eng_hint
            else:
                safe_name = f"reg_{cmd_id.lower().replace('0x', '').replace(' ', '_')}"

        is_read = any(k in rw_hint.lower() for k in ("r", "ro", "读", "read", "rd"))
        is_write = any(k in rw_hint.lower() for k in ("w", "wo", "写", "write", "wr", "rw"))
        if not is_read and not is_write:
            is_read = True

        func_cat = "read" if (is_read and not is_write) else "control"
        side_effect = "none" if func_cat == "read" else "medium"
        desc = parts[3] if len(parts) > 3 else name

        param_constraints = None
        for pi in range(2, len(parts)):
            constraint = _parse_range_to_constraint(parts[pi])
            if constraint is not None:
                param_constraints = {"value": constraint}
                break

        if safe_name not in functions:
            signature = f"def {safe_name}(self) -> Any" if func_cat == "read" else f"def {safe_name}(self, value) -> None"
            functions[safe_name] = {
                "function_name": safe_name,
                "purpose": desc,
                "signature": signature,
                "function_category": func_cat,
                "side_effect_level": side_effect,
                "protocol_action_binding": [str(cmd_id)],
                "implementation_strategy": "direct_command",
                "test_functional_category": "functional" if func_cat == "read" else "safety",
            }
            if param_constraints:
                functions[safe_name]["parameter_constraints"] = param_constraints

    return {"functions": functions}


def _build_flat_summary(state: dict) -> dict:
    """Build a summary of extracted flat fields for the LLM."""
    flat_keys = ["flat_device", "flat_cmd_table", "flat_serial", "flat_framing",
                 "flat_network", "flat_timing"]
    extracted = {}
    for k in flat_keys:
        val = state.get(k)
        if val and isinstance(val, dict):
            non_empty = {kk: vv for kk, vv in val.items() if vv}
            extracted[k] = len(non_empty)
        else:
            extracted[k] = 0

    fd = state.get("flat_device", {}) or {}
    fct = state.get("flat_cmd_table", {}) or {}
    fs = state.get("flat_serial", {}) or {}
    ff = state.get("flat_framing", {}) or {}

    return {
        "protocol_family": fd.get("protocol_family", "UNKNOWN"),
        "manufacturer": fd.get("manufacturer", ""),
        "model": fd.get("model", ""),
        "has_raw_table": bool(fct.get("raw_table", "").strip()),
        "raw_table_rows": fct.get("raw_table", "").count("\n") if fct.get("raw_table") else 0,
        "baudrate": fs.get("baudrate", ""),
        "encoding": ff.get("encoding", ""),
        "fields_extracted": extracted,
        "total_non_empty": sum(1 for v in extracted.values() if v > 0),
    }


# ── run_manual_understanding ─────────────────────────────────────────────────

async def run_manual_understanding(tool_context: ToolContext) -> dict:
    """Ingest a PDF manual, run PP-StructureV3 OCR, and extract flat data.

    Must be called before any other BUILD tools.
    Returns a summary of what was extracted so you can decide next steps.
    """
    try:
        if tool_context.state.get("manual_assembled_context"):
            summary = _build_flat_summary(tool_context.state)
            return {
                "status": "already_done",
                "message": "Manual already processed. OCR context exists.",
                "summary": summary,
            }

        flow = create_manual_understanding_flow()
        ctx = get_invocation_context(tool_context)
        async for _event in flow.run_async(ctx):
            pass

        summary = _build_flat_summary(tool_context.state)
        issues = []
        if summary["protocol_family"] in ("", "UNKNOWN"):
            issues.append("protocol_family is UNKNOWN — manual may not describe a supported protocol")
        if not summary["has_raw_table"]:
            issues.append("raw_table is empty — no command/register table found")
        if not summary["baudrate"]:
            issues.append("baudrate not found — serial connection may fail")

        result = {"status": "completed", "summary": summary}
        if issues:
            result["warnings"] = issues
            result["message"] = (
                f"OCR complete. {summary['total_non_empty']}/7 fields extracted. "
                f"Issues: {'; '.join(issues)}. "
                "You can still proceed — the code writer will handle missing fields with defaults."
            )
        else:
            result["message"] = (
                f"OCR complete. {summary['total_non_empty']}/7 fields extracted. "
                f"protocol_family={summary['protocol_family']}, "
                f"raw_table has {summary['raw_table_rows']} rows. "
                "Ready for generate_code()."
            )
        return result

    except Exception as exc:
        return {**_error_result(exc)}


# ── generate_code ────────────────────────────────────────────────────────────

async def generate_code(tool_context: ToolContext, missing_functions: list[dict] | None) -> dict:
    """Generate driver code from device_spec.

    Primary path: deterministic generation (no LLM).
    Fallback: LLM-based CodeWriterAgent if deterministic fails.

    Args:
        missing_functions: Unused (kept for API compat). Deterministic generation
                          always generates all functions.

    Returns:
        status, code, class_name, module_name, protocol, coverage
    """
    import pathlib

    from app.agents.build.code_writer_agent import _select_skill

    try:
        s = tool_context.state

        # ── Read device_spec ──────────────────────────────────────
        device_spec = s.get("device_spec", {})
        if not device_spec:
            return {"status": "blocked", "message": "No device_spec found. Call run_manual_understanding() first."}

        ds_device = device_spec.get("device", {})
        ds_protocol = device_spec.get("protocol", {})
        ds_raw_table = device_spec.get("raw_command_table", "")

        protocol_family = (ds_device.get("protocol_family") or "").strip().upper()
        manufacturer = (ds_device.get("manufacturer") or "").strip()
        model = (ds_device.get("model") or "").strip()

        # ── Device identity ───────────────────────────────────────
        if manufacturer and model:
            instrument_type = f"{manufacturer} {model}"
        elif manufacturer:
            instrument_type = manufacturer
        elif model:
            instrument_type = model
        else:
            instrument_type = "unknown_device"
        device_id = instrument_type.lower().replace(" ", "_").replace("-", "_")
        if not all(ord(c) < 128 for c in device_id):
            device_id = "unknown_device"
        s[ACTIVE_DEVICE] = device_id

        # ── Skill selection ───────────────────────────────────────
        skill_name = _select_skill(protocol_family, ds_raw_table, ds_protocol.get("checksum_type", ""))
        s[SELECTED_SKILL] = skill_name

        module_name = f"{device_id}_driver"
        class_name = f"{_to_pascal_case(device_id)}Driver"

        # ── Deterministic generation ──────────────────────────────
        code = None
        generation_method = "deterministic"

        if skill_name:
            template_path = pathlib.Path(__file__).resolve().parent.parent / "skills" / skill_name / "template.py"
            if template_path.exists():
                template_code = template_path.read_text(encoding="utf-8")
                try:
                    from app.codegen.skeleton_generator import generate_driver_code
                    code = generate_driver_code(
                        device_spec=device_spec,
                        template_code=template_code,
                        skill_name=skill_name,
                    )
                except Exception as e:
                    logger.warning("generate_code: deterministic generation failed: %s", e)
                    code = None

        # ── Fallback: LLM-based generation ────────────────────────
        if not code:
            generation_method = "llm"
            code = await _generate_code_with_llm(
                tool_context=tool_context,
                device_spec=device_spec,
                protocol_family=protocol_family,
                instrument_type=instrument_type,
                device_id=device_id,
                skill_name=skill_name,
                missing_functions=missing_functions,
            )

        if not code:
            return {"status": "error", "message": "Code generation failed."}

        # ── Syntax check ──────────────────────────────────────────
        syntax_ok = True
        syntax_error_msg = ""
        try:
            ast.parse(code)
        except SyntaxError as e:
            syntax_ok = False
            syntax_error_msg = str(e)
            logger.warning("generate_code: syntax error: %s", e)

        # ── Save artifact (even if syntax error, so edit_code/merge_code can fix) ──
        ctx = get_invocation_context(tool_context)
        artifact_name = candidate_driver_artifact(device_id)
        from google.genai import types as genai_types
        part = genai_types.Part.from_text(text=code)
        try:
            await ctx.artifact_service.save_artifact(
                app_name=ctx.app_name, user_id=ctx.user_id,
                session_id=ctx.session.id, filename=artifact_name, artifact=part,
            )
        except Exception as e:
            logger.error("generate_code: failed to save artifact: %s", e)

        s[CURRENT_CANDIDATE_CODE] = artifact_name

        if not syntax_ok:
            return {
                "status": "syntax_error",
                "message": f"Syntax error: {syntax_error_msg}. Code saved as candidate — use edit_code() to fix.",
                "code": code,
            }

        # ── Coverage check ────────────────────────────────────────
        coverage = _check_coverage(code, s)

        logger.info("generate_code[%s]: %d chars, %s coverage (%d/%d)",
                     generation_method, len(code), coverage.get("coverage_pct", "?"),
                     coverage["actual_count"], coverage["expected_count"])

        return {
            "status": "ok",
            "code": code,
            "class_name": class_name,
            "module_name": module_name,
            "protocol": protocol_family,
            "coverage": coverage,
            "generation_method": generation_method,
        }

    except Exception as exc:
        return {**_error_result(exc)}


async def _generate_code_with_llm(
    tool_context, device_spec, protocol_family, instrument_type,
    device_id, skill_name, missing_functions,
) -> str | None:
    """Fallback: generate code using LLM CodeWriterAgent."""
    import json
    import pathlib

    from app.agents.build.code_writer_agent import create_code_writer_agent

    s = tool_context.state
    is_retry = bool(missing_functions)
    ds_raw_table = device_spec.get("raw_command_table", "")

    # Build context
    lines: list[str] = []
    if device_spec:
        lines.append("=== device_spec (ground truth from manual) ===\n"
                     + json.dumps(device_spec, ensure_ascii=False, indent=2)[:8000])
    if ds_raw_table and not device_spec.get("raw_command_table"):
        lines.append("=== raw command/register table from the manual ===\n" + ds_raw_table)

    if skill_name and skill_name in SKILL_CACHE and SKILL_CACHE[skill_name]:
        lines.append(f"=== skill: {skill_name} ===\n" + SKILL_CACHE[skill_name])

    if skill_name:
        template_path = pathlib.Path(__file__).resolve().parent.parent / "skills" / skill_name / "template.py"
        if template_path.exists():
            template_code = template_path.read_text(encoding="utf-8")
            lines.append("=== REFERENCE DRIVER TEMPLATE ===\n" + template_code)

    # Instruction
    if is_retry:
        missing_block = []
        for item in missing_functions:
            fn = item.get("function_name", "")
            sig = item.get("signature", "")
            cmd = item.get("command_binding", [])
            purpose = item.get("purpose", "")
            line = f"  - {fn}"
            if sig: line += f"  ({sig})"
            if cmd: line += f"  [command: {', '.join(str(c) for c in cmd)}]"
            if purpose: line += f"  — {purpose}"
            missing_block.append(line)
        lines.insert(0, (
            f"Output ONLY the following missing functions for {instrument_type}.\n"
            + "\n".join(missing_block)
            + "\n\nDo NOT output the full class. Only function definitions."
        ))
    else:
        lines.insert(0, (
            f"Generate a complete Python driver for protocol={protocol_family}, "
            f"device={instrument_type}. Implement EVERY function."
        ))

    s["_cw_context"] = "\n\n".join(lines)
    if is_retry:
        s["_coverage_feedback"] = missing_functions
    else:
        for k in ("_coverage_feedback", "_previous_code"):
            if k in s:
                del s[k]

    # Run agent
    ctx = get_invocation_context(tool_context)
    agent = create_code_writer_agent()
    async for _event in agent.run_async(ctx):
        pass

    # Extract code
    artifact_name = candidate_driver_artifact(device_id)
    from app.agents.shared.artifact_io import load_text_artifact
    code = await load_text_artifact(ctx, artifact_name)
    return code


# ── check_function_coverage ──────────────────────────────────────────────────

async def check_function_coverage(tool_context: ToolContext) -> dict:
    """Verify function coverage of the current candidate code against device_spec.

    Returns:
        all_covered, missing, missing_details, stubs, coverage_pct, etc.
    """
    try:
        s = tool_context.state

        if not s.get("device_spec"):
            return {"status": "blocked", "message": "No device_spec found. Call run_manual_understanding() first."}

        artifact_name = candidate_driver_artifact(s.get(ACTIVE_DEVICE, "unknown_device"))
        ctx = get_invocation_context(tool_context)
        from app.agents.shared.artifact_io import load_text_artifact
        code = await load_text_artifact(ctx, artifact_name)

        if not code:
            return {"status": "blocked", "message": "No candidate code found. Call generate_code() first."}

        coverage = _check_coverage(code, s)
        return {"status": "ok", **coverage}

    except Exception as exc:
        return {**_error_result(exc)}


def _check_coverage(code: str, state: dict) -> dict:
    """Check whether generated code covers all expected functions.

    Returns dict with keys: all_covered, missing, missing_details, stubs,
    expected_count, actual_count, extra, coverage_pct.
    """
    # Expected functions: from device_spec (ground truth)
    expected: set[str] = set()
    func_details: dict[str, dict] = {}
    device_spec = state.get("device_spec", {}) or {}
    ds_functions = device_spec.get("functions", [])
    if ds_functions:
        for f in ds_functions:
            name = f.get("function_name", "")
            if name:
                expected.add(name)
                func_details[name] = f
    else:
        raw_table = state.get("flat_cmd_table", {}).get("raw_table", "")
        fd = state.get("flat_device", {}) or {}
        protocol_family = (fd.get("protocol_family") or "").strip().upper()
        if raw_table:
            parsed = _parse_command_table_to_functions(raw_table, protocol_family)
            for name, f in parsed.get("functions", {}).items():
                expected.add(name)
                func_details[name] = f

    # Actual functions via AST
    actual: set[str] = set()
    stubs: list[str] = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                actual.add(node.name)
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, (ast.Constant, ast.Str)):
                    body = body[1:]
                def _is_stub(stmt):
                    if isinstance(stmt, ast.Pass):
                        return True
                    if isinstance(stmt, ast.Raise) and stmt.exc:
                        if isinstance(stmt.exc, ast.Name) and stmt.exc.id == "NotImplementedError":
                            return True
                    return False
                if body and all(_is_stub(s) for s in body):
                    stubs.append(node.name)
    except SyntaxError:
        _methods = re.findall(r"^\s*def\s+(\w+)\s*\(self", code, re.MULTILINE)
        actual = {n for n in _methods if not n.startswith("_")}

    # Fuzzy matching
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        actual_stems = {_stem(a): a for a in actual}
        still_missing = []
        for fn in missing:
            fn_stem = _stem(fn)
            if fn_stem not in actual_stems:
                still_missing.append(fn)
        missing = still_missing

    # Build detailed info for missing functions
    missing_details = []
    for fn in missing:
        detail = func_details.get(fn, {})
        missing_details.append({
            "function_name": fn,
            "signature": detail.get("signature", ""),
            "purpose": detail.get("purpose", ""),
            "category": detail.get("function_category", ""),
            "command_binding": detail.get("protocol_action_binding", []),
            "constraints": detail.get("parameter_constraints", {}),
        })

    total = len(actual) + len(missing)
    pct = (len(actual) / total * 100) if total > 0 else 100

    return {
        "all_covered": len(missing) == 0,
        "missing": missing,
        "missing_details": missing_details,
        "stubs": stubs,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "extra": extra,
        "coverage_pct": f"{pct:.1f}%",
    }


# ── merge_code ───────────────────────────────────────────────────────────────

async def merge_code(tool_context: ToolContext, new_functions_code: str) -> dict:
    """AST-merge new function definitions into the existing candidate code.

    Use after generate_code() returns missing functions in retry mode.
    New functions are merged into the existing class body via AST.

    Args:
        new_functions_code: Python code with new function definitions (def ...)

    Returns:
        status, code (merged), merged_count, coverage
    """
    try:
        s = tool_context.state
        device_id = s.get(ACTIVE_DEVICE, "unknown_device")
        artifact_name = candidate_driver_artifact(device_id)

        # Load existing code
        ctx = get_invocation_context(tool_context)
        from app.agents.shared.artifact_io import load_text_artifact
        existing_code = await load_text_artifact(ctx, artifact_name)

        if not existing_code:
            return {"status": "blocked", "message": "No existing candidate code. Call generate_code() first."}

        # Merge via AST
        merged = _ast_merge_functions(existing_code, new_functions_code)
        if merged is None:
            return {"status": "error", "message": "AST merge failed — no new functions found or syntax error."}

        # Validate syntax
        try:
            ast.parse(merged)
        except SyntaxError as e:
            logger.warning("merge_code: merged code has syntax error: %s", e)
            return {"status": "syntax_error", "message": f"Merged code has syntax error: {e}", "code": merged}

        # Count merged functions
        try:
            new_tree = ast.parse(new_functions_code)
            new_count = sum(1 for node in ast.walk(new_tree)
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not node.name.startswith("_"))
        except SyntaxError:
            new_count = 0

        # Save merged code as artifact
        from google.genai import types as genai_types
        part = genai_types.Part.from_text(text=merged)
        try:
            await tool_context.save_artifact(artifact_name, part)
        except Exception as e:
            logger.error("merge_code: failed to save artifact: %s", e)

        s[CURRENT_CANDIDATE_CODE] = artifact_name
        s["_previous_code"] = merged

        # Check coverage after merge
        coverage = _check_coverage(merged, s)

        logger.info("merge_code: merged %d functions, total %d chars, coverage %s",
                     new_count, len(merged), coverage["coverage_pct"])

        return {
            "status": "ok",
            "code": merged,
            "merged_count": new_count,
            "coverage": coverage,
        }

    except Exception as exc:
        return {**_error_result(exc)}


def _ast_merge_functions(existing_code: str, new_functions_code: str) -> str | None:
    """Merge new function definitions into an existing class body.

    Extracts function definitions from new_functions_code using AST,
    then appends them to the first class body in existing_code.
    Handles class-body indented functions (4-space indent from agent output).

    Returns merged code, or None if merge fails.
    """
    try:
        existing_tree = ast.parse(existing_code)
        target_class = None
        # Find the driver class (has methods, not just an exception class)
        for node in ast.walk(existing_tree):
            if isinstance(node, ast.ClassDef):
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if len(methods) >= 2:  # Has at least __init__ + one method
                    target_class = node
                    break
        # Fallback: any class with methods
        if not target_class:
            for node in ast.walk(existing_tree):
                if isinstance(node, ast.ClassDef):
                    methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    if methods:
                        target_class = node
                        break
        # Last resort: any class
        if not target_class:
            for node in ast.walk(existing_tree):
                if isinstance(node, ast.ClassDef):
                    target_class = node
                    break
        if not target_class:
            return None

        existing_names = set()
        for node in ast.walk(existing_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                existing_names.add(node.name)

        line_offset = 0
        try:
            new_tree = ast.parse(new_functions_code)
        except SyntaxError:
            new_tree = ast.parse("class _Dummy:\n" + new_functions_code)
            line_offset = 1

        new_funcs = [
            node for node in ast.walk(new_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name != "_Dummy"
        ]
        if not new_funcs:
            return None

        insert_at = target_class.end_lineno

        new_lines = new_functions_code.split("\n")
        funcs_to_add = []
        for func in new_funcs:
            if func.name in existing_names:
                continue
            start = func.lineno - 1 - line_offset
            end = func.end_lineno - line_offset
            func_source = "\n".join(new_lines[start:end])
            funcs_to_add.append(func_source)

        if not funcs_to_add:
            return None

        existing_lines = existing_code.split("\n")
        class_body_indent = "    "
        in_class = False
        for line in existing_lines[target_class.lineno - 1:]:
            if line.strip().startswith("class "):
                in_class = True
                continue
            if in_class and line.strip() and not line.strip().startswith("#"):
                class_body_indent = line[:len(line) - len(line.lstrip())]
                break

        agent_base_indent = ""
        for line in funcs_to_add[0].split("\n"):
            if line.strip():
                agent_base_indent = line[:len(line) - len(line.lstrip())]
                break

        new_func_lines = []
        for func_src in funcs_to_add:
            new_func_lines.append("")
            for line in func_src.split("\n"):
                if line.strip():
                    if line.startswith(agent_base_indent):
                        relative = line[len(agent_base_indent):]
                    else:
                        relative = line.lstrip()
                    new_func_lines.append(class_body_indent + relative)
                else:
                    new_func_lines.append("")

        merged_lines = existing_lines[:insert_at] + new_func_lines + existing_lines[insert_at:]
        merged = "\n".join(merged_lines)

        ast.parse(merged)
        return merged

    except (SyntaxError, ValueError, AttributeError):
        return None
