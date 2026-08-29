"""Build Blueprint Pydantic model from device_spec.

Replaces the stored BUILD_BLUEPRINT state key — builds the model
on-the-fly from device_spec (the single source of truth).
"""

from __future__ import annotations

from typing import Any

from app.schemas.build_blueprint import (
    ActionMethodBlueprint,
    BuildBlueprint,
    DriverBlueprint,
    DriverTransportConfig,
)


def device_spec_to_blueprint(device_spec: dict[str, Any]) -> BuildBlueprint:
    """Convert device_spec dict to BuildBlueprint Pydantic model.

    Args:
        device_spec: The unified device spec from _build_device_spec().
            Expected keys: device, connection, protocol, functions, raw_command_table.

    Returns:
        BuildBlueprint populated from device_spec data.
    """
    ds_device = device_spec.get("device", {})
    ds_connection = device_spec.get("connection", {})
    ds_protocol = device_spec.get("protocol", {})
    ds_functions = device_spec.get("functions", [])

    # ── Transport config ──────────────────────────────────────────
    transport_type = ds_connection.get("transport_type", ds_connection.get("type", "unknown"))
    optional_args: dict[str, Any] = {}
    if ds_connection.get("baudrate"):
        optional_args["baudrate"] = ds_connection["baudrate"]
    if ds_connection.get("timeout_ms"):
        optional_args["timeout"] = ds_connection["timeout_ms"] / 1000.0
    if ds_connection.get("databits"):
        optional_args["bytesize"] = ds_connection["databits"]
    if ds_connection.get("parity"):
        optional_args["parity"] = ds_connection["parity"]
    if ds_connection.get("stopbits"):
        optional_args["stopbits"] = ds_connection["stopbits"]

    transport_config = DriverTransportConfig(
        transport_type=transport_type,
        optional_constructor_args=optional_args,
        default_timeout_ms=ds_connection.get("timeout_ms"),
    )

    # ── Action methods ────────────────────────────────────────────
    action_methods: dict[str, ActionMethodBlueprint] = {}
    for func in ds_functions:
        fname = func.get("function_name", "")
        if not fname:
            continue
        action_methods[fname] = ActionMethodBlueprint(
            function_name=fname,
            signature=func.get("signature", f"def {fname}(self) -> Any"),
            purpose=func.get("purpose", ""),
            function_category=func.get("function_category", "read"),
            side_effect_level=func.get("side_effect_level", "none"),
            protocol_action_binding=func.get("protocol_action_binding", []),
            implementation_strategy=func.get("implementation_strategy", "direct_command"),
            parameter_constraints=func.get("parameter_constraints", {}),
        )

    # ── Protocol style ────────────────────────────────────────────
    protocol_family = (ds_device.get("protocol_family") or "unknown").lower()
    style_mapping = {
        "modbus": "modbus", "modbus-rtu": "modbus", "modbus-tcp": "modbus",
        "scpi": "scpi", "lxi": "scpi",
        "ascii": "ascii",
        "gcode": "gcode",
        "canopen": "canopen",
        "binary": "ascii",
    }
    base_protocol_style = style_mapping.get(protocol_family, "unknown")

    # ── Device identity ───────────────────────────────────────────
    manufacturer = ds_device.get("manufacturer", "")
    model = ds_device.get("model", "")
    instrument_type = f"{manufacturer} {model}".strip() if manufacturer and model else (manufacturer or model or "unknown")
    device_id = instrument_type.lower().replace(" ", "_").replace("-", "_")

    # ── Driver blueprint ──────────────────────────────────────────
    driver_blueprint = DriverBlueprint(
        module_name=f"{device_id}_driver",
        driver_class_name="".join(w.capitalize() for w in device_id.split("_") if w) + "Driver",
        base_protocol_style=base_protocol_style,
        transport_config=transport_config,
        action_methods=action_methods,
    )

    return BuildBlueprint(
        instrument_type=instrument_type,
        protocol_layer=ds_device.get("protocol_family", "UNKNOWN"),
        driver_blueprint=driver_blueprint,
    )
