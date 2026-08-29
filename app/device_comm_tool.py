from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from app.schemas.build_blueprint import BuildBlueprint
from app.schemas.protocol_spec import ProtocolSpec

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:  # pragma: no cover - imported lazily in tests
    serial = None
    list_ports = None


class SerialLike(Protocol):
    def write(self, payload: bytes) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...


@dataclass
class RealSerialTransport:
    serial_port: SerialLike

    def __getattr__(self, name: str):
        return getattr(self.serial_port, name)

    @property
    def is_open(self) -> bool:
        return getattr(self.serial_port, "is_open", True)

    def write(self, payload: bytes) -> int:
        return self.serial_port.write(payload)

    def read(self, size: int = 4096) -> bytes:
        return self.serial_port.read(size)

    def close(self) -> None:
        self.serial_port.close()


def list_available_serial_ports(list_ports_factory: Any | None = None) -> list[dict[str, str]]:
    factory = list_ports_factory
    if factory is None:
        if list_ports is None:
            return []
        factory = list_ports.comports
    ports: list[dict[str, str]] = []
    for item in factory():
        dev = str(getattr(item, "device", ""))
        ports.append(
            {
                "port": dev,
                "description": str(getattr(item, "description", "")),
                "hwid": str(getattr(item, "hwid", "")),
            }
        )
    return ports


def verify_serial_port(port: str) -> dict:
    """Try to open a serial port to verify it's accessible.

    Returns {"ok": True, ...} if the port can be opened.
    """
    if serial is None:
        return {"ok": False, "error": "pyserial not available"}
    port = (port or "").strip()
    if not port:
        return {"ok": False, "error": "empty port name"}
    try:
        s = serial.Serial(port, 9600, timeout=0.1)
        s.close()
        return {"ok": True, "port": port, "message": f"{port} opened successfully"}
    except Exception as e:
        return {"ok": False, "port": port, "error": str(e)}


def real_device_gate(*, port: str | None = None) -> tuple[bool, str | None]:
    selected_port = (port or "").strip()
    if not selected_port:
        return False, "Real serial execution requires an explicit serial port selected by the user."
    if serial is None:
        return False, "pyserial is not available in the current environment."
    return True, None


def resolve_real_serial_settings(
    *,
    blueprint: BuildBlueprint,
    protocol_spec_payload: dict[str, Any] | None,
    selected_port: str,
) -> dict[str, Any]:
    protocol_spec = (
        ProtocolSpec.model_validate(protocol_spec_payload)
        if isinstance(protocol_spec_payload, dict) and protocol_spec_payload
        else None
    )
    transport_config = blueprint.driver_blueprint.transport_config
    protocol_transport = protocol_spec.transport if protocol_spec is not None else None
    optional_args = dict(transport_config.optional_constructor_args or {})

    baudrate = protocol_transport.baudrate if protocol_transport and protocol_transport.baudrate is not None else None
    if baudrate is None:
        baudrate = optional_args.get("baudrate")

    timeout_ms = protocol_transport.timeout_ms if protocol_transport and protocol_transport.timeout_ms is not None else None
    if timeout_ms is None:
        timeout_ms = transport_config.default_timeout_ms
    if timeout_ms is None:
        timeout_ms = optional_args.get("timeout_ms")

    databits = protocol_transport.databits if protocol_transport else None
    if databits is None:
        databits = optional_args.get("databits") or optional_args.get("bytesize")

    parity = protocol_transport.parity if protocol_transport else None
    if parity is None:
        parity = optional_args.get("parity")

    stopbits = protocol_transport.stopbits if protocol_transport else None
    if stopbits is None:
        stopbits = optional_args.get("stopbits")

    flow_control = protocol_transport.flow_control if protocol_transport else None
    if flow_control is None:
        flow_control = optional_args.get("flow_control")

    return {
        "port": selected_port.strip(),
        "baudrate": int(baudrate) if baudrate is not None else 9600,
        "timeout_s": round((float(timeout_ms) / 1000.0), 3) if timeout_ms is not None else 1.0,
        "databits": databits,
        "parity": parity,
        "stopbits": stopbits,
        "flow_control": flow_control,
    }


def _serial_factory_kwargs(
    *,
    port: str,
    baudrate: int,
    timeout_s: float,
    databits: int | None,
    parity: str | None,
    stopbits: float | None,
    flow_control: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "port": port,
        "baudrate": baudrate,
        "timeout": timeout_s,
    }
    if databits is not None:
        kwargs["bytesize"] = databits
    if parity:
        kwargs["parity"] = parity
    if stopbits is not None:
        kwargs["stopbits"] = stopbits
    flow = (flow_control or "").strip().lower()
    if flow in {"xonxoff", "software", "software_xonxoff"}:
        kwargs["xonxoff"] = True
    elif flow in {"rtscts", "hardware", "hardware_rtscts"}:
        kwargs["rtscts"] = True
    elif flow in {"dsrdtr"}:
        kwargs["dsrdtr"] = True
    return kwargs


def build_real_serial_transport(
    *,
    port: str,
    baudrate: int,
    timeout_s: float,
    databits: int | None = None,
    parity: str | None = None,
    stopbits: float | None = None,
    flow_control: str | None = None,
    serial_factory: Any | None = None,
) -> RealSerialTransport:
    allowed, reason = real_device_gate(port=port)
    if not allowed:
        raise RuntimeError(reason)

    if serial_factory is None:
        serial_factory = serial.Serial

    connection = serial_factory(
        **_serial_factory_kwargs(
            port=port.strip(),
            baudrate=baudrate,
            timeout_s=timeout_s,
            databits=databits,
            parity=parity,
            stopbits=stopbits,
            flow_control=flow_control,
        )
    )
    return RealSerialTransport(serial_port=connection)


def execute_serial_command(
    payload: bytes,
    *,
    port: str,
    baudrate: int,
    timeout_s: float,
    databits: int | None = None,
    parity: str | None = None,
    stopbits: float | None = None,
    flow_control: str | None = None,
    read_size: int = 4096,
    serial_factory: Any | None = None,
) -> dict[str, Any]:
    start = perf_counter()
    allowed, reason = real_device_gate(port=port)
    if not allowed:
        return {
            "status": "blocked",
            "success": False,
            "message": reason,
            "transport_type": "serial",
        }

    transport: RealSerialTransport | None = None
    try:
        transport = build_real_serial_transport(
            port=port,
            baudrate=baudrate,
            timeout_s=timeout_s,
            databits=databits,
            parity=parity,
            stopbits=stopbits,
            flow_control=flow_control,
            serial_factory=serial_factory,
        )
        bytes_written = transport.write(payload)
        response = transport.read(read_size)
        elapsed_ms = round((perf_counter() - start) * 1000, 3)
        return {
            "status": "success",
            "success": True,
            "transport_type": "serial",
            "port": port,
            "baudrate": baudrate,
            "timeout_s": timeout_s,
            "bytes_written": bytes_written,
            "response_bytes": response,
            "response_text": response.decode("utf-8", errors="replace"),
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = round((perf_counter() - start) * 1000, 3)
        return {
            "status": "error",
            "success": False,
            "transport_type": "serial",
            "port": port,
            "baudrate": baudrate,
            "timeout_s": timeout_s,
            "message": str(exc),
            "elapsed_ms": elapsed_ms,
        }
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
