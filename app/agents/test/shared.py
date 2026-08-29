"""Shared helpers for test agents."""

from __future__ import annotations

from typing import Any


# ── Serial settings ───────────────────────────────────────────────────


def merge_serial_settings(kwargs: dict[str, Any], serial_settings: dict[str, Any]) -> None:
    """Merge serial port settings into constructor kwargs.

    Maps test-framework keys (timeout_s) to driver __init__ parameter names
    (timeout).  Only overrides kwargs that are already present (i.e. parameters
    that the driver actually accepts).  Protocol-agnostic — the driver's
    connect() method creates whatever transport it needs.
    """
    _key_map = {
        "port": "port",
        "baudrate": "baudrate",
        "timeout_s": "timeout",
        "parity": "parity",
        "stopbits": "stopbits",
    }
    _DATABITS_ALIASES = ("bytesize", "databits")
    _PARITY_MAP = {
        "E": 2, "even": 2, "O": 1, "odd": 1, "N": 0, "none": 0,
        "M": 3, "mark": 3, "S": 4, "space": 4,
    }
    for src, dst in _key_map.items():
        val = serial_settings.get(src)
        if val is not None and dst in kwargs:
            if dst == "parity" and isinstance(val, str) and isinstance(kwargs.get(dst), int):
                val = _PARITY_MAP.get(val.upper(), kwargs.get(dst))
            kwargs[dst] = val
    dbits = serial_settings.get("databits")
    if dbits is not None:
        for alias in _DATABITS_ALIASES:
            if alias in kwargs:
                kwargs[alias] = dbits
                break
