from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.common import (
    EvidenceRef,
    FunctionCategory,
    SideEffectLevel,
    UserConfirmation,
)


class ConfirmationRequest(BaseModel):
    confirmation_id: str

    device_id: Optional[str] = None
    function_name: str

    function_category: FunctionCategory
    side_effect_level: SideEffectLevel

    arguments: dict[str, Any] = Field(default_factory=dict)

    expected_effect: str
    confirmation_question: str

    tool_result_summary: Optional[str] = None

    stdout_artifact_name: Optional[str] = None
    stderr_artifact_name: Optional[str] = None
    traceback_artifact_name: Optional[str] = None

    status: Literal[
        "pending",
        "confirmed",
        "rejected",
        "uncertain",
        "expired",
    ] = "pending"

    user_raw_reply: Optional[str] = None
    normalized_user_reply: Optional[UserConfirmation] = None

    created_by: str = "CodeTestAgent"
    created_at: Optional[str] = None


class ConfirmationResult(BaseModel):
    confirmation_id: str
    function_name: str

    tool_success: bool
    user_confirmation: UserConfirmation

    final_verdict: Literal["pass", "fail", "manual_review"]

    reason: str

    should_continue_testing: bool
    should_trigger_repair: bool
    should_stop_for_human: bool

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
