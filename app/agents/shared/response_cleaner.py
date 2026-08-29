"""Response cleaning — strips reasoning preambles from LLM output.

Extracted from agent.py to allow reuse across agents without circular imports.
"""

from __future__ import annotations


# Markers that indicate the start of actual user-facing content.
_REASONING_MARKERS = [
    "\n\nHello!",
    "\n\nHello! I'm",
    "\n\nAs ChemAutoAgent",
    "\n\nBased on my instructions",
    "\n\nWould you like to:",
    # Chinese response-start markers
    "\n\n你好！",
    "\n\n您好！",
    "\n\n我是",
    "\n\n作为 ChemAutoAgent",
    "\n\n根据当前实现",
    "\n\n根据当前实现边界",
    "\n\n根据当前实现状态",
]

# Prefixes that indicate reasoning/thinking rather than user-facing output.
_REASONING_PREFIXES = (
    # English
    "The user has", "The user is", "The user wants", "The user asked",
    "I should", "I need to", "Based on my instructions", "Let me provide",
    "Let me analyze", "This is a general question", "According to my description",
    # Chinese reasoning-preface leakage
    "用户说", "用户上传", "用户已经", "用户选择", "用户想要",
    "用户询问", "用户需要", "用户提供", "用户回复", "用户输入",
    "我需要", "我应该", "根据我的", "根据指令",
)


def strip_reasoning_preamble(text: str) -> str:
    """Remove reasoning preamble from LLM output, returning only user-facing content.

    Handles both marker-based and prefix-based preambles in English and Chinese.
    """
    # 1. Marker-based: find the first marker and return content after it.
    for marker in _REASONING_MARKERS:
        index = text.find(marker)
        if index > 0:
            return text[index + 2:].strip()

    # 2. Prefix-based: iteratively strip known reasoning prefixes.
    stripped = text.strip()
    while stripped.startswith(_REASONING_PREFIXES):
        parts = stripped.split("\n\n", 1)
        if len(parts) != 2:
            break
        stripped = parts[1].strip()
    if stripped != text.strip():
        return stripped

    return text


def looks_like_reasoning_preamble(text: str) -> bool:
    """Check if text starts with a known reasoning prefix (not user-facing)."""
    normalized = text.strip()
    if not normalized:
        return False
    return normalized.startswith(_REASONING_PREFIXES)
