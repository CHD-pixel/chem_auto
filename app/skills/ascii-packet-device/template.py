"""ASCII serial protocol template — pyserial.

Protocol: text commands with \\r or \\n termination over RS-232/RS-485.
Copy this skeleton. Replace class name. Add device functions from device_spec.
"""

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
    """ASCII serial driver. Replace class name with your device."""

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: float = 1.0,
        timeout: float = 2.0,
        write_terminator: str = "\r",
        read_terminator: str = "\r",
        transport: Any | None = None,
    ):
        self._port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._timeout = timeout
        self._write_term = write_terminator.encode() if isinstance(write_terminator, str) else write_terminator
        self._read_term = read_terminator.encode() if isinstance(read_terminator, str) else read_terminator
        self._ser = transport  # inject pre-configured serial for testing
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

    def send_command(self, command: str) -> str:
        """Send a text command and return the response line."""
        self._ser.reset_input_buffer()
        self._ser.write(command.encode() + self._write_term)
        time.sleep(0.05)  # inter-command gap
        raw = b""
        while not raw.endswith(self._read_term):
            chunk = self._ser.read(1)
            if not chunk:
                break
            raw += chunk
        return raw.rstrip(self._read_term).decode("utf-8", errors="replace").strip()

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
    # Pattern: def get_xxx(self): return self.send_command("CMD")
    # Pattern: def set_xxx(self, value): self.send_command(f"CMD {value}")
