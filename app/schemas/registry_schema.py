from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.common import ProtocolLayer


class RegistryDeviceEntry(BaseModel):
    device_id: str
    device_name: str

    instrument_type: str
    protocol_layer: ProtocolLayer

    latest_version: str
    available_versions: list[str] = Field(default_factory=list)

    active_manifest_artifact_name: str
    active_driver_artifact_name: str
    active_safety_artifact_name: str

    available_functions: list[str] = Field(default_factory=list)

    status: Literal["active", "disabled", "deprecated", "failed"] = "active"

    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    notes: list[str] = Field(default_factory=list)


class RegistrySchema(BaseModel):
    registry_version: str = "0.1.0"

    devices: dict[str, RegistryDeviceEntry] = Field(default_factory=dict)

    updated_at: Optional[str] = None
