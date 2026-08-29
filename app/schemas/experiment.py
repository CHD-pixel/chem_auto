"""Schemas for experiment plans and experiment logs."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ExperimentStep(BaseModel):
    """A single step in an experiment plan."""

    step_number: int
    device_id: str
    function_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    description: str = ""  # human-readable, e.g. "向烧杯中加入5ml硫酸"


class ExperimentPlan(BaseModel):
    """A saved experiment plan — reusable template for experiments."""

    plan_id: str
    name: str
    description: str = ""
    steps: list[ExperimentStep] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class StepExecution(BaseModel):
    """Record of a single step execution during an experiment."""

    step_number: int
    device_id: str
    function_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    input_params: dict[str, Any] = Field(default_factory=dict)
    output_value: Optional[Any] = None
    status: Literal["success", "failed", "skipped"] = "success"
    error_message: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""


class ExperimentLog(BaseModel):
    """A record of an experiment execution — captures everything that happened."""

    log_id: str
    plan_id: Optional[str] = None
    experiment_name: str = ""
    steps: list[StepExecution] = Field(default_factory=list)
    overall_status: Literal["running", "completed", "failed", "aborted"] = "running"
    started_at: str = ""
    finished_at: Optional[str] = None


class PlanIndexEntry(BaseModel):
    """Lightweight entry in the plans index."""

    plan_id: str
    name: str
    description: str = ""
    step_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class LogIndexEntry(BaseModel):
    """Lightweight entry in the logs index."""

    log_id: str
    experiment_name: str = ""
    plan_id: Optional[str] = None
    overall_status: str = "running"
    step_count: int = 0
    started_at: str = ""
    finished_at: Optional[str] = None
