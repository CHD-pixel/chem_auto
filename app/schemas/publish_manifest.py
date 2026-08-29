from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.common import (
    EvidenceRef,
    FunctionCategory,
    ProtocolLayer,
    SideEffectLevel,
    RiskLevel,
)


class PublishedFunctionInfo(BaseModel):
    function_name: str

    signature: str
    function_category: FunctionCategory
    side_effect_level: SideEffectLevel
    risk_level: RiskLevel = "low"

    requires_guardrail: bool = True
    requires_user_confirmation_after_call: bool = False

    description: Optional[str] = None


class PublishManifest(BaseModel):
    manifest_version: str = "0.1.0"

    device_id: str
    device_name: str

    instrument_type: str
    protocol_layer: ProtocolLayer

    driver_version: str

    driver_artifact_name: str
    safety_artifact_name: str
    function_catalog_artifact_name: Optional[str] = None
    build_blueprint_artifact_name: Optional[str] = None

    available_functions: dict[str, PublishedFunctionInfo]

    published_at: Optional[str] = None
    published_by: Optional[str] = None

    test_result_artifact_name: Optional[str] = None
    test_status: Literal["passed", "failed", "partial", "manual_review"]

    is_active: bool = True
    deprecated: bool = False

    notes: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
