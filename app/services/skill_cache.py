"""Shared skill instructions cache — loaded once at import time.

The SKILL_CACHE is used by both CodeArchitectAgent (for prompt injection)
and BlueprintAssembler (for protocol-specific command table parsing).
"""

from __future__ import annotations

import pathlib

from google.adk.skills import load_skill_from_dir

_SKILLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "skills"

SKILL_CACHE: dict[str, str] = {}
for _skill_name in (
    "scpi-generic", "ascii-packet-device", "modbus-generic",
    "canopen-generic", "gcode-generic", "lxi-scpi", "binary-frame",
):
    _skill_dir = _SKILLS_DIR / _skill_name
    if _skill_dir.is_dir():
        try:
            _skill = load_skill_from_dir(_skill_dir)
            SKILL_CACHE[_skill_name] = _skill.instructions or ""
        except Exception:
            SKILL_CACHE[_skill_name] = ""


def get_skill_instructions(skill_name: str) -> str:
    return SKILL_CACHE.get(skill_name, "")
