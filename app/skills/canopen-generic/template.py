"""CANopen template — canopen-python.

Protocol: CANopen CiA 301/401/402 over CAN bus.
Copy this skeleton. Replace class name. Read OD indices from device_spec.
"""

from typing import Any

import canopen


# ── Exceptions (copy into your driver) ─────────────────────────────

class DriverError(Exception): pass
class ProtocolError(DriverError): pass
class SafetyError(DriverError): pass
class DeviceBusyError(DriverError): pass
class DeviceCommandError(DriverError): pass


# ── Driver skeleton ────────────────────────────────────────────────

class DeviceDriver:
    """CANopen driver via canopen-python. Replace class name with your device.

    Object Dictionary indices come from device_spec.functions (e.g. 0x6041).
    Use _sdo_read / _sdo_write for register access.
    """

    def __init__(
        self,
        channel: str = "can0",
        bustype: str = "socketcan",
        bitrate: int = 500000,
        node_id: int = 1,
        transport: Any | None = None,
    ):
        self._channel = channel
        self._bustype = bustype
        self._bitrate = bitrate
        self._node_id = node_id
        self._network = None
        self._node = None
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        self._network = canopen.Network()
        self._network.connect(channel=self._channel, bustype=self._bustype, bitrate=self._bitrate)
        self._node = self._network.add_node(self._node_id)
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._network is not None:
            self._network.disconnect()
        self._connected = False

    def _sdo_read(self, index: int, subindex: int = 0) -> Any:
        """Read an Object Dictionary entry via SDO."""
        return self._node.sdo[index][subindex].raw

    def _sdo_write(self, index: int, subindex: int, value: Any) -> None:
        """Write an Object Dictionary entry via SDO."""
        self._node.sdo[index][subindex].raw = value

    def _sdo_read_float(self, index: int, subindex: int = 0) -> float:
        """Read a float32 from OD."""
        return self._node.sdo[index][subindex].phys

    def _sdo_write_float(self, index: int, subindex: int, value: float) -> None:
        """Write a float32 to OD."""
        self._node.sdo[index][subindex].phys = value

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
    # Pattern: def get_status(self): return self._sdo_read(0x6041)
    # Pattern: def set_target(self, value): self._sdo_write_float(0x60FF, 0, value)
