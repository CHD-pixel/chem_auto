from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.common import (
    EvidenceRef,
    FunctionCategory,
    ProtocolLayer,
    SchemaMeta,
    SideEffectLevel,
    TestRole,
)


class FunctionParameter(BaseModel):
    name: str
    type_hint: str
    required: bool = True
    default_value: Optional[Any] = None
    unit: Optional[str] = None
    description: Optional[str] = None


class FunctionOutput(BaseModel):
    name: str
    type_hint: str
    description: Optional[str] = None
    unit: Optional[str] = None


class FunctionDefinition(BaseModel):
    function_name: str
    purpose: str
    signature_hint: str

    function_category: FunctionCategory
    side_effect_level: SideEffectLevel

    inputs: list[FunctionParameter] = Field(default_factory=list)
    outputs: list[FunctionOutput] = Field(default_factory=list)

    depends_on: list[str] = Field(default_factory=list)
    protocol_actions: list[str] = Field(default_factory=list)

    recommended_verifier: Optional[str] = None

    test_role: TestRole
    execution_priority: Optional[int] = None

    # Per-parameter constraints: min, max, allowed_values, unit, default_value.
    # Populated by the catalog_functions extraction agent when the manual provides
    # explicit parameter ranges. Keyed by parameter name.
    parameter_constraints: dict[str, dict[str, Any]] = Field(default_factory=dict)

    should_generate: bool = True
    generation_notes: list[str] = Field(default_factory=list)

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class LifecycleSequences(BaseModel):
    init_sequence: list[str] = Field(default_factory=list)
    runtime_sequence: list[str] = Field(default_factory=list)
    cleanup_sequence: list[str] = Field(default_factory=list)
    recommended_test_sequence: list[str] = Field(default_factory=list)


class FunctionGroups(BaseModel):
    connect: list[str] = Field(default_factory=list)
    setup: list[str] = Field(default_factory=list)
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    control: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)
    cleanup: list[str] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


class FunctionCatalog(SchemaMeta):
    source_agent: Literal["FunctionCatalogAgent"] = "FunctionCatalogAgent"

    functions: dict[str, FunctionDefinition]

    lifecycle_sequences: LifecycleSequences
    function_groups: FunctionGroups
