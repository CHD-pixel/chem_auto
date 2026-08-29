"""G-code template — pyserial.

Protocol: G-code text commands over serial (GRBL/Marlin/Smoothieware).
Copy this skeleton. Replace class name. Read commands from device_spec.
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
    """G-code driver via pyserial. Replace class name with your device.

    Supports GRBL dialect (ok/error:N). For Marlin, check ok vs echo:busy.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        parity: str = "N",
        stopbits: float = 1.0,
        timeout: float = 5.0,
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

    def send_gcode(self, command: str) -> dict:
        """Send a G-code command and wait for acknowledgment.

        Returns: {"status": "ok"} or {"status": "error", "message": "..."}
        """
        self._ser.reset_input_buffer()
        self._ser.write((command + "\n").encode())
        time.sleep(0.05)
        response_lines = []
        while True:
            raw = self._ser.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line == "ok":
                return {"status": "ok", "lines": response_lines}
            if line.startswith("error"):
                return {"status": "error", "message": line, "lines": response_lines}
            if line.startswith("echo:busy"):
                time.sleep(0.1)
                continue
            response_lines.append(line)
        return {"status": "timeout", "lines": response_lines}

    def send_gcode_query(self, command: str) -> str:
        """Send a G-code command and return all response lines."""
        self._ser.reset_input_buffer()
        self._ser.write((command + "\n").encode())
        time.sleep(0.05)
        lines = []
        while True:
            raw = self._ser.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line == "ok" or line.startswith("error"):
                break
            if line:
                lines.append(line)
        return "\n".join(lines)

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
    # Pattern: def home(self): return self.send_gcode("G28")
    # Pattern: def move_to(self, x, y, feedrate): return self.send_gcode(f"G1 X{x} Y{y} F{feedrate}")
