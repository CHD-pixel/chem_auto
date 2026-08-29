"""CodeWriterAgent — generates candidate driver code from blueprint intermediates + flat data."""

from __future__ import annotations

import json
import logging
import pathlib
import re
from collections.abc import Callable

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from app.constants.artifact_names import candidate_driver_artifact
from app.constants.state_keys import (
    ACTION_METHODS_MAPPING, ACTIVE_DEVICE,
    CORE_BLUEPRINT, CURRENT_CANDIDATE_CODE, SELECTED_SKILL, VALIDATION_AND_TEST,
)
from app.services.skill_cache import SKILL_CACHE
from app.llm.client_factory import build_llm
from app.runtime.config import MULTIMODAL_MODEL

logger = logging.getLogger(__name__)

_PROMPT_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "prompts" / "build" / "code_writer.md"
_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

_CODE_FENCE_RE = re.compile(r"^\s*```(?:python|py)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)

# ── Skill selection by protocol layer ──────────────────────────────────────

_PROTOCOL_TO_SKILL: dict[str, str] = {
    "ASCII": "ascii-packet-device",
    "SCPI": "scpi-generic",
    "MODBUS": "modbus-generic",
    "MODBUS-RTU": "modbus-generic",
    "MODBUS-TCP": "modbus-generic",
    "LXI": "lxi-scpi",
    "GCODE": "gcode-generic",
    "CANOPEN": "canopen-generic",
    "BINARY": "binary-frame",
}


def _select_skill(protocol_layer: str, raw_table: str = "", framing_checksum: str = "") -> str:
    """Select ADK skill by protocol. Falls back to raw_table/framing hints if protocol_layer is UNKNOWN."""
    # 1. Primary: flat_device.protocol_family
    key = protocol_layer.upper().replace("_", "-") if protocol_layer else ""
    if key and key != "UNKNOWN":
        match = _PROTOCOL_TO_SKILL.get(key, "")
        if match:
            return match

    # 2. Fallback: keyword scan in raw command/register table
    if raw_table:
        upper = raw_table.upper()
        for proto, skill in _PROTOCOL_TO_SKILL.items():
            if proto in ("LXI",):  # LXI needs SCPI sub-detection
                continue
            if proto.replace("-", "").replace("_", "") in upper.replace("-", "").replace("_", ""):
                return skill
        # Specific aliases
        if "MODBUS" in upper or "RTU" in upper:
            return _PROTOCOL_TO_SKILL.get("MODBUS", "")
        if "G-CODE" in upper or "GCODE" in upper or '"G"' in upper:
            return _PROTOCOL_TO_SKILL.get("GCODE", "")
        if "CANOPEN" in upper or "CAN OPEN" in upper:
            return _PROTOCOL_TO_SKILL.get("CANOPEN", "")
        if "JSON-RPC" in upper or "JSONRPC" in upper:
            return ""  # No dedicated skill yet — will generate generic transport skeleton

    # 3. Fallback: checksum type hint
    if framing_checksum == "crc16_modbus":
        return "modbus-generic"

    return ""


# ── Fallback: parse raw command table into function definitions ────────────

def _looks_numeric(s: str) -> bool:
    s = s.strip().replace("0x", "").replace("0X", "")
    return bool(s) and all(c.isdigit() or c in "xabcdefABCDEF" for c in s)


def _make_func(name: str, desc: str, cat: str, side: str, cmd_id: str, test_role: str,
               param_constraints: dict[str, Any] | None = None) -> dict:
    signature = f"def {name}(self) -> Any" if cat == "read" else f"def {name}(self, value) -> None"
    result = {
        "function_name": name,
        "purpose": desc,
        "signature": signature,
        "function_category": cat,
        "side_effect_level": side,
        "protocol_action_binding": [str(cmd_id)],
        "implementation_strategy": "direct_command",
    }
    if param_constraints:
        result["parameter_constraints"] = param_constraints
    return result


def _parse_range_to_constraint(range_str: str) -> dict[str, Any] | None:
    """Parse a range string from the command table into parameter constraints.

    Handles:
      - "0.1-600rpm"                    → min/max + unit
      - "13,14,15,...116"              → allowed_values (comma-separated list)
      - "1：启动 0：停止"              → allowed_values (extracts numbers before ：)
      - "对应参数见表1"                 → None (cross-reference, needs LLM)
    """
    r = range_str.strip()
    if not r or r.startswith("对应参数见") or r.startswith("参见"):
        return None

    # Extract unit suffix (CJK units like 度, 秒, 分, 转, etc.)
    import re as _re
    unit = ""
    # Match trailing unit: Latin units (rpm, mL, C, etc.) or CJK units (度, 秒, 转, ...)
    unit_match = _re.search(r'(?:[a-zA-Z/°℃℉]+(?:/[a-zA-Z]+)?|[一-鿿]+)\s*$', r)
    if unit_match:
        unit = unit_match.group().strip()
        r = r[:unit_match.start()].strip()

    # Strip tolerance notation (±x) before parsing
    r = _re.sub(r'\s*[±]\s*\d+(\.\d+)?\s*$', '', r)

    # Comma-separated values → allowed_values (numeric or string)
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
                        vals.append(token)  # string value like "A", "b"
        if len(vals) >= 2:
            # Check for mixed numeric + string (LLM error: "0,50-1500")
            strs_in_vals = [v for v in vals if isinstance(v, str)]
            nums_in_vals = [v for v in vals if isinstance(v, (int, float))]
            if strs_in_vals:
                # Try to parse any string token as a range
                for sv in strs_in_vals:
                    dm = _re.match(r'^(-?[\d.]+)\s*-\s*(-?[\d.]+)$', str(sv))
                    if dm:
                        return {
                            "min_value": float(dm.group(1)),
                            "max_value": float(dm.group(2)),
                            "allowed_values": nums_in_vals if nums_in_vals else None,
                            "unit": unit,
                        }
                # Pure string list (e.g. "A,b,d")
                if not nums_in_vals:
                    return {"allowed_values": vals, "unit": unit}
            return {"allowed_values": vals, "unit": unit}
        # Try extracting range from comma-split tokens
        for token in r.split(","):
            token = token.strip()
            dm = _re.match(r'^(-?[\d.]+)\s*-\s*(-?[\d.]+)$', token)
            if dm:
                return {
                    "min_value": float(dm.group(1)),
                    "max_value": float(dm.group(2)),
                    "unit": unit,
                }
        return None

    # Dash-separated range → min/max (e.g. "0.1-600", "0-99999")
    dash_match = _re.match(r'^(-?[\d.]+)\s*-\s*(-?[\d.]+)$', r)
    if dash_match:
        lo = float(dash_match.group(1))
        hi = float(dash_match.group(2))
        return {
            "min_value": lo if lo == int(lo) else lo,
            "max_value": hi if hi == int(hi) else hi,
            "unit": unit,
        }

    # Number-colon pattern: "1：启动 0：停止2：暂停"
    colon_nums = _re.findall(r'(\d+)\s*[：:]', r)
    if colon_nums:
        nums = [int(n) for n in colon_nums]
        return {"allowed_values": nums, "unit": unit}

    return None


def _parse_command_table_to_functions(raw_table: str, protocol_family: str = "") -> dict:
    functions: dict = {}
    if not raw_table.strip():
        return {"functions": functions}

    is_modbus = protocol_family.upper().replace("_", "-") in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP")

    # Split on both real newlines and literal \\n (from JSON string encoding)
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
            # Strip leading/trailing empty elements from Markdown table format
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

        # Normalize parenthesized names: "calibration_data_(fitting_coefficients)"
        # → "calibration_data_fitting_coefficients"
        # The code generator strips parentheses, so we must match that convention.
        import re as _re_name
        safe_name = _re_name.sub(r'\(([^)]+)\)', r'_\1', safe_name)
        safe_name = _re_name.sub(r'_+', '_', safe_name).strip("_")

        # Strip existing verb prefixes to avoid double-prefixing.
        # "Get Module Version" → "module_version"
        # "Set Temperature" → "temperature"
        # "Read Set Temperature" → "set_temperature" (stripped "read_", kept "set_" as it's part of the name)
        _verb_prefixes = ("get_", "set_", "read_", "write_", "start_", "stop_")
        for _vp in _verb_prefixes:
            if safe_name.startswith(_vp):
                safe_name = safe_name[len(_vp):]
                break

        # Sanitize: if the name is non-ASCII, build a fallback from the
        # command ID and any English text in later columns.
        if not all(ord(c) < 128 for c in safe_name):
            # Try to extract an English name from the description or later columns
            eng_hint = ""
            for pi in range(3, len(parts)):
                tok = parts[pi]
                if all(ord(c) < 128 for c in tok) and len(tok) > 2:
                    eng_hint = tok.lower().replace(" ", "_").replace("-", "_").replace("/", "_")
                    break
            if eng_hint and not eng_hint.startswith(("unsigned", "float", "int", "0x")):
                safe_name = eng_hint
            else:
                # Fallback: use register/command ID
                safe_name = f"reg_{cmd_id.lower().replace('0x', '').replace(' ', '_')}"

        is_read = any(k in rw_hint.lower() for k in ("r", "ro", "读", "read", "rd"))
        is_write = any(k in rw_hint.lower() for k in ("w", "wo", "写", "write", "wr", "rw"))
        if not is_read and not is_write:
            if is_modbus and _looks_numeric(cmd_id):
                is_read = True
            else:
                is_read = True

        func_cat = "read" if (is_read and not is_write) else "control"
        side_effect = "none" if func_cat == "read" else "medium"
        desc = parts[3] if len(parts) > 3 else name

        # Try to extract parameter constraints from later columns.
        # Column order varies by OCR output; scan remaining parts for
        # range-like patterns (dash-separated, comma-separated, or colon-separated).
        param_constraints = None
        for pi in range(2, len(parts)):
            candidate = parts[pi]
            constraint = _parse_range_to_constraint(candidate)
            if constraint is not None:
                param_constraints = {"value": constraint}
                break

        # Use safe_name directly — no get_/set_ prefix.
        # The raw table's name column already contains the full function name.
        # read/write distinction is captured by function_category, not by name prefix.
        # This avoids double-prefixing (get_get_xxx) and name collisions.
        if safe_name not in functions:
            functions[safe_name] = _make_func(safe_name, desc, func_cat, side_effect, cmd_id,
                                              "functional" if func_cat == "read" else "safety",
                                              param_constraints=param_constraints)

    return {"functions": functions}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _to_pascal_case(snake: str) -> str:
    return "".join(word.capitalize() for word in snake.split("_") if word)


# ── Code writer ────────────────────────────────────────────────────────────

def _extract_code(text: str) -> str:
    stripped = text.strip()
    m = _CODE_FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip()
    return stripped


def _make_blueprint_injector() -> Callable:
    """Build context injection callback for CodeWriterAgent.

    If _cw_context is pre-built in state (from generate_code tool), uses it directly.
    Otherwise falls back to building context from device_spec/flat_* keys.
    """

    async def _inject(
        callback_context: CallbackContext, llm_request: LlmRequest,
    ) -> LlmResponse | None:
        # Strip non-text parts that text-only models reject
        from app.callbacks.file_filter import filter_file_parts
        await filter_file_parts(callback_context, llm_request)

        s = callback_context.state

        # ── Fast path: pre-built context from generate_code tool ──
        prebuilt = s.get("_cw_context", "")
        if prebuilt:
            llm_request.contents.insert(
                0,
                genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prebuilt)]),
            )
            return None

        # ── Fallback: build context from state (standalone use) ───
        device_spec = s.get("device_spec", {}) or {}
        if not device_spec:
            from app.agents.build.manual_understanding_flow import _build_device_spec
            device_spec = _build_device_spec(s)
            s["device_spec"] = device_spec
        ds_device = device_spec.get("device", {})
        ds_connection = device_spec.get("connection", {})
        ds_protocol = device_spec.get("protocol", {})
        ds_functions = device_spec.get("functions", [])
        ds_raw_table = device_spec.get("raw_command_table", "")

        fd = ds_device or s.get("flat_device", {}) or {}
        protocol_family = (fd.get("protocol_family") or "").strip().upper()
        manufacturer = (fd.get("manufacturer") or "").strip()
        model = (fd.get("model") or "").strip()
        raw_table = ds_raw_table or s.get("flat_cmd_table", {}).get("raw_table", "")
        ff = ds_protocol or s.get("flat_framing", {}) or {}

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

        skill_name = _select_skill(protocol_family, raw_table, ff.get("checksum_type", ""))
        s[SELECTED_SKILL] = skill_name

        lines: list[str] = []
        if device_spec:
            lines.append("=== device_spec (ground truth from manual) ===\n"
                         + json.dumps(device_spec, ensure_ascii=False, indent=2)[:8000])
        if raw_table and not ds_raw_table:
            lines.append("=== raw command/register table from the manual ===\n" + raw_table)

        action_methods = {}
        if ds_functions:
            for func in ds_functions:
                fname = func.get("function_name", "")
                if fname:
                    action_methods[fname] = func
        elif raw_table:
            parsed = _parse_command_table_to_functions(raw_table, protocol_family)
            action_methods = parsed.get("functions", {})

        module_name = f"{device_id}_driver"
        class_name = f"{_to_pascal_case(device_id)}Driver"
        driver_blueprint = {
            "action_methods": action_methods,
            "lifecycle_sequences": {},
            "module_name": module_name,
            "driver_class_name": class_name,
            "base_protocol_style": protocol_family.lower() if protocol_family else "unknown",
            "transport_config": ds_connection or {"transport_type": "serial"},
            "core_methods": [],
            "protocol_helpers": _build_binary_protocol_helpers(ff) if protocol_family == "BINARY" and ff else [],
        }

        blueprint = {
            "schema_version": "0.1.0",
            "source_agent": "CodeArchitectAgent",
            "instrument_type": instrument_type,
            "protocol_layer": protocol_family or "UNKNOWN",
            "confidence": 0.5,
            "driver_blueprint": driver_blueprint,
            "validation_policy": {},
        }
        lines.append("=== build_blueprint ===\n" + json.dumps(blueprint, ensure_ascii=False, indent=2))

        if skill_name and skill_name in SKILL_CACHE and SKILL_CACHE[skill_name]:
            lines.append(f"=== skill: {skill_name} ===\n" + SKILL_CACHE[skill_name])

        if skill_name:
            template_path = pathlib.Path(__file__).resolve().parent.parent / "skills" / skill_name / "template.py"
            if template_path.exists():
                template_code = template_path.read_text(encoding="utf-8")
                lines.append(
                    "=== REFERENCE DRIVER TEMPLATE ===\n"
                    "Follow this structure for the protocol type.\n"
                    "Generate the actual device functions based on the command table above.\n\n"
                    + template_code
                )

        final_class_name = driver_blueprint.get("driver_class_name", class_name)
        lines.insert(0, (
            f"Generate a complete Python driver for protocol={protocol_family or 'UNKNOWN'}, "
            f"device={instrument_type}. "
            f"Module name: {module_name}.py, Driver class: {final_class_name}. "
            "The device_spec above contains ALL function definitions and protocol details. "
            "Implement EVERY function listed in device_spec.functions."
        ))

        llm_request.contents.insert(
            0,
            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="\n\n".join(lines))]),
        )
        return None

    return _inject


