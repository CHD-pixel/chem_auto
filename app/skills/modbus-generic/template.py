"""Modbus RTU template — minimalmodbus.

Protocol: Modbus RTU over RS-232/RS-485.
Copy this skeleton. Replace class name. Read register addresses from device_spec.
Do NOT use pyserial directly — minimalmodbus handles framing and CRC.
"""

import time
from typing import Any

import minimalmodbus


# ── Exceptions (copy into your driver) ─────────────────────────────

class DriverError(Exception): pass
class ProtocolError(DriverError): pass
class SafetyError(DriverError): pass
class DeviceBusyError(DriverError): pass
class DeviceCommandError(DriverError): pass


# ── Parity mapping ─────────────────────────────────────────────────

_PARITY_MAP = {
    "N": minimalmodbus.serial.PARITY_NONE,
    "E": minimalmodbus.serial.PARITY_EVEN,
    "O": minimalmodbus.serial.PARITY_ODD,
}


# ── Driver skeleton ────────────────────────────────────────────────

class DeviceDriver:
    """Modbus RTU driver via minimalmodbus. Replace class name with your device.

    Register addresses come from device_spec.functions (e.g. 0x1000).
    Use _read_register / _write_register for all register access.
    """

    def __init__(
        self,
        port: str,
        unit_id: int = 1,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: float = 1.0,
        timeout: float = 2.0,
        transport: Any | None = None,
    ):
        if transport is not None:
            self._instr = transport
        else:
            self._instr = minimalmodbus.Instrument(port, unit_id)
            self._instr.serial.baudrate = baudrate
            self._instr.serial.parity = _PARITY_MAP.get(parity.upper(), minimalmodbus.serial.PARITY_NONE)
            self._instr.serial.stopbits = stopbits
            self._instr.serial.timeout = timeout
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        if not self._instr.serial.is_open:
            self._instr.serial.open()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._instr.serial.is_open:
            self._instr.serial.close()
        self._connected = False

    # ── Register access ────────────────────────────────────────────

    def _read_register(self, address: int, signed: bool = False) -> int:
        """Read a single holding register (function 0x03)."""
        return self._instr.read_register(address, signed=signed)

    def _read_float32(self, address: int) -> float:
        """Read a 32-bit float from two consecutive registers."""
        return self._instr.read_float(address)

    def _read_string(self, address: int, length: int) -> str:
        """Read a string from consecutive registers."""
        return self._instr.read_string(address, length)

    def _write_register(self, address: int, value: int) -> None:
        """Write a single holding register (function 0x06)."""
        self._instr.write_register(address, value)

    def _write_float32(self, address: int, value: float) -> None:
        """Write a 32-bit float to two consecutive registers."""
        self._instr.write_float(address, value)

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

    # ── Add device functions below ─────────────────────────────────
    # Pattern: def get_xxx(self): return self._read_float32(0x1000)
    # Pattern: def set_xxx(self, value): self._write_float32(0x2000, value)
