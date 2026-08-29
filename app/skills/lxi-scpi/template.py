"""LXI/SCPI template — raw TCP socket.

Protocol: SCPI over TCP/IP (port 5025).
Copy this skeleton. Replace class name. Read command strings from device_spec.
For VXI-11 or HiSLIP, use pyvisa-py instead of raw sockets.
"""

import socket
import time
from typing import Any


# ── Exceptions (copy into your driver) ─────────────────────────────

class DriverError(Exception): pass
class ProtocolError(DriverError): pass
class SafetyError(DriverError): pass
class DeviceBusyError(DriverError): pass
class DeviceCommandError(DriverError): pass


# ── Driver skeleton ────────────────────────────────────────────────

class DeviceDriver:
    """LXI/SCPI driver via raw TCP socket. Replace class name with your device.

    For instruments on the network (Ethernet/WiFi).
    Commands come from device_spec.functions (e.g. ":MEAS:VOLT:DC?").
    """

    def __init__(
        self,
        host: str,
        port: int = 5025,
        timeout: float = 10.0,
        write_terminator: str = "\n",
        read_terminator: str = "\n",
        transport: Any | None = None,
    ):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._write_term = write_terminator
        self._read_term = read_terminator
        self._sock = transport
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self._timeout)
            self._sock.connect((self._host, self._port))
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._sock is not None:
            self._sock.close()
        self._sock = None
        self._connected = False

    def write(self, command: str) -> None:
        """Send a SCPI command (no response expected)."""
        self._sock.sendall((command + self._write_term).encode())

    def query(self, command: str) -> str:
        """Send a SCPI query and return the response."""
        self._sock.sendall((command + self._write_term).encode())
        time.sleep(0.05)
        raw = b""
        term = self._read_term.encode()
        while not raw.endswith(term):
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        return raw.rstrip(term).decode("utf-8", errors="replace").strip()

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