def _build_binary_protocol_helpers(ff: dict) -> list[dict]:
    """Build deterministic protocol helpers for binary protocols."""
    header_hex = ff.get("header_hex", "AA55")
    header_display = " ".join(f"0x{header_hex[i:i+2].upper()}" for i in range(0, len(header_hex), 2))
    length_size = int(ff.get("length_field_size", 2))
    byte_order = ff.get("byte_order", "big-endian")
    bo_prefix = ">" if "big" in byte_order.lower() else "<"
    checksum_type = ff.get("checksum_type", "additive")
    checksum_bytes = int(ff.get("checksum_bytes", 1))
    length_semantics = ff.get("length_semantics", "")

    if "header_to_checksum" in length_semantics:
        length_desc = f"Length counts from length field through checksum: {length_size}(length) + len(cmd+data) + {checksum_bytes}(checksum)"
        remaining_desc = f"remaining = length_value - {length_size}"
    else:
        length_desc = f"Length = len(cmd+data) + {checksum_bytes}(checksum)"
        remaining_desc = "remaining = length_value"

    if checksum_type == "additive" and checksum_bytes == 1:
        csum_algo = "sum(length_bytes + cmd + data) & 0xFF"
    elif checksum_type == "xor":
        csum_algo = "XOR of all bytes from length through data"
    elif checksum_type == "crc16":
        csum_algo = "CRC16 of all bytes from length through data"
    else:
        csum_algo = f"{checksum_type}, {checksum_bytes} byte(s)"

    return [
        {"helper_name": "build_packet", "purpose": "Construct command packet",
         "implementation_notes": [f"Frame: [{header_display}] [Length({length_size}B, {bo_prefix})] [Cmd(1B)] [Data(N)] [Checksum({checksum_bytes}B)]", length_desc, f"Checksum = {csum_algo}"]},
        {"helper_name": "parse_packet", "purpose": "Parse response packet",
         "implementation_notes": [f"Response: [{header_display}] [Length({length_size}B, {bo_prefix})] [Cmd/Status(1B)] [Data(N)] [Checksum({checksum_bytes}B)]", f"Step 1: read header, verify magic", f"Step 2: read length ({length_size}B), decode {bo_prefix} uint{length_size*8}", f"Step 3: {remaining_desc}", "Step 4: read remaining; first=cmd/status, middle=data, last=checksum", f"Step 5: verify checksum = {csum_algo}"]},
        {"helper_name": "compute_checksum", "purpose": f"Compute {checksum_bytes}-byte {checksum_type} checksum",
         "implementation_notes": [f"Algorithm: {checksum_type}", f"Input: length field through data (excl header and checksum)", f"Output: {checksum_bytes} byte(s)"]},
    ]


