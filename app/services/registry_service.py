from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.constants.artifact_names import REGISTRY_ARTIFACT
from app.schemas.registry_schema import RegistryDeviceEntry, RegistrySchema
from app.services.artifact_service import load_json_artifact, save_json_artifact

# Process-level locks for shared user-scoped artifacts
_registry_lock = asyncio.Lock()
_manual_registry_lock = asyncio.Lock()


MANUAL_REGISTRY_ARTIFACT = "user_registry/manuals.json"


async def load_manual_registry(context: Any) -> list[dict]:
    rows = await load_json_artifact(context, MANUAL_REGISTRY_ARTIFACT, default=[])
    return list(rows)


async def save_manual_registry(context: Any, rows: list[dict]) -> int:
    return await save_json_artifact(context, MANUAL_REGISTRY_ARTIFACT, rows)


async def append_manual_registry_entry(context: Any, entry: dict) -> list[dict]:
    async with _manual_registry_lock:
        rows = await load_manual_registry(context)
        rows.append(entry)
        await save_manual_registry(context, rows)
        return rows


async def load_device_registry(context: Any) -> RegistrySchema:
    payload = await load_json_artifact(
        context,
        REGISTRY_ARTIFACT,
        default=RegistrySchema().model_dump(),
    )
    return RegistrySchema(**payload)


async def save_device_registry(context: Any, registry: RegistrySchema) -> int:
    registry.updated_at = datetime.now(timezone.utc).isoformat()
    return await save_json_artifact(context, REGISTRY_ARTIFACT, registry.model_dump())


def _merge_available_versions(existing: RegistryDeviceEntry | None, entry: RegistryDeviceEntry) -> list[str]:
    merged = set(entry.available_versions)
    merged.add(entry.latest_version)
    if existing is not None:
        merged.update(existing.available_versions)
        merged.add(existing.latest_version)
    return sorted(merged)


async def upsert_device_registry_entry(
    context: Any, entry: RegistryDeviceEntry
) -> RegistrySchema:
    async with _registry_lock:
        registry = await load_device_registry(context)
        now = datetime.now(timezone.utc).isoformat()
        existing = registry.devices.get(entry.device_id)
        entry.available_versions = _merge_available_versions(existing, entry)
        if existing is not None and entry.created_at is None:
            entry.created_at = existing.created_at
        if entry.created_at is None:
            entry.created_at = now
        entry.updated_at = now
        registry.devices[entry.device_id] = entry
        await save_device_registry(context, registry)
        return registry


async def get_device_registry_entry(
    context: Any, device_id: str
) -> RegistryDeviceEntry | None:
    registry = await load_device_registry(context)
    return registry.devices.get(device_id)


async def list_active_device_entries(context: Any) -> list[RegistryDeviceEntry]:
    registry = await load_device_registry(context)
    return [
        entry
        for entry in registry.devices.values()
        if entry.status == "active"
    ]


async def register_device_version(
    context: Any,
    entry: RegistryDeviceEntry,
) -> RegistrySchema:
    return await upsert_device_registry_entry(context, entry)


async def delete_device_registry_entry(
    context: Any, device_id: str
) -> RegistryDeviceEntry | None:
    """Remove a device from the registry and return the removed entry."""
    async with _registry_lock:
        registry = await load_device_registry(context)
        entry = registry.devices.pop(device_id, None)
        if entry is None:
            return None
        await save_device_registry(context, registry)
        return entry


async def list_callable_device_entries(context: Any) -> list[RegistryDeviceEntry]:
    entries = await list_active_device_entries(context)
    return [
        entry
        for entry in entries
        if entry.active_driver_artifact_name and entry.available_functions
    ]


async def get_driver_artifact_for_device(context: Any, device_id: str) -> str | None:
    entry = await get_device_registry_entry(context, device_id)
    if entry is None:
        return None
    return entry.active_driver_artifact_name
