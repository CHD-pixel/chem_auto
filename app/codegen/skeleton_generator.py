"""Deterministic driver code generator.

Generates complete Python driver code from device_spec + protocol template.
No LLM involved — pure template substitution.

Usage:
    code = generate_driver_code(device_spec, template_code, skill_name)
"""

from __future__ import annotations

import re
import textwrap
from typing import Any


# ── Return type mapping ──────────────────────────────────────────────────

_RETURN_TYPE_MAP = {
    "float": "float",
    "int": "int",
    "str": "str",
    "bool": "bool",
    "any": "Any",
    "none": "None",
}


def _parse_return_type(signature: str) -> str:
    """Extract return type annotation from signature string."""
    m = re.search(r'->\s*(\w+)', signature)
    if m:
        raw = m.group(1).lower()
        return _RETURN_TYPE_MAP.get(raw, "Any")
    return "Any"


def _parse_param_names(signature: str) -> list[str]:
    """Extract parameter names (excluding self) from signature string."""
    m = re.search(r'\(([^)]+)\)', signature)
    if not m:
        return []
    params = []
    for part in m.group(1).split(","):
        name = part.strip().split(":")[0].strip().split("=")[0].strip()
        if name and name != "self":
            params.append(name)
    return params


# ── Function body generators ─────────────────────────────────────────────

def _generate_read_body(
    protocol_family: str,
    command: str,
    return_type: str,
) -> list[str]:
    """Generate function body lines for a read function."""
    pf = protocol_family.upper()

    if pf in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP"):
        return _generate_modbus_read_body(command, return_type)
    elif pf in ("BINARY",):
        return _generate_binary_read_body(command, return_type)
    elif pf in ("SCPI", "LXI"):
        return _generate_scpi_read_body(command, return_type)
    elif pf in ("ASCII",):
        return _generate_ascii_read_body(command, return_type)
    else:
        return _generate_ascii_read_body(command, return_type)


def _generate_write_body(
    protocol_family: str,
    command: str,
    param_name: str,
    param_constraints: dict | None,
) -> list[str]:
    """Generate function body lines for a write function."""
    pf = protocol_family.upper()

    # Range check
    range_check = _generate_range_check(param_name, param_constraints)

    if pf in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP"):
        return range_check + _generate_modbus_write_body(command, param_name)
    elif pf in ("BINARY",):
        return range_check + _generate_binary_write_body(command, param_name)
    elif pf in ("SCPI", "LXI"):
        return range_check + _generate_scpi_write_body(command, param_name)
    elif pf in ("ASCII",):
        return range_check + _generate_ascii_write_body(command, param_name)
    else:
        return range_check + _generate_ascii_write_body(command, param_name)


def _generate_range_check(param_name: str, constraints: dict | None) -> list[str]:
    """Generate parameter range check lines."""
    if not constraints:
        return []

    allowed = constraints.get("allowed_values")
    min_val = constraints.get("min_value")
    max_val = constraints.get("max_value")
    unit = constraints.get("unit", "")

    if allowed and isinstance(allowed, list) and len(allowed) <= 10:
        return [f"        if {param_name} not in {allowed}:",
                f"            raise SafetyError(f\"Value {{{param_name}}} not in allowed {allowed}\")"]
    if min_val is not None or max_val is not None:
        lines = []
        if min_val is not None:
            lines.append(f"        if {param_name} < {min_val}:")
            lines.append(f"            raise SafetyError(f\"Value {{{param_name}}} below minimum {min_val}\")")
        if max_val is not None:
            lines.append(f"        if {param_name} > {max_val}:")
            lines.append(f"            raise SafetyError(f\"Value {{{param_name}}} above maximum {max_val}\")")
        return lines
    return []


# ── MODBUS bodies ────────────────────────────────────────────────────────

def _generate_modbus_read_body(command: str, return_type: str) -> list[str]:
    """MODBUS read: use minimalmodbus helpers."""
    try:
        addr = int(command, 0) if isinstance(command, str) else int(command)
    except (ValueError, TypeError):
        addr = command

    if return_type == "float":
        return [f"        return self._read_float32({addr})"]
    elif return_type == "int":
        return [f"        return self._read_register({addr})"]
    elif return_type == "str":
        return [f"        return self._read_string({addr}, 16)"]
    else:
        return [f"        return self._read_register({addr})"]


