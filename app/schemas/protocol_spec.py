from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import (
    EvidenceRef,
    ProtocolLayer,
    SchemaMeta,
    TransportType,
)


class DeviceIdentity(BaseModel):
    vendor: Optional[str] = None
    model: Optional[str] = None
    series: Optional[str] = None
    instrument_family: Optional[str] = None
    device_alias: Optional[str] = None


class TransportSpec(BaseModel):
    transport_type: TransportType
    port_hint: Optional[str] = None

    baudrate: Optional[int] = None
    databits: Optional[int] = None
    parity: Optional[str] = None
    stopbits: Optional[float] = None
    flow_control: Optional[str] = None

    ip: Optional[str] = None
    port: Optional[int] = None

    timeout_ms: Optional[int] = None
    retry_count: Optional[int] = None
    retry_interval_ms: Optional[int] = None

    @field_validator("flow_control", mode="before")
    @classmethod
    def _coerce_flow_control(cls, v: Any) -> Any:
        if isinstance(v, bool):
            return None
        return v


class FramingSpec(BaseModel):
    encoding: Literal["ascii", "utf-8", "hex", "binary", "unknown"]
    head_bytes: list[str] = Field(default_factory=list)
    terminator_write: Optional[str] = None
    terminator_read: Optional[str] = None

    @field_validator("terminator_write", "terminator_read", mode="before")
    @classmethod
    def _coerce_terminator(cls, v: Any) -> Any:
        if isinstance(v, dict) and "bytes" in v:
            return "".join(chr(b) for b in v["bytes"])
        return v

    length_field_bytes: Optional[int] = None
    length_rule: Optional[str] = None

    command_id_bytes: Optional[int] = None
    data_field_rule: Optional[str] = None

    checksum_required: bool = False
    checksum_bytes: Optional[int] = None
    checksum_rule: Optional[str] = None

    byte_order: Literal["big_endian", "little_endian", "mixed", "unknown"] = "unknown"


class CommandPattern(BaseModel):
    action_name: str
    command_id: Optional[str] = None
    command_format: str
    request_data_format: Optional[str] = None

    argument_placeholders: list[str] = Field(default_factory=list)
    response_expected: bool = True

    response_pattern_ref: Optional[str] = None
    parser_hint: Optional[str] = None

    side_effect_hint: Literal["none", "low", "medium", "high", "unknown"] = "unknown"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("command_id", mode="before")
    @classmethod
    def _coerce_command_id(cls, v: Any) -> Any:
        """Accept int/float command IDs (MODBUS registers) as strings."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return str(int(v) if isinstance(v, float) and v == int(v) else v)
        if isinstance(v, str):
            return v.strip() or None
        return str(v)


class ResponsePattern(BaseModel):
    pattern_id: Optional[str] = None
    command_id: Optional[str] = None
    response_format: str = "unknown"
    status_field: Optional[str] = None

    @field_validator("command_id", mode="before")
    @classmethod
    def _coerce_command_id(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return str(int(v) if isinstance(v, float) and v == int(v) else v)
        if isinstance(v, str):
            return v.strip() or None
        return str(v)

    @field_validator("response_format", mode="before")
    @classmethod
    def _coerce_response_format(cls, v: Any) -> Any:
        return v if v is not None else "unknown"

    @field_validator("success_values", "failure_values", "busy_values", mode="before")
    @classmethod
    def _coerce_value_lists(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item) for item in v]
        if isinstance(v, dict):
            code = v.get("code", v.get("error_code", "UNKNOWN"))
            desc = v.get("description", v.get("desc", ""))
            text = f"[{code}] {desc}".rstrip()
            return [text]
        return [str(v)]

    success_values: list[str] = Field(default_factory=list)
    failure_values: list[str] = Field(default_factory=list)
    busy_values: list[str] = Field(default_factory=list)
    data_format: Optional[str] = None
    parser_strategy: Optional[str] = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class HandshakeStep(BaseModel):
    step: int
    action_name: str
    command_id: Optional[str] = None
    expected_response: Optional[str] = None
    required: bool = True
    failure_hint: Optional[str] = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("expected_response", mode="before")
    @classmethod
    def _coerce_expected_response(cls, v: Any) -> Any:
        if v is None:
            return None
        return str(v)


class TimingConstraints(BaseModel):
    inter_command_delay_ms: Optional[int] = None
    response_wait_ms: Optional[int] = None
    warmup_required: Optional[bool] = None
    warmup_ms: Optional[int] = None
    stabilization_wait_ms: Optional[int] = None
    polling_interval_ms: Optional[int] = None
    max_polling_attempts: Optional[int] = None


class ErrorSemantics(BaseModel):
    error_response_patterns: list[str] = Field(default_factory=list)
    busy_response_patterns: list[str] = Field(default_factory=list)
    retryable_conditions: list[str] = Field(default_factory=list)
    non_retryable_conditions: list[str] = Field(default_factory=list)
    exception_mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_exception_mapping(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        em = data.get("exception_mapping")
        if isinstance(em, dict):
            data = dict(data)
            data["exception_mapping"] = {
                k: (v.get("description", str(v)) if isinstance(v, dict) else str(v))
                for k, v in em.items()
            }
        return data

    @field_validator(
        "error_response_patterns",
        "busy_response_patterns",
        "retryable_conditions",
        "non_retryable_conditions",
        mode="before",
    )
    @classmethod
    def _coerce_items(cls, v):
        """Convert dict items (error objects) to their string representation."""
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            if isinstance(item, dict):
                code = item.get("error_code", item.get("code", "UNKNOWN"))
                desc = item.get("description", item.get("desc", ""))
                result.append(f"[{code}] {desc}".rstrip())
            else:
                result.append(str(item))
        return result


class ProtocolSpec(SchemaMeta):
    source_agent: Literal["ProtocolExtractorAgent"] = "ProtocolExtractorAgent"

    device_identity: DeviceIdentity
    transport: TransportSpec
    framing: FramingSpec

    command_patterns: list[CommandPattern]
    response_patterns: list[ResponsePattern] = Field(default_factory=list)
    handshake_sequence: list[HandshakeStep] = Field(default_factory=list)

    timing_constraints: TimingConstraints
    error_semantics: ErrorSemantics

    protocol_constraints: list[str] = Field(default_factory=list)
