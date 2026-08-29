"""Binary frame protocol template — pyserial.

Protocol: [Header][Length][Command][Data...][Checksum] over RS-232/RS-485.
Copy this skeleton. Replace class name. Read ALL protocol constants from device_spec.
"""

import struct
import time
from typing import Any, Optional

import serial


# ── Exceptions (copy into your driver) ─────────────────────────────

class DriverError(Exception): pass
class ProtocolError(DriverError): pass
class SafetyError(DriverError): pass
class DeviceBusyError(DriverError): pass
class DeviceCommandError(DriverError): pass


# ── Driver skeleton ────────────────────────────────────────────────

class DeviceDriver:
    """Binary frame driver. Replace class name with your device.

    IMPORTANT: Replace ALL constants below with values from device_spec.
    Do NOT use the example values shown here.
    """

    # Protocol constants — read from device_spec.protocol
    _HEADER = b"\xAA\x55"           # from device_spec.protocol.header_hex
    _LENGTH_FIELD_SIZE = 2          # 1 or 2 bytes
    _CHECKSUM_SIZE = 1              # 0, 1, or 2 bytes
    _LENGTH_BYTE_ORDER = ">"        # ">" big-endian, "<" little-endian

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: float = 1.0,
        timeout: float = 2.0,
        transport: Any | None = None,
    ):
        self._port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._timeout = timeout
        self._ser = transport
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        if self._ser is None:
            self._ser = serial.Serial(
                port=self._port, baudrate=self._baudrate,
                bytesize=8, parity=self._parity,
                stopbits=self._stopbits, timeout=self._timeout,
            )
        if not self._ser.is_open:
            self._ser.open()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._ser is not None and self._ser.is_open:
            self._ser.close()
        self._connected = False

    def _build_packet(self, command: int, data: bytes = b"") -> bytes:
        """Build a frame: [HEADER][LENGTH][COMMAND][DATA...][CHECKSUM].

        CRITICAL: The checksum MUST include the length field bytes!
        checksum = sum(length_bytes + command_byte + data_bytes) & 0xFF
        This is because the manual says: 'Checksum = Accumulation of all bytes
        from Length to Data' — the length field IS included in the checksum.
        """
        payload = bytes([command]) + data
        length_value = len(payload)  # adjust per device_spec.protocol.length_semantics
        if self._LENGTH_FIELD_SIZE == 2:
            length_field = struct.pack(f"{self._LENGTH_BYTE_ORDER}H", length_value)
        else:
            length_field = bytes([length_value & 0xFF])
        frame = self._HEADER + length_field + payload
        # CRITICAL: Include length_field in checksum computation!
        return frame + self._compute_checksum(length_field + payload)

    def _compute_checksum(self, data: bytes) -> bytes:
        """Compute checksum. Replace with algorithm from device_spec.protocol."""
        return bytes([sum(data) & 0xFF])  # additive checksum example

    def _parse_response(self, raw: bytes) -> tuple[int, bytes]:
        """Parse response frame. Returns (command_byte, data_bytes)."""
        header_size = len(self._HEADER)
        if len(raw) < header_size:
            raise ProtocolError(f"Response too short: {len(raw)} bytes")
        if raw[:header_size] != self._HEADER:
            raise ProtocolError(f"Header mismatch: {raw[:header_size].hex()}")

        length_size = self._LENGTH_FIELD_SIZE
        if length_size == 2:
            length_value = struct.unpack(f"{self._LENGTH_BYTE_ORDER}H", raw[header_size:header_size+2])[0]
        else:
            length_value = raw[header_size]

        payload_start = header_size + length_size
        payload_end = payload_start + length_value
        if len(raw) < payload_end:
            raise ProtocolError(f"Incomplete payload")

        payload = raw[payload_start:payload_end]
        command = payload[0]
        resp_data = payload[1:]

        if self._CHECKSUM_SIZE > 0:
            received = raw[payload_end:payload_end + self._CHECKSUM_SIZE]
            computed = self._compute_checksum(raw[header_size:payload_end])
            if received != computed:
                raise ProtocolError(f"Checksum mismatch: {received.hex()} vs {computed.hex()}")

        return command, resp_data

    def send_recv(self, command: int, data: bytes = b"") -> bytes:
        """Send a command and return the response data bytes."""
        self._ser.reset_input_buffer()
        packet = self._build_packet(command, data)
        self._ser.write(packet)
        time.sleep(0.05)

        # Read header
        header_size = len(self._HEADER)
        raw = self._ser.read(header_size)
        if len(raw) < header_size:
            raise ProtocolError(f"Timeout reading header")

        # Read length field
        length_bytes = self._ser.read(self._LENGTH_FIELD_SIZE)
        if len(length_bytes) < self._LENGTH_FIELD_SIZE:
            raise ProtocolError(f"Timeout reading length")
        if self._LENGTH_FIELD_SIZE == 2:
            length_value = struct.unpack(f"{self._LENGTH_BYTE_ORDER}H", length_bytes)[0]
        else:
            length_value = length_bytes[0]

        # Read remaining
        remaining = length_value + self._CHECKSUM_SIZE
        rest = self._ser.read(remaining)
        if len(rest) < remaining:
            raise ProtocolError(f"Timeout reading payload")

        _, resp_data = self._parse_response(raw + length_bytes + rest)
        return resp_data

    def _check_parameter_range(self, value: Any, allowed: Any, unit: str = "") -> None:
        if allowed is None:
            return
        if isinstance(allowed, (list, tuple, set)):
            if value not in allowed:
                raise SafetyError(f"Value {value} not in allowed {list(allowed)}")
        elif isinstance(allowed, dict):
            lo, hi = allowed.get("min_value"), allowed.get("max_value")
            if lo is not None and value < lo:
                raise SafetyError(f"Value {value} below minimum {lo}{' ' + unit if unit else ''}")
            if hi is not None and value > hi:
                raise SafetyError(f"Value {value} above maximum {hi}{' ' + unit if unit else ''}")

    # ── Data helpers (adapt byte order to your protocol) ────────────

    def _parse_uint16(self, data: bytes, offset: int = 0) -> int:
        return struct.unpack_from(">H", data, offset)[0]

    def _parse_int16(self, data: bytes, offset: int = 0) -> int:
        return struct.unpack_from(">h", data, offset)[0]

    def _parse_float32(self, data: bytes, offset: int = 0) -> float:
        return struct.unpack_from(">f", data, offset)[0]

    def _pack_uint16(self, value: int) -> bytes:
        return struct.pack(">H", value)

    def _pack_int16(self, value: int) -> bytes:
        return struct.pack(">h", value)

    def _pack_float32(self, value: float) -> bytes:
        return struct.pack(">f", value)

    # ── Add device functions below ─────────────────────────────────
    # Pattern: data = self.send_recv(CMD_ID); return self._parse_uint16(data)
    # Pattern: self.send_recv(CMD_ID, self._pack_uint16(value))