def _generate_modbus_write_body(command: str, param_name: str) -> list[str]:
    """MODBUS write: use minimalmodbus helpers."""
    try:
        addr = int(command, 0) if isinstance(command, str) else int(command)
    except (ValueError, TypeError):
        addr = command

    return [f"        self._write_register({addr}, {param_name})"]


# ── Binary bodies ────────────────────────────────────────────────────────

def _generate_binary_read_body(command: str, return_type: str) -> list[str]:
    """Binary read: send_recv + parse."""
    cmd_hex = _to_int_literal(command)

    if return_type == "float":
        return [f"        data = self.send_recv({cmd_hex})",
                "        return self._parse_float32(data)"]
    elif return_type == "int":
        return [f"        data = self.send_recv({cmd_hex})",
                "        return self._parse_uint16(data)"]
    elif return_type == "str":
        return [f"        data = self.send_recv({cmd_hex})",
                "        return data.decode('utf-8', errors='replace').strip()"]
    else:
        return [f"        data = self.send_recv({cmd_hex})",
                "        return self._parse_uint16(data)"]


def _generate_binary_write_body(command: str, param_name: str) -> list[str]:
    """Binary write: send_recv with packed data."""
    cmd_hex = _to_int_literal(command)

    return [f"        self.send_recv({cmd_hex}, self._pack_uint16({param_name}))"]


# ── SCPI bodies ──────────────────────────────────────────────────────────

def _generate_scpi_read_body(command: str, return_type: str) -> list[str]:
    """SCPI read: send query command, parse response."""
    cmd = command.strip()
    if not cmd.endswith("?"):
        cmd += "?"

    if return_type == "float":
        return [f"        response = self.send_command(\"{cmd}\")",
                "        return float(response)"]
    elif return_type == "int":
        return [f"        response = self.send_command(\"{cmd}\")",
                "        return int(response)"]
    elif return_type == "str":
        return [f"        return self.send_command(\"{cmd}\")"]
    elif return_type == "bool":
        return [f"        response = self.send_command(\"{cmd}\")",
                "        return response.strip().upper() in ('1', 'ON', 'TRUE')"]
    else:
        return [f"        return self.send_command(\"{cmd}\")"]


def _generate_scpi_write_body(command: str, param_name: str) -> list[str]:
    """SCPI write: send command with value."""
    cmd = command.strip()
    if cmd.endswith("?"):
        cmd = cmd[:-1]

    return [f"        self.send_command(f\"{cmd} {{{param_name}}}\")"]


# ── ASCII bodies ─────────────────────────────────────────────────────────

def _generate_ascii_read_body(command: str, return_type: str) -> list[str]:
    """ASCII read: send command, parse response."""
    cmd = command.strip()

    if return_type == "float":
        return [f"        response = self.send_command(\"{cmd}\")",
                "        return float(response)"]
    elif return_type == "int":
        return [f"        response = self.send_command(\"{cmd}\")",
                "        return int(response)"]
    elif return_type == "str":
        return [f"        return self.send_command(\"{cmd}\")"]
    elif return_type == "bool":
        return [f"        response = self.send_command(\"{cmd}\")",
                "        return response.strip().upper() in ('1', 'ON', 'TRUE')"]
    else:
        return [f"        return self.send_command(\"{cmd}\")"]


def _generate_ascii_write_body(command: str, param_name: str) -> list[str]:
    """ASCII write: send command with value."""
    cmd = command.strip()

    # Check for @ notation: "OUT_SP_1@{value}" → send as-is
    if "@" in cmd:
        return [f"        self.send_command(f\"{cmd}\")"]
    else:
        return [f"        self.send_command(f\"{cmd} {{{param_name}}}\")"]


# ── Helpers ──────────────────────────────────────────────────────────────

