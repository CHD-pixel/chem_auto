"""Flat extraction schemas — each model has 3-8 flat fields, NO nesting, NO lists of objects.

Research basis: PARSE (EMNLP 2025), ExtractBench (2026), NEXT-EVAL (2026).
Flat schemas with <=8 fields achieve 0.90-0.95+ extraction F1 vs 0.5-0.7 for nested.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, field_validator


# ── Base class with int→str coercion ──────────────────────────────

class _FlatBase(BaseModel):
    """Base for all flat schemas: coerces int/float fields to str automatically."""

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_numeric_to_str(cls, v: Any, info) -> Any:
        """LLMs output numbers without quotes even for str-typed fields. Coerce them."""
        field_name = info.field_name
        field = cls.model_fields.get(field_name)
        if field is not None and field.annotation is str:
            if isinstance(v, (int, float)):
                return str(int(v) if isinstance(v, float) and v == int(v) else v)
            if isinstance(v, bool):
                return str(v).lower()
        return v


# ── 1. Device Identity (3 fields) ──────────────────────────────────

class FlatDeviceIdentity(_FlatBase):
    manufacturer: str = Field(default="", description="Instrument manufacturer name")
    model: str = Field(default="", description="Instrument model name/number")
    protocol_family: str = Field(
        default="UNKNOWN",
        description="SCPI, MODBUS, ASCII, GCODE, CANOPEN, JSON-RPC, BINARY, or UNKNOWN",
    )


# ── 2. Serial Connection (5 fields) ────────────────────────────────

class FlatSerialConnection(_FlatBase):
    baudrate: str = Field(default="", description="Baud rate from the manual. Leave empty if not found.")
    databits: int = Field(default=8, description="Data bits: 7 or 8")
    parity: str = Field(default="N", description="Parity: N, E, or O")
    stopbits: float = Field(default=1.0, description="Stop bits: 1.0, 1.5, or 2.0")
    flow_control: str = Field(default="none", description="Flow control: none, rtscts, xonxoff")


# ── 3. Network Connection (3 fields) ───────────────────────────────

class FlatNetworkConnection(_FlatBase):
    default_port: str = Field(default="", description="TCP port as text, e.g. '502' for Modbus TCP, '5025' for SCPI raw, empty if serial")
    protocol_hint: str = Field(default="", description="raw, vxi11, hislip, modbus-tcp, or empty if N/A")
    host_hint: str = Field(default="", description="Default IP or hostname if specified in manual, else empty")


# ── 4. Message Framing (6 fields) ──────────────────────────────────

class FlatFraming(_FlatBase):
    encoding: str = Field(default="", description="Encoding from the manual: ascii, binary, ascii_hex. Leave empty if not found.")
    write_terminator: str = Field(default="", description="Write terminator from the manual. Leave empty if not found.")
    read_terminator: str = Field(default="", description="Read terminator from the manual. Leave empty if not found.")
    header_hex: str = Field(default="", description="Header bytes as hex from the manual. Leave empty if not found.")
    checksum_type: str = Field(default="", description="Checksum type from the manual. Leave empty if not found.")
    checksum_bytes: int = Field(default=0, description="Checksum byte count from the manual. Leave 0 if not found.")
    length_field_size: int = Field(default=0, description="Length field byte count (1 or 2). 0 if not applicable (MODBUS, SCPI, ASCII).")
    length_semantics: str = Field(
        default="",
        description=(
            "What the length field value counts. "
            "'payload' = command + data bytes (most common: HDLC, BLE). "
            "'payload_and_checksum' = command + data + checksum bytes. "
            "'header_to_checksum' = everything from Length field to end of Checksum, "
            "including Length field itself. "
            "Leave empty if not applicable or not found."
        ),
    )
    byte_order: str = Field(
        default="",
        description=(
            "Byte order of the length field. "
            "'big' for big-endian (high byte first, most common). "
            "'little' for little-endian (low byte first). "
            "Leave empty if not found or not applicable."
        ),
    )


# ── 5. Command Table — Raw Text (1 field) ──────────────────────────

class FlatCommandTable(_FlatBase):
    raw_table: str = Field(
        default="",
        description="The ENTIRE command/register table from the manual as raw text. "
        "One line per command/register. Preserve original IDs exactly. "
        "Format per line: command_id | name | read/write | data_type | range | unit | notes",
    )


# ── 6. Read Commands (4 fields each, flat list) ────────────────────

class FlatReadCommand(_FlatBase):
    command_id: str = Field(default="", description="Command identifier or register address")
    function_name: str = Field(default="", description="Snake-case Python function name")
    description: str = Field(default="", description="What this command reads")
    return_type: str = Field(default="str", description="Return type: str, int, float, list[int]")


# ── 7. Write Commands (5 fields each, flat list) ───────────────────

class FlatWriteCommand(_FlatBase):
    command_id: str = Field(default="", description="Command identifier or register address")
    function_name: str = Field(default="", description="Snake-case Python function name")
    description: str = Field(default="", description="What this command writes/configures")
    param_name: str = Field(default="value", description="Parameter name")
    param_type: str = Field(default="int", description="Parameter type: int, float, str, bool")


# ── 8. Parameter Constraints (5 fields each, flat list) ────────────

class FlatParamConstraint(_FlatBase):
    function_name: str = Field(default="", description="Function this constraint applies to")
    param_name: str = Field(default="value", description="Parameter name")
    min_value: float | None = Field(default=None, description="Minimum allowed value, or null")
    max_value: float | None = Field(default=None, description="Maximum allowed value, or null")
    unit: str = Field(default="", description="Physical unit, e.g. C, rpm, mL/min")


# ── 9. Data Types per Command (4 fields each) ──────────────────────

class FlatDataType(_FlatBase):
    command_id: str = Field(default="", description="Command identifier or register address")
    data_type: str = Field(default="int16", description="int16, uint16, int32, uint32, float32, string")
    byte_order: str = Field(default="big", description="big or little")
    scale: str = Field(default="1", description="Scale factor, e.g. 0.1 means raw * 0.1 = physical")


# ── 10. Modbus-Specific (5 fields each) ────────────────────────────

class FlatModbusRegister(_FlatBase):
    register_address: str = Field(default="", description="Register address as in manual, e.g. 0x1000 or 40001")
    name: str = Field(default="", description="Register name from manual")
    register_type: str = Field(default="holding", description="holding, input, coil, discrete")
    function_read: str = Field(default="0x03", description="Read function code: 0x03 or 0x04")
    function_write: str = Field(default="", description="Write function code: 0x06 or 0x10, empty if read-only")


# ── 11. Error Patterns (4 fields) ──────────────────────────────────

class FlatErrorPatterns(_FlatBase):
    error_prefix: str = Field(default="", description="Error prefix/suffix from the manual. Leave empty if not found.")
    success_pattern: str = Field(default="", description="Success pattern from the manual. Leave empty if not found.")
    has_error_codes: str = Field(default="", description="'true' if device has specific error codes, 'false' if not, empty if unknown.")
    error_examples: str = Field(default="", description="Error examples from the manual. Leave empty if not found.")


# ── 12. Timing (4 fields) ──────────────────────────────────────────

class FlatTiming(_FlatBase):
    inter_command_ms: str = Field(default="", description="Min command gap from the manual. Leave empty if not found.")
    response_timeout_ms: str = Field(default="", description="Response timeout from the manual. Leave empty if not found.")
    warmup_seconds: str = Field(default="", description="Warmup time from the manual. Leave empty if not found.")
    special_timing_notes: str = Field(default="", description="Any special timing requirements from manual")


# ── 13. Lifecycle — Init (2 fields) ────────────────────────────────

class FlatLifecycleInit(_FlatBase):
    init_sequence: str = Field(default="", description="Startup steps from manual. Leave empty if none described.")
    handshake_required: str = Field(default="", description="'true' if device requires handshake, 'false' if not, empty if unknown.")


# ── 14. Lifecycle — Shutdown (2 fields) ────────────────────────────

class FlatLifecycleShutdown(_FlatBase):
    shutdown_sequence: str = Field(default="", description="Shutdown steps from manual, one step per line")
    emergency_stop: str = Field(default="", description="Emergency stop command or procedure, empty if not specified")


# ── 15. Preconditions (3 fields each) ──────────────────────────────

class FlatPrecondition(_FlatBase):
    function_name: str = Field(default="", description="Function that has a precondition")
    required_first: str = Field(default="", description="Function that must be called first")
    reason: str = Field(default="", description="Why this precondition exists, from manual")


# ── Helper: registry of all extraction agents ──────────────────────

FLAT_AGENTS: list[dict[str, Any]] = [
    {
        "key": "flat_device",
        "schema": FlatDeviceIdentity,
        "desc": (
            "Extract device identity from the cover page, title, header area, and first section.\n"
            "- manufacturer: company/vendor name in English. If the manual is non-English,\n"
            "  transliterate or use the standard English trade name.\n"
            "- model: instrument model number/series name in English.\n"
            "- protocol_family: the communication protocol used. Check the communication/interface\n"
            "  section for keywords: MODBUS, SCPI, ASCII, CANopen, G-code, JSON-RPC, REST, proprietary.\n"
            "  If not explicitly named, infer from command format: register addresses → MODBUS;\n"
            "  SCPI-style commands (:MEAS:VOLT?) → SCPI; JSON objects → JSON-RPC.\n"
            "  Valid values: MODBUS, SCPI, ASCII, GCODE, CANOPEN, JSON-RPC, BINARY, UNKNOWN."
        ),
    },
    {
        "key": "flat_serial",
        "schema": FlatSerialConnection,
        "desc": (
            "Extract serial port parameters from the communication settings section.\n"
            "Look for a table or list labeled 'Communication parameters', 'Serial settings', etc.\n"
            "- baudrate: the baud rate value from the manual. Search for 'Data rate', 'Baud rate', '波特率'. Output the exact documented value. Leave empty if the manual does not specify a baud rate.\n"
            "- databits: 7 or 8. Usually stated as '8 data bits' or '8-N-1' notation.\n"
            "- parity: N (none), E (even), O (odd). Decode from '8E1' → E, '8N1' → N.\n"
            "- stopbits: 1.0, 1.5, or 2.0. Usually 1.0.\n"
            "- flow_control: 'none' unless RTS/CTS or XON/XOFF is explicitly mentioned.\n"
            "If the device is TCP/Ethernet only, leave all fields at defaults."
        ),
    },
    {
        "key": "flat_network",
        "schema": FlatNetworkConnection,
        "desc": (
            "Extract TCP/IP network parameters from the communication section.\n"
            "Only fill if the manual mentions Ethernet, TCP, LAN, or IP address.\n"
            "- default_port: TCP port (e.g. 502 for Modbus TCP, 5025 for SCPI raw).\n"
            "- protocol_hint: transport-layer protocol: 'raw', 'vxi11', 'hislip', 'modbus-tcp'.\n"
            "- host_hint: default IP or hostname if provided, else empty.\n"
            "If the manual only describes serial (RS-232/RS-485), leave ALL fields empty."
        ),
    },
    {
        "key": "flat_framing",
        "schema": FlatFraming,
        "desc": (
            "Extract message/packet framing from the protocol format section.\n"
            "Look for sections describing command format, frame structure, message layout.\n"
            "- encoding: 'ascii' for text commands, 'binary' for byte/binary protocols.\n"
            "- write_terminator: command line ending ('\\r', '\\r\\n', '\\n'). Empty for binary.\n"
            "- read_terminator: response line ending. Empty for binary.\n"
            "- header_hex: fixed header bytes as hex (e.g. 'AA55'). Empty if none.\n"
            "- checksum_type: 'none', 'xor', 'additive', 'crc16', 'crc16_modbus', 'crc32'.\n"
            "  Look for 'checksum', 'CRC', 'BCC', 'LRC', 'FCS'. MODBUS → crc16_modbus.\n"
            "- checksum_bytes: 0, 1, 2, or 4. Typically 2 for CRC16.\n"
            "- length_field_size: number of bytes for the length field in binary frames.\n"
            "  Common: 1 or 2 bytes. 0 if protocol has no length field (MODBUS, SCPI, ASCII).\n"
            "- length_semantics: what the length field VALUE counts. Look for text like:\n"
            "  'number of bytes from Length to Checksum', 'Length includes', 'data length'.\n"
            "  Compare with example packets to determine the convention:\n"
            "  * If Length value = (cmd + data bytes only) → 'payload'\n"
            "  * If Length value = (cmd + data + checksum) → 'payload_and_checksum'\n"
            "  * If Length value = (Length field + cmd + data + checksum) → 'header_to_checksum'\n"
            "  Leave empty if protocol has no length field.\n"
            "- byte_order: byte order of the length field. Look at example packets:\n"
            "  * AA 55 00 06 → big-endian (high byte first: 00 then 06)\n"
            "  * AA 55 06 00 → little-endian (low byte first: 06 then 00)\n"
            "  Most binary protocols use big-endian. Leave empty if not found."
        ),
    },
    {
        "key": "flat_cmd_table",
        "schema": FlatCommandTable,
        "desc": (
            "Find ALL instrument-controllable functions, commands, registers, or API methods\n"
            "in the manual. They may appear in ANY form: tables, directory listings,\n"
            "numbered method lists, prose descriptions, API references (JSON-RPC, REST).\n\n"
            "For EACH function, determine the valid parameter values, ranges, or allowed\n"
            "options. This information may be located ANYWHERE in the manual — in the same\n"
            "table row, in a different section, in a reference table, in prose, in an\n"
            "appendix, or in a footnote. If the constraint is stated indirectly (e.g. a\n"
            "cross-reference to another location), follow that reference and fill in the\n"
            "actual resolved values. Never leave a constraint as an unresolved reference.\n\n"
            "CRITICAL — standardize the range column.  Use ONLY these three formats:\n"
            "  1. \"MIN-MAX\" — a numeric range with a single dash (e.g. \"50-1500\", \"0.1-600\")\n"
            "  2. \"VAL1,VAL2,VAL3\" — a comma-separated list of individual values\n"
            "     (e.g. \"13,14,15\" for numbers, \"A,b,d\" for letter options)\n"
            "  3. \"none\" — no constraint exists\n\n"
            "STRICT RULES:\n"
            "- Convert \"..\" \"...\" \"~\" \"to\" → \"-\" (e.g. \"50..1500\" → \"50-1500\")\n"
            "- Strip prefixes (\"m =\", \"n =\", \"xxx\"), tolerances (\"±10\"), and units from\n"
            "  the range column — units go in the unit column, NOT the range column\n"
            "- For \"0 or 50-1500\" style: output two rows, one with range \"0\" and one with range \"50-1500\".\n"
            "  Or output \"0,50-1500\" and let the downstream parser handle it.\n"
            "- For read-only functions with no parameters, write \"none\"\n"
            "- If a constraint references another table, resolve it to actual values\n\n"
            "JSON ENCODING: The raw_table field is a JSON string. Line breaks in the\n"
            "pipe-delimited text MUST be written as \\\\n (backslash-n), not real newlines.\n"
            "Real newlines inside a JSON string will break parsing.\n\n"
            "Output as PIPE-DELIMITED TEXT (one line per item):\n"
            "  identifier | name | read/write | data_type | range | unit | notes\n"
            "Preserve original identifiers (register address, command code). "
            "The 'name' column MUST be in English — translate non-English names into "
            "descriptive English. If NO controllable functions exist, leave empty."
        ),
    },
    {
        "key": "flat_timing",
        "schema": FlatTiming,
        "desc": (
            "Extract timing constraints from the communication or protocol section.\n"
            "Look for 'delay', 'timeout', 'interval', 'gap', 'character time', '响应时间'.\n"
            "- inter_command_ms: minimum delay between successive commands in milliseconds.\n"
            "  Default 50. For MODBUS 3.5 char times at 9600 baud ≈ 4ms — still use 50 as safe default.\n"
            "- response_timeout_ms: maximum wait for a response. Default 2000.\n"
            "- warmup_seconds: device warmup after power-on. Default 0 if not mentioned.\n"
            "- special_timing_notes: any unusual timing requirements. Leave empty if none."
        ),
    },
]
# Note: FlatReadCommand, FlatWriteCommand, FlatParamConstraint, FlatDataType,
# FlatModbusRegister, FlatPrecondition are for per-command/per-register extraction
# and would need to be run once per command/register (not in the initial parallel batch).
