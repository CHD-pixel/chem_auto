from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import (
    EvidenceRef,
    FunctionCategory,
    ProtocolLayer,
    SchemaMeta,
    SideEffectLevel,
    TransportType,
)
from app.schemas.function_catalog import LifecycleSequences


class BlueprintInputsSummary(BaseModel):
    protocol_spec_state_key: str = "protocol_spec"
    safety_schema_state_key: str = "safety_schema"
    function_catalog_state_key: str = "function_catalog"

    protocol_spec_version: Optional[str] = None
    safety_schema_version: Optional[str] = None
    function_catalog_version: Optional[str] = None

    manual_artifact_name: Optional[str] = None


class SelectedTemplate(BaseModel):
    template_mode: Literal["plain_template", "skill", "none"] = "none"
    template_name: Optional[str] = None
    template_version: Optional[str] = None

    driver_skeleton_ref: Optional[str] = None
    protocol_helper_ref: Optional[str] = None
    safety_template_ref: Optional[str] = None
    test_template_ref: Optional[str] = None

    selection_reason: Optional[str] = None


class DriverTransportConfig(BaseModel):
    transport_type: TransportType = "unknown"

    required_constructor_args: list[str] = Field(default_factory=list)
    optional_constructor_args: dict[str, Any] = Field(default_factory=dict)

    default_timeout_ms: Optional[int] = None
    connection_notes: list[str] = Field(default_factory=list)