def _to_int_literal(command: str) -> str:
    """Convert command string to Python int literal."""
    if isinstance(command, int):
        return str(command)
    s = str(command).strip()
    if s.startswith("0x") or s.startswith("0X"):
        return s  # already hex literal
    try:
        val = int(s)
        if val > 255:
            return hex(val)
        return str(val)
    except ValueError:
        return f"0x{s}"  # assume hex


def _to_pascal_case(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_") if w)


def _extract_class_body(template_code: str) -> str:
    """Extract the class body from template code (everything after class definition)."""
    lines = template_code.split("\n")
    in_class = False
    class_lines = []
    class_indent = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class ") and "Driver" in stripped:
            in_class = True
            class_indent = len(line) - len(line.lstrip())
            continue
        if in_class:
            if line.strip() and not line.startswith(" " * (class_indent + 1)) and not line.startswith("\t"):
                # Exited class body
                if stripped and not stripped.startswith("#"):
                    break
            class_lines.append(line)

    return "\n".join(class_lines)


# ── Main generator ───────────────────────────────────────────────────────

def generate_driver_code(
    device_spec: dict[str, Any],
    template_code: str,
    skill_name: str = "",
) -> str:
    """Generate complete driver code deterministically from device_spec + template.

    Args:
        device_spec: Unified device spec from _build_device_spec().
        template_code: Protocol template code (from skill/template.py).
        skill_name: Protocol skill name (e.g. "modbus-generic").

    Returns:
        Complete Python driver code as a string.
    """
    ds_device = device_spec.get("device", {})
    ds_connection = device_spec.get("connection", {})
    ds_protocol = device_spec.get("protocol", {})
    ds_functions = device_spec.get("functions", [])

    manufacturer = ds_device.get("manufacturer", "")
    model = ds_device.get("model", "")
    protocol_family = ds_device.get("protocol_family", "UNKNOWN")

    # Device identity
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

    class_name = _to_pascal_case(device_id) + "Driver"

    # ── Build code sections ───────────────────────────────────────
    sections = []

    # 1. Module docstring
    sections.append(f'"""Driver for {instrument_type}.\n\n'
                    f'Protocol: {protocol_family}\n'
                    f'Generated deterministically from device_spec.\n'
                    f'"""')

    # 2. Imports from template
    imports = _extract_imports(template_code)
    sections.append(imports)

    # 3. Exception classes
    exceptions = _extract_exceptions(template_code)
    if exceptions:
        sections.append(exceptions)

    # 4. Parity mapping (if present in template)
    parity_map = _extract_parity_map(template_code)
    if parity_map:
        sections.append(parity_map)

    # 5. Driver class
    class_lines = []
    class_lines.append(f"class {class_name}:")
    class_lines.append(f'    """Driver for {instrument_type}.')
    class_lines.append(f'')
    class_lines.append(f'    Protocol: {protocol_family}')
    class_lines.append(f'    Generated from device_spec. Do not edit manually.')
    class_lines.append(f'    """')
    class_lines.append("")

    # 6. Protocol constants (from template, with device_spec values)
    constants = _generate_protocol_constants(protocol_family, ds_protocol)
    if constants:
        class_lines.extend(constants)
        class_lines.append("")

    # 7. __init__
    init_lines = _generate_init(protocol_family, ds_connection, template_code)
    class_lines.extend(init_lines)
    class_lines.append("")

    # 8. connect / disconnect
    connect_disconnect = _generate_connect_disconnect(protocol_family, template_code)
    class_lines.extend(connect_disconnect)
    class_lines.append("")

    # 9. Protocol helpers from template
    helpers = _extract_helpers(template_code)
    if helpers:
        class_lines.append("    # ── Protocol helpers ─────────────────────────────────────")
        class_lines.append("")
        class_lines.extend(helpers)
        class_lines.append("")

    # 10. Device functions
    class_lines.append("    # ── Device functions ──────────────────────────────────────")
    class_lines.append("")

    for func in ds_functions:
        func_lines = _generate_function(func, protocol_family)
        class_lines.extend(func_lines)
        class_lines.append("")

    sections.append("\n".join(class_lines))

    return "\n\n".join(sections) + "\n"


def _extract_imports(template_code: str) -> str:
    """Extract import statements from template."""
    lines = []
    for line in template_code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            lines.append(stripped)
        elif stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        elif lines and not stripped:
            continue
        elif lines and (stripped.startswith("#") or stripped.startswith("class ")):
            break
    return "\n".join(lines)


def _extract_exceptions(template_code: str) -> str:
    """Extract exception class definitions from template."""
    lines = []
    in_exceptions = False
    for line in template_code.split("\n"):
        stripped = line.strip()
        if "Exception" in stripped and "class " in stripped:
            in_exceptions = True
        if in_exceptions:
            if stripped.startswith("class ") and "Exception" not in stripped and "Error" not in stripped:
                break
            if stripped:
                lines.append(stripped)
    return "\n".join(lines)


def _extract_parity_map(template_code: str) -> str:
    """Extract parity mapping dict from template."""
    if "_PARITY_MAP" not in template_code:
        return ""
    lines = []
    in_map = False
    for line in template_code.split("\n"):
        if "_PARITY_MAP" in line:
            in_map = True
        if in_map:
            lines.append(line)
            if line.strip().startswith("}"):
                break
    return "\n".join(lines)


def _extract_helpers(template_code: str) -> list[str]:
    """Extract helper method lines from template class body."""
    lines = []
    in_class = False
    in_method = False
    method_indent = 0
    skip_method_names = {"__init__", "connect", "disconnect"}

    for line in template_code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("class ") and "Driver" in stripped:
            in_class = True
            continue
        if not in_class:
            continue

        # Detect method definition
        if stripped.startswith("def "):
            method_name = stripped.split("(")[0].replace("def ", "").strip()
            if method_name in skip_method_names:
                in_method = False
                continue
            if method_name.startswith("_") or method_name.startswith("send"):
                in_method = True
                method_indent = len(line) - len(line.lstrip())
                # Re-indent to 4 spaces (class method level)
                relative = line[method_indent:]
                lines.append("    " + relative)
                continue

        if in_method:
            if stripped and not line.startswith(" " * method_indent) and not line.startswith("\t"):
                if stripped.startswith("def ") or stripped.startswith("class "):
                    in_method = False
                    continue
            # Re-indent
            if stripped:
                relative = line[method_indent:]
                lines.append("    " + relative)
            else:
                lines.append("")

    return lines


def _generate_protocol_constants(protocol_family: str, ds_protocol: dict) -> list[str]:
    """Generate protocol constant declarations."""
    pf = protocol_family.upper()
    lines = []

    if pf in ("BINARY",):
        header_hex = ds_protocol.get("header_hex", "AA55")
        if header_hex:
            # Convert hex string to bytes literal
            header_bytes = bytes.fromhex(header_hex)
            header_repr = "b'" + "".join(f"\\x{b:02x}" for b in header_bytes) + "'"
            lines.append(f"    _HEADER = {header_repr}")

        length_size = int(ds_protocol.get("length_field_size", 2))
        lines.append(f"    _LENGTH_FIELD_SIZE = {length_size}")

        checksum_bytes = int(ds_protocol.get("checksum_bytes", 1))
        lines.append(f"    _CHECKSUM_SIZE = {checksum_bytes}")

        byte_order = ds_protocol.get("byte_order", "big-endian")
        bo = ">" if "big" in byte_order.lower() else "<"
        lines.append(f"    _LENGTH_BYTE_ORDER = \"{bo}\"")

    return lines


def _generate_init(protocol_family: str, ds_connection: dict, template_code: str) -> list[str]:
    """Generate __init__ method."""
    pf = protocol_family.upper()
    lines = []

    # Connection parameters
    baudrate = ds_connection.get("baudrate", 9600)
    parity = ds_connection.get("parity", "N")
    stopbits = ds_connection.get("stopbits", 1.0)
    timeout_s = ds_connection.get("timeout_ms", 2000) / 1000.0

    if pf in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP"):
        lines.append("    def __init__(self, port, unit_id=1, baudrate=9600, parity=\"N\", stopbits=1.0, timeout=2.0, transport=None):")
        lines.append("        if transport is not None:")
        lines.append("            self._instr = transport")
        lines.append("        else:")
        lines.append("            self._instr = minimalmodbus.Instrument(port, unit_id)")
        lines.append(f"            self._instr.serial.baudrate = {baudrate}")
        lines.append(f"            self._instr.serial.parity = _PARITY_MAP.get(\"{parity}\", minimalmodbus.serial.PARITY_NONE)")
        lines.append(f"            self._instr.serial.stopbits = {stopbits}")
        lines.append(f"            self._instr.serial.timeout = {timeout_s}")
        lines.append("        self._connected = False")
    else:
        lines.append("    def __init__(self, port, baudrate=9600, parity=\"N\", stopbits=1.0, timeout=2.0, transport=None):")
        lines.append("        self._port = port")
        lines.append(f"        self._baudrate = {baudrate}")
        lines.append(f"        self._parity = \"{parity}\"")
        lines.append(f"        self._stopbits = {stopbits}")
        lines.append(f"        self._timeout = {timeout_s}")
        lines.append("        self._ser = transport")
        lines.append("        self._connected = False")

    return lines


def _generate_connect_disconnect(protocol_family: str, template_code: str) -> list[str]:
    """Generate connect/disconnect methods."""
    pf = protocol_family.upper()
    lines = []

    if pf in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP"):
        lines.extend([
            "    def connect(self):",
            "        if self._connected:",
            "            return",
            "        if not self._instr.serial.is_open:",
            "            self._instr.serial.open()",
            "        self._connected = True",
            "",
            "    def disconnect(self):",
            "        if not self._connected:",
            "            return",
            "        if self._instr.serial.is_open:",
            "            self._instr.serial.close()",
            "        self._connected = False",
        ])
    else:
        lines.extend([
            "    def connect(self):",
            "        if self._connected:",
            "            return",
            "        if self._ser is None:",
            "            self._ser = serial.Serial(",
            "                port=self._port, baudrate=self._baudrate,",
            "                bytesize=8, parity=self._parity,",
            "                stopbits=self._stopbits, timeout=self._timeout,",
            "            )",
            "        if not self._ser.is_open:",
            "            self._ser.open()",
            "        self._connected = True",
            "",
            "    def disconnect(self):",
            "        if not self._connected:",
            "            return",
            "        if self._ser is not None and self._ser.is_open:",
            "            self._ser.close()",
            "        self._connected = False",
        ])

    return lines


def _generate_function(func: dict, protocol_family: str) -> list[str]:
    """Generate a single device function."""
    fname = func.get("function_name", "unknown")
    signature = func.get("signature", f"def {fname}(self) -> Any")
    purpose = func.get("purpose", "")
    category = func.get("function_category", "read")
    commands = func.get("protocol_action_binding", [])
    constraints = func.get("parameter_constraints", {})

    return_type = _parse_return_type(signature)
    param_names = _parse_param_names(signature)

    # Docstring
    docstring = purpose or f"Execute {fname}"
    cmd_str = ", ".join(str(c) for c in commands) if commands else "N/A"

    lines = []
    lines.append(f"    def {fname}(self{_format_params(param_names)}) {_get_return_annotation(return_type)}:")
    lines.append(f'        """{docstring}')
    lines.append(f'')
    lines.append(f'        Command: {cmd_str}')
    lines.append(f'        """')

    # Function body
    command = commands[0] if commands else fname
    is_read = category == "read"

    if is_read:
        body = _generate_read_body(protocol_family, command, return_type)
    else:
        param = param_names[0] if param_names else "value"
        # Get constraints for this parameter
        param_constraints = constraints.get(param, constraints.get("value"))
        body = _generate_write_body(protocol_family, command, param, param_constraints)

    lines.extend(body)

    return lines


def _format_params(param_names: list[str]) -> str:
    """Format parameter list for function definition."""
    if not param_names:
        return ""
    return ", " + ", ".join(param_names)


def _get_return_annotation(return_type: str) -> str:
    """Get return type annotation string."""
    if return_type == "Any":
        return "-> Any"
    return f"-> {return_type}"
