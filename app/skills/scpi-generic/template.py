"""SCPI template — pyserial.

Protocol: SCPI/IEEE 488.2 text commands over serial/USB.
Copy this skeleton. Replace class name. Read command strings from device_spec.
"""

import time
from typing import Any

import serial


# ── Exceptions (copy into your driver) ─────────────────────────────

class DriverError(Exception): pass
class ProtocolError(DriverError): pass
class SafetyError(DriverError): pass
class DeviceBusyError(DriverError): pass
class DeviceCommandError(DriverError): pass


# ── Driver skeleton ────────────────────────────────────────────────

class DeviceDriver:
    """SCPI driver via pyserial. Replace class name with your device."""

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: float = 1.0,
        timeout: float = 5.0,
        write_terminator: str = "\n",
        read_terminator: str = "\n",
        transport: Any | None = None,
    ):
        self._port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._timeout = timeout
        self._write_term = write_terminator
        self._read_term = read_terminator
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

    def write(self, command: str) -> None:
        """Send a SCPI command (no response expected)."""
        self._ser.reset_input_buffer()
        self._ser.write((command + self._write_term).encode())

    def query(self, command: str) -> str:
        """Send a SCPI query and return the response."""
        self._ser.reset_input_buffer()
        self._ser.write((command + self._write_term).encode())
        time.sleep(0.05)
        raw = b""
        while not raw.endswith(self._read_term.encode()):
            chunk = self._ser.read(1)
            if not chunk:
                break
            raw += chunk
        return raw.rstrip(self._read_term.encode()).decode("utf-8", errors="replace").strip()

    def query_float(self, command: str) -> float:
        return float(self.query(command))

    def query_int(self, command: str) -> int:
        return int(self.query(command))

    def check_error(self) -> None:
        """Check SCPI error queue. Raises on errors."""
        errors = []
        while True:
            resp = self.query(":SYSTem:ERRor?")
            if resp.startswith("0,") or "No error" in resp:
                break
            errors.append(resp)
            if len(errors) > 20:
                break
        if errors:
            raise DeviceCommandError(f"SCPI errors: {errors}")

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
    # Pattern: def get_xxx(self): return self.query_float(":MEAS:VOLT:DC?")
    # Pattern: def set_xxx(self, value): self.write(f":SOUR:VOLT {value}")