class DriverPacketModel(BaseModel):
    head_bytes: list[str] = Field(default_factory=list)
    length_rule: Optional[str] = None
    command_id_rule: Optional[str] = None
    data_rule: Optional[str] = None
    checksum_rule: Optional[str] = None
    byte_order: Literal["big_endian", "little_endian", "mixed", "unknown"] = "unknown"

    @field_validator("byte_order", mode="before")
    @classmethod
    def _normalize_byte_order(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        mapping = {"big": "big_endian", "little": "little_endian"}
        return mapping.get(v.lower(), v)


class CoreMethodBlueprint(BaseModel):
    method_name: str
    purpose: str
    required: bool = True
    implementation_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        # LLM often outputs "name"/"description" instead of "method_name"/"purpose"
        if "method_name" not in data and "name" in data:
            data["method_name"] = data.pop("name")
        if "purpose" not in data and "description" in data:
            data["purpose"] = data.pop("description")
        # LLM might use "notes" instead of "implementation_notes"
        if "implementation_notes" not in data and "notes" in data:
            notes = data.pop("notes")
            if isinstance(notes, list):
                data["implementation_notes"] = notes
            elif isinstance(notes, str):
                data["implementation_notes"] = [notes]
        if isinstance(data.get("implementation_notes"), str):
            data["implementation_notes"] = [data["implementation_notes"]]
        return data


class ProtocolHelperBlueprint(BaseModel):
    helper_name: str
    purpose: str
    source_protocol_fields: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        # LLM often outputs "name"/"description" instead of "helper_name"/"purpose"
        if "helper_name" not in data and "name" in data:
            data["helper_name"] = data.pop("name")
        if "helper_name" not in data and "method_name" in data:
            data["helper_name"] = data.pop("method_name")
        if "purpose" not in data and "description" in data:
            data["purpose"] = data.pop("description")
        if "implementation_notes" not in data and "notes" in data:
            notes = data.pop("notes")
            if isinstance(notes, list):
                data["implementation_notes"] = notes
            elif isinstance(notes, str):
                data["implementation_notes"] = [notes]
        # Coerce string implementation_notes to list
        if isinstance(data.get("implementation_notes"), str):
            data["implementation_notes"] = [data["implementation_notes"]]
        return data


class ActionMethodBlueprint(BaseModel):
    function_name: str
    signature: str
    purpose: str

    function_category: FunctionCategory
    side_effect_level: SideEffectLevel

    protocol_action_binding: list[str] = Field(default_factory=list)

    implementation_strategy: Literal[
        "direct_command",
        "query_command",
        "async_sequence",
        "sync_sequence",
        "software_postprocess",
        "composite_workflow",
        "stub",
    ]

    depends_on: list[str] = Field(default_factory=list)
    verifier_function: Optional[str] = None

    parameter_constraints: dict[str, dict[str, Any]] = Field(default_factory=dict)

    parameter_guard_required: bool = False
    call_sequence_guard_required: bool = False

    restore_strategy: Literal[
        "none",
        "snapshot_and_restore",
        "readback_verify",
        "manual_review",
    ] = "none"

    @field_validator("restore_strategy", mode="before")
    @classmethod
    def _normalize_restore_strategy(cls, v: Any) -> Any:
        _valid = {"none", "snapshot_and_restore", "readback_verify", "manual_review"}
        if isinstance(v, str) and v not in _valid:
            # Model outputs function names like "stop_heating" — means restore is possible
            if "_" in v or v.islower():
                return "snapshot_and_restore"
            return "manual_review"
        return v

    user_confirmation_required_after_call: bool = False

    code_generation_notes: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_nullable_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        impl = data.get("implementation_strategy")
        if isinstance(impl, str):
            normalized_impl = impl.strip().lower()
            impl_aliases = {
                "computer_postprocess": "software_postprocess",
                "postprocess": "software_postprocess",
                "post_process": "software_postprocess",
            }
            data["implementation_strategy"] = impl_aliases.get(normalized_impl, impl)
        if data.get("restore_strategy") is None:
            data["restore_strategy"] = "none"
        if isinstance(data.get("code_generation_notes"), str):
            data["code_generation_notes"] = [data["code_generation_notes"]]
        pab = data.get("protocol_action_binding")
        if pab is None:
            data["protocol_action_binding"] = []
        elif isinstance(pab, dict):
            data["protocol_action_binding"] = [pab]
        elif isinstance(pab, str):
            data["protocol_action_binding"] = [] if pab.lower() == "none" else [pab]
        if "implementation_strategy" not in data:
            data["implementation_strategy"] = "direct_command"
        if data.get("parameter_constraints") is None:
            data["parameter_constraints"] = {}
        return data


class DriverBlueprint(BaseModel):
    module_name: str = "driver"
    driver_class_name: str = "Driver"

    base_protocol_style: Literal[
        "packet_serial",
        "scpi_text",
        "modbus_register",
        "gcode_text",
        "canopen_object",
        "scpi",
        "modbus",
        "gcode",
        "ascii",
        "canopen",
        "unknown",
    ] = "unknown"

    @field_validator("base_protocol_style", mode="before")
    @classmethod
    def _normalize_base_protocol_style(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        # Normalize old names → new names (backward compat)
        mapping = {
            "scpi_text": "scpi", "modbus_register": "modbus",
            "gcode_text": "gcode", "canopen_object": "canopen",
            "packet_serial": "ascii",
            "ascii_text": "scpi", "ascii_packet": "ascii",
            "text": "scpi", "binary": "ascii",
            "namur_text": "scpi", "namur_packet": "ascii",
        }
        return mapping.get(v.lower(), v)

    transport_config: DriverTransportConfig = Field(default_factory=DriverTransportConfig)
    packet_model: Optional[DriverPacketModel] = None

    core_methods: list[CoreMethodBlueprint] = Field(default_factory=list)
    protocol_helpers: list[ProtocolHelperBlueprint] = Field(default_factory=list)

    action_methods: dict[str, ActionMethodBlueprint] = Field(default_factory=dict)

    lifecycle_sequences: LifecycleSequences = Field(default_factory=LifecycleSequences)

    @model_validator(mode="before")
    @classmethod
    def _coerce_action_methods_to_dict(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        am = data.get("action_methods")
        if isinstance(am, list):
            converted: dict[str, Any] = {}
            for item in am:
                if isinstance(item, dict) and "function_name" in item:
                    converted[item["function_name"]] = item
                else:
                    converted[str(len(converted))] = item
            data = dict(data)
            data["action_methods"] = converted
        # protocol_helpers: LLM may output a dict keyed by helper_name, or bare strings
        ph = data.get("protocol_helpers")
        if isinstance(ph, dict):
            data = dict(data)
            data["protocol_helpers"] = list(ph.values())
        elif isinstance(ph, list):
            data = dict(data)
            data["protocol_helpers"] = [
                {"helper_name": x, "purpose": x} if isinstance(x, str) else x
                for x in ph
            ]
        # core_methods: LLM may output bare strings instead of objects
        cm = data.get("core_methods")
        if isinstance(cm, list):
            data = data if isinstance(data, dict) else dict(data)
            data["core_methods"] = [
                {"method_name": x, "purpose": x} if isinstance(x, str) else x
                for x in cm
            ]
        return data


class ValidationPolicy(BaseModel):
    parameter_checks_required: bool = True
    call_sequence_checks_required: bool = True
    pre_tool_guard_enabled: bool = True
    post_action_verification_required: bool = True

    functions_requiring_parameter_checks: list[str] = Field(default_factory=list)
    functions_requiring_sequence_checks: list[str] = Field(default_factory=list)
    functions_requiring_user_confirmation: list[str] = Field(default_factory=list)
    functions_requiring_restore: list[str] = Field(default_factory=list)

    blocked_without_evidence: bool = True

    @field_validator("blocked_without_evidence", mode="before")
    @classmethod
    def _coerce_blocked_to_bool(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "yes", "1")
        if isinstance(v, list):
            return len(v) > 0
        return bool(v)


class PublishHints(BaseModel):
    device_id_hint: Optional[str] = None
    driver_artifact_name_hint: Optional[str] = None
    manifest_artifact_name_hint: Optional[str] = None
    safety_artifact_name_hint: Optional[str] = None

    registry_update_required: bool = True
    publish_ready_conditions: list[str] = Field(default_factory=list)


class BuildBlueprint(SchemaMeta):
    source_agent: Literal["CodeArchitectAgent"] = "CodeArchitectAgent"
    instrument_type: str = "unknown"
    protocol_layer: ProtocolLayer = "UNKNOWN"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    driver_blueprint: DriverBlueprint = Field(default_factory=DriverBlueprint)
    validation_policy: ValidationPolicy = Field(default_factory=ValidationPolicy)
    inputs_summary: Optional[BlueprintInputsSummary] = None
    selected_template: Optional[SelectedTemplate] = None
    publish_hints: Optional[PublishHints] = None