async def _save_code_callback(
    callback_context: CallbackContext, llm_response: LlmResponse,
) -> LlmResponse | None:
    if not llm_response.content or not llm_response.content.parts:
        logger.warning("CodeWriterAgent: empty LLM response, no code to save.")
        return None

    text = "".join(
        p.text for p in llm_response.content.parts if getattr(p, "text", None)
    )
    if not text.strip():
        logger.warning("CodeWriterAgent: LLM response contains no text parts.")
        return None

    code = _extract_code(text)

    # Note: pyvisa vs pyserial is determined by the skill template.
    # Serial devices use pyserial, network/LXI/GPIB devices use pyvisa.
    # The template's connect() pattern tells the LLM which to use.
    # Do NOT force-convert here — it would break LXI/GPIB drivers.

    # Fix COM prefix duplication: "COMCOM11" → "COM11"
    code = code.replace("COMCOM", "COM")

    # Post-generation fix: ensure serial config attributes exist as instance vars.
    # The LLM sometimes defines _DEFAULT_BAUDRATE as a class attribute but uses
    # self._baudrate in connect() without assigning it in __init__.
    _SERIAL_ATTRS = {
        "_baudrate": ("_DEFAULT_BAUDRATE", "9600"),
        "_bytesize": ("_DEFAULT_DATABITS", "8"),
        "_parity": ("_DEFAULT_PARITY", "serial.PARITY_NONE"),
        "_stopbits": ("_DEFAULT_STOPBITS", "serial.STOPBITS_ONE"),
        "_timeout": ("_DEFAULT_TIMEOUT", "2.0"),
    }
    code_lines = code.split('\n')
    for attr, (class_default, fallback_value) in _SERIAL_ATTRS.items():
        # Only fix if self.<attr> is used somewhere in the code
        if f"self.{attr}" not in code:
            continue
        # Check if it's already assigned somewhere (self.<attr> = ...)
        already_assigned = any(f"self.{attr} =" in line or f"self.{attr}=" in line for line in code_lines)
        if already_assigned:
            continue
        # Determine value: use class default if it exists, else fallback
        has_class_default = any(class_default in line for line in code_lines)
        value = f"self.{class_default}" if has_class_default else fallback_value
        # Find the __init__ method and insert assignment after its first body line
        in_init = False
        init_indent = ""
        insert_idx = None
        for i, line in enumerate(code_lines):
            stripped = line.strip()
            if 'def __init__(' in line:
                in_init = True
                continue
            if in_init:
                if stripped == '' or stripped.startswith('#'):
                    continue
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    # Skip docstring
                    if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                        continue  # single-line docstring
                    # Multi-line docstring — find end
                    quote = '"""' if '"""' in stripped else "'''"
                    for j in range(i, len(code_lines)):
                        if quote in code_lines[j] and j > i:
                            i = j
                            break
                    continue
                # First real code line in __init__
                # Detect its indentation
                init_indent = line[:len(line) - len(line.lstrip())]
                insert_idx = i
                break
        if insert_idx is not None:
            code_lines.insert(insert_idx, f"{init_indent}self.{attr} = {value}")
    code = '\n'.join(code_lines)

    # Post-generation fix: @notation commands should not have extra space-separated params.
    # If a function sends `CMD@{expr} {expr}` where the blueprint has extra params,
    # collapse to `CMD@{expr}` and remove the extra param from the signature.
    import re as _re_at
    # Find functions that use @notation with extra space-separated args
    # Pattern: f"OUT_SP_12@{mode} {value}" → f"OUT_SP_12@{value}"
    _at_extra_re = _re_at.compile(
        r'(f["\'])(\w+@\{)\w+\}\s+\{(\w+)\}(["\'])'
    )
    code = _at_extra_re.sub(r'\1\2\3}\4', code)
    # Also handle non-f-string: "OUT_SP_12@" + mode + " " + value → "OUT_SP_12@" + value
    # (less common, skip for now)

    # Post-generation validation: check for syntax errors
    import ast as _ast
    try:
        _ast.parse(code)
    except SyntaxError as syn_err:
        logger.warning("CodeWriterAgent: generated code has syntax error: %s", syn_err)
        # Don't silently truncate — the retry loop in generate_code
        # will catch this and regenerate. Log the error for diagnostics.

    # Validate that the code has a class definition
    if "class " not in code:
        logger.warning("CodeWriterAgent: generated code has no class definition")

    # Validate that the code has a connect method
    if "def connect" not in code:
        logger.warning("CodeWriterAgent: generated code has no connect method")

    device_id = callback_context.state.get(ACTIVE_DEVICE, "unknown_device")
    artifact_name = candidate_driver_artifact(device_id)

    part = genai_types.Part.from_text(text=code)
    try:
        version = await callback_context.save_artifact(artifact_name, part)
    except (ValueError, OSError) as exc:
        logger.error("CodeWriterAgent: failed to save artifact: %s", exc)
        return None

    callback_context.state[CURRENT_CANDIDATE_CODE] = artifact_name
    logger.info(
        "CodeWriterAgent: saved candidate driver as %r (version %d, %d chars)",
        artifact_name, version, len(code),
    )
    return None


def create_code_writer_agent() -> Agent:
    return Agent(
        name="code_writer",
        model=build_llm(MULTIMODAL_MODEL),
        description="Generates a complete Python candidate driver from blueprint intermediates and flat extraction data.",
        instruction=_PROMPT,
        before_model_callback=_make_blueprint_injector(),
        after_model_callback=_save_code_callback,
        generate_content_config=genai_types.GenerateContentConfig(
            max_output_tokens=200000,
        ),
    )
