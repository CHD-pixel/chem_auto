from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

# ── Shared enumerations ──────────────────────────────────────────────

ProtocolLayer = Literal[
    "SCPI",
    "ASCII",
    "MODBUS",
    "LXI",
    "GCODE",
    "CANOPEN",
    "BINARY",
    "UNKNOWN",
]

TransportType = Literal[
    "serial",
    "tcp",
    "usb",
    "can",
    "unknown",
]

FunctionCategory = Literal[
    "connect",
    "setup",
    "read",
    "write",
    "control",
    "config",
    "status",
    "cleanup",
    "safety",
    "unknown",
]

SideEffectLevel = Literal[
    "none",
    "low",
    "medium",
    "high",
]

RiskLevel = Literal[
    "low",
    "medium",
    "high",
    "critical",
]

Severity = Literal[
    "info",
    "warning",
    "critical",
]

TestRole = Literal[
    "smoke",
    "functional",
    "safety",
    "integration",
]

FailureScope = Literal[
    "local",
    "protocol",
    "architecture",
]

RepairLevel = Literal[
    "single_function",
    "function_cluster",
    "protocol_helper",
    "full_driver",
    "blueprint_regeneration",
    "manual_review",
]

Verdict = Literal[
    "pass",
    "fail",
    "skip",
    "manual_review",
    "blocked",
]

UserConfirmation = Literal[
    "yes",
    "no",
    "uncertain",
]

SourceType = Literal[
    "manual",
    "artifact",
    "state",
    "skill",
    "template",
    "log",
    "user",
    "tool",
    "test",
]


# ── Shared models ────────────────────────────────────────────────────


class EvidenceRef(BaseModel):
    source_type: SourceType
    source_name: str

    page: Optional[int] = None
    section: Optional[str] = None
    quote: Optional[str] = None

    artifact_name: Optional[str] = None
    state_key: Optional[str] = None

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("source_type", mode="before")
    @classmethod
    def _normalize_source_type(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        _extra_mapping = {
            "text_block": "manual", "manual_text": "manual", "ocr": "manual", "layout": "manual",
            "pdf": "manual", "table": "manual", "figure": "manual",
            "code": "artifact", "file": "artifact",
            "test_result": "test", "test_output": "test",
            "human": "user", "operator": "user",
        }
        return _extra_mapping.get(v.lower(), v)

    @model_validator(mode="before")
    @classmethod
    def _coerce_string(cls, data):
        """Accept bare marker strings (e.g. ``'P11_P11-P11'``) as evidence."""
        if isinstance(data, str):
            return {
                "source_type": "manual",
                "source_name": data,
                "confidence": 0.5,
            }
        return data


class SchemaMeta(BaseModel):
    schema_version: str = "0.1.0"
    source_agent: str

    instrument_type: str
    protocol_layer: ProtocolLayer

    @field_validator("protocol_layer", mode="before")
    @classmethod
    def _normalize_protocol_layer(cls, v: Any) -> Any:
        """Map common LLM mistakes for physical-layer or non-standard names."""
        if not isinstance(v, str):
            return v
        mapping: dict[str, str] = {
            "rs-232": "ASCII", "rs232": "ASCII",
            "rs-485": "ASCII", "rs485": "ASCII",
            "uart": "ASCII", "serial": "ASCII",
            "ethernet": "ASCII", "tcp": "ASCII", "tcp/ip": "ASCII",
            "spi": "UNKNOWN", "i2c": "UNKNOWN",
            "gpib": "SCPI", "ieee-488": "SCPI", "ieee488": "SCPI",
            "usb": "UNKNOWN", "bluetooth": "UNKNOWN", "wifi": "UNKNOWN",
            "http": "ASCII", "https": "ASCII",
            "modbus-rtu": "MODBUS", "modbus-tcp": "MODBUS", "modbus_rtu": "MODBUS", "modbus_tcp": "MODBUS",
            "namur": "UNKNOWN", "naumur": "UNKNOWN", "hart": "UNKNOWN", "foundation_fieldbus": "UNKNOWN",
            "profibus": "UNKNOWN", "ethernet/ip": "UNKNOWN",
            "binary": "BINARY", "byte": "BINARY",
        }
        result = mapping.get(v.lower())
        if result is not None:
            return result
        # Case-insensitive match against valid ProtocolLayer values
        _valid = ("SCPI", "ASCII", "MODBUS", "LXI", "GCODE", "CANOPEN", "BINARY", "UNKNOWN")
        vupper = v.upper()
        if vupper in _valid:
            return vupper
        return v

    confidence: float = Field(ge=0.0, le=1.0)

    manual_artifact_name: Optional[str] = None
    manual_segment_ids: list[str] = Field(default_factory=list)

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_segment_ids(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw = data.get("manual_segment_ids")
        if isinstance(raw, list):
            converted: list[str] = []
            for item in raw:
                if isinstance(item, str):
                    converted.append(item)
                elif isinstance(item, dict):
                    rid = item.get("region_id", item.get("id", str(item)))
                    page = item.get("page", "")
                    rtype = item.get("type", "")
                    parts = [str(rid)]
                    if rtype:
                        parts.append(f"({rtype})")
                    if page != "" and page is not None:
                        parts.append(f"p{page}")
                    converted.append(" ".join(parts))
                else:
                    converted.append(str(item))
            data["manual_segment_ids"] = converted
        return data
