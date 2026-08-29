from __future__ import annotations

from typing import Any, Iterable

from app.services.artifact_service import build_artifact_summary


def load_state_value(state: dict[str, Any], key: str, default: Any = None) -> Any:
    return state.get(key, default)


def collect_missing_state_keys(state: dict[str, Any], required_keys: Iterable[str]) -> list[str]:
    return [key for key in required_keys if key not in state or state[key] in (None, "", [])]


def has_required_state_keys(state: dict[str, Any], required_keys: Iterable[str]) -> bool:
    return not collect_missing_state_keys(state, required_keys)


def load_required_state_values(
    state: dict[str, Any],
    required_keys: Iterable[str],
) -> tuple[dict[str, Any], list[str]]:
    missing = collect_missing_state_keys(state, required_keys)
    values = {
        key: state[key]
        for key in required_keys
        if key not in missing
    }
    return values, missing


def write_artifact_summary(
    state: dict[str, Any],
    summary_key: str,
    artifact_name: str,
    summary: str,
) -> dict[str, Any]:
    payload = build_artifact_summary(artifact_name, summary)
    state[summary_key] = payload
    return payload


def write_artifact_summaries(
    state: dict[str, Any],
    summary_key: str,
    summaries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payload = dict(summaries)
    state[summary_key] = payload
    return payload
