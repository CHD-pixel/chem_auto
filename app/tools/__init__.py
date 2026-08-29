"""Tool registry — provides tool lists and skill toolset.

Multi-agent architecture: each agent has its own tool list.
Tools enforce their own preconditions internally.
"""

from __future__ import annotations

import pathlib


def _build_skill_toolset():
    """Build a SkillToolset from all skill directories."""
    from google.adk.skills import load_skill_from_dir
    from google.adk.tools.skill_toolset import SkillToolset

    skills_dir = pathlib.Path(__file__).resolve().parent.parent / "skills"
    skills = []
    for d in sorted(skills_dir.iterdir()):
        if d.is_dir() and (d / "SKILL.md").exists():
            try:
                skill = load_skill_from_dir(str(d))
                skills.append(skill)
            except Exception:
                pass
    return SkillToolset(skills=skills) if skills else None
