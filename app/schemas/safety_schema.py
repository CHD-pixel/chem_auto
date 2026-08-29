from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import (
    EvidenceRef,
    FunctionCategory,
    ProtocolLayer,
    SchemaMeta,
    Severity,
    SideEffectLevel,
    RiskLevel,
)


class GlobalConstraint(BaseModel):
    rule_id: str
    description: str
    severity: Severity
    applies_to: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ParameterConstraint(BaseModel):
    parameter_name: str
    type_hint: Optional[str] = None

    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: list[Any] = Field(default_factory=list)

    @field_validator("allowed_values", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return v if v is not None else []

    unit: Optional[str] = None
    required: bool = True
    default_value: Optional[Any] = None

    description: Optional[str] = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class FunctionSafetyRule(BaseModel):
    function_name: str
    function_category: FunctionCategory
    side_effect_level: SideEffectLevel
    risk_level: RiskLevel = "low"

    parameter_constraints: dict[str, ParameterConstraint] = Field(default_factory=dict)

    preconditions: list[str] = Field(default_factory=list)
    required_states: list[str] = Field(default_factory=list)
    forbidden_states: list[str] = Field(default_factory=list)

    postconditions: list[str] = Field(default_factory=list)
    verifier_function: Optional[str] = None

    requires_user_confirmation_before: bool = False
    requires_user_confirmation_after: bool = False

    requires_restore_after_test: bool = False

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_list_items(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for field_name in ("preconditions", "postconditions", "required_states", "forbidden_states"):
            raw = data.get(field_name)
            if not isinstance(raw, list):
                continue
            converted: list[str] = []
            for item in raw:
                if isinstance(item, str):
                    converted.append(item)
                elif isinstance(item, dict):
                    desc = item.get("description", item.get("desc", str(item)))
                    converted.append(str(desc))
                else:
                    converted.append(str(item))
            data[field_name] = converted
        return data


class ForbiddenSequence(BaseModel):
    sequence: list[str]
    reason: str
    severity: Literal["warning", "critical"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class StateRestorationRule(BaseModel):
    function_name: str
    has_side_effect: bool

    snapshot_before_test: list[str] = Field(default_factory=list)
    restore_method: Optional[str] = None
    restore_arguments_from_snapshot: dict[str, str] = Field(default_factory=dict)
    restore_verifier: Optional[str] = None

    failure_policy: Literal[
        "continue",
        "retry",
        "stop_and_require_human",
    ] = "stop_and_require_human"

    @field_validator("failure_policy", mode="before")
    @classmethod
    def _normalize_failure_policy(cls, v: Any) -> Any:
        if v is None:
            return "stop_and_require_human"
        normalized = str(v).strip().lower()
        if normalized in {"", "none", "unknown", "n/a", "na"}:
            return "stop_and_require_human"
        return normalized

    @field_validator("snapshot_before_test", mode="before")
    @classmethod
    def _none_to_list(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [x for x in v if x is not None]
        return v

    @field_validator("restore_arguments_from_snapshot", mode="before")
    @classmethod
    def _none_to_dict(cls, v):
        return v if v is not None else {}

    description: Optional[str] = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ShutdownStep(BaseModel):
    step: int
    action_name: str
    description: Optional[str] = None
    required: bool = True
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class EmergencyAction(BaseModel):
    trigger: str
    required_action: str
    severity: Literal["warning", "critical"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class SafetySchema(SchemaMeta):
    source_agent: Literal["SafetySchemaAgent"] = "SafetySchemaAgent"

    global_constraints: list[GlobalConstraint] = Field(default_factory=list)

    function_safety: dict[str, FunctionSafetyRule] = Field(default_factory=dict)

    forbidden_sequences: list[ForbiddenSequence] = Field(default_factory=list)

    state_restoration_rules: dict[str, StateRestorationRule] = Field(default_factory=dict)

    shutdown_requirements: list[ShutdownStep] = Field(default_factory=list)
    emergency_actions: list[EmergencyAction] = Field(default_factory=list)
