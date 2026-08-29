"""Three-layer validation for extracted command tables.

Layer 1: Format validation — can the raw_table be parsed?
Layer 2: Reasonability validation — protocol-specific sanity checks.
Layer 3: LLM cross-validation — only when layers 1-2 find issues.

Usage:
    result = validate_cmd_table(raw_table, protocol_family, context_text)
    if result.ok:
        # use result.functions
    elif result.needs_llm_fix:
        # run LLM cross-validation with result.issues
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Result of command table validation."""
    ok: bool = False
    functions: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_llm_fix: bool = False
    fixed_raw_table: str | None = None


def validate_cmd_table(
    raw_table: str,
    protocol_family: str,
    context_text: str = "",
) -> ValidationResult:
    """Run three-layer validation on extracted command table.

    Args:
        raw_table: Pipe-delimited command table from flat_cmd_table extractor.
        protocol_family: Protocol type (MODBUS, SCPI, ASCII, BINARY, etc.)
        context_text: Original OCR text (for LLM cross-validation prompt).

    Returns:
        ValidationResult with ok=True if valid, or issues/needs_llm_fix if not.
    """
    result = ValidationResult()

    if not raw_table or not raw_table.strip():
        result.issues.append("raw_table is empty")
        result.needs_llm_fix = True
        return result

    # ── Layer 1: Format validation ────────────────────────────────
    functions = _parse_raw_table(raw_table)
    if not functions:
        result.issues.append("raw_table could not be parsed into any functions")
        result.needs_llm_fix = True
        return result

    # Check each function has required fields
    for func in functions:
        fname = func.get("function_name", "")
        cmd = func.get("protocol_action_binding", [])
        if not fname:
            result.issues.append(f"Function missing name: {func}")
        if not cmd:
            result.issues.append(f"Function '{fname}' missing command binding")

    # ── Layer 2: Reasonability validation ─────────────────────────
    pf = protocol_family.upper().replace("_", "-")
    layer2_issues = _validate_by_protocol(functions, pf)
    result.issues.extend(layer2_issues)

    # ── Layer 2: Cross-checks ─────────────────────────────────────
    cross_issues = _cross_validate(functions, pf)
    result.warnings.extend(cross_issues)

    # ── Result ────────────────────────────────────────────────────
    if result.issues:
        result.needs_llm_fix = True
    else:
        result.ok = True
        result.functions = functions

    return result


def _parse_raw_table(raw_table: str) -> list[dict]:
    """Parse pipe-delimited raw_table into function dicts."""
    functions = []
    lines = raw_table.strip().replace("\\n", "\n").split("\n")

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|---" in line or "| --" in line:
            continue
        # Skip header rows
        if any(h in line.lower() for h in ("| address", "| 地址", "| register", "| command", "| name", "| 名称")):
            continue

        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            while parts and not parts[0]:
                parts.pop(0)
            while parts and not parts[-1]:
                parts.pop()
        else:
            parts = line.split()

        if len(parts) < 2:
            continue

        cmd_id = parts[0]
        name = parts[1]
        rw_hint = parts[2] if len(parts) > 2 else ""

        if not cmd_id or cmd_id.lower() in ("command", "register", "address"):
            continue
        if name.lower() in ("name", "名称", "description", "说明", "function", "purpose", ""):
            continue

        # Normalize name
        safe_name = name.lower().replace(" ", "_").replace("-", "_").replace("/", "_").replace(".", "_")
        safe_name = re.sub(r'\(([^)]+)\)', r'_\1', safe_name)
        safe_name = re.sub(r'_+', '_', safe_name).strip("_")

        # Strip verb prefixes
        for vp in ("get_", "set_", "read_", "write_", "start_", "stop_"):
            if safe_name.startswith(vp):
                safe_name = safe_name[len(vp):]
                break

        if not safe_name or safe_name == "_":
            continue

        # Handle non-ASCII names
        if not all(ord(c) < 128 for c in safe_name):
            eng_hint = ""
            for pi in range(3, len(parts)):
                tok = parts[pi]
                if all(ord(c) < 128 for c in tok) and len(tok) > 2:
                    eng_hint = tok.lower().replace(" ", "_").replace("-", "_").replace("/", "_")
                    break
            if eng_hint and not eng_hint.startswith(("unsigned", "float", "int", "0x")):
                safe_name = eng_hint
            else:
                safe_name = f"reg_{cmd_id.lower().replace('0x', '').replace(' ', '_')}"

        # Classify read/write
        is_read = any(k in rw_hint.lower() for k in ("r", "ro", "读", "read", "rd"))
        is_write = any(k in rw_hint.lower() for k in ("w", "wo", "写", "write", "wr", "rw"))
        if not is_read and not is_write:
            is_read = True  # default to read

        func_cat = "read" if (is_read and not is_write) else "control"

        functions.append({
            "function_name": safe_name,
            "raw_name": name,
            "command_id": cmd_id,
            "protocol_action_binding": [cmd_id],
            "function_category": func_cat,
            "read_write_hint": rw_hint,
        })

    return functions


def _validate_by_protocol(functions: list[dict], protocol_family: str) -> list[str]:
    """Protocol-specific reasonability checks."""
    issues = []

    if protocol_family in ("MODBUS", "MODBUS-RTU", "MODBUS-TCP"):
        issues.extend(_validate_modbus(functions))
    elif protocol_family in ("SCPI", "LXI"):
        issues.extend(_validate_scpi(functions))
    elif protocol_family in ("BINARY",):
        issues.extend(_validate_binary(functions))
    elif protocol_family in ("ASCII",):
        issues.extend(_validate_ascii(functions))

    return issues


def _validate_modbus(functions: list[dict]) -> list[str]:
    """MODBUS-specific validation."""
    issues = []
    for func in functions:
        cmd = func.get("command_id", "")
        try:
            addr = int(cmd, 0)
            if addr < 0 or addr > 65535:
                issues.append(f"MODBUS address out of range: {func['function_name']} = {cmd}")
        except (ValueError, TypeError):
            if cmd and not cmd.startswith("0x"):
                issues.append(f"MODBUS address not numeric: {func['function_name']} = {cmd}")
    return issues


def _validate_scpi(functions: list[dict]) -> list[str]:
    """SCPI-specific validation."""
    issues = []
    for func in functions:
        cmd = func.get("command_id", "")
        if cmd and not cmd.startswith(":") and not cmd[0].isalpha():
            issues.append(f"SCPI command format unusual: {func['function_name']} = {cmd}")
    return issues


def _validate_binary(functions: list[dict]) -> list[str]:
    """Binary protocol validation."""
    issues = []
    for func in functions:
        cmd = func.get("command_id", "")
        try:
            val = int(cmd, 0)
            if val < 0 or val > 255:
                issues.append(f"Binary command byte out of range: {func['function_name']} = {cmd}")
        except (ValueError, TypeError):
            if cmd and not cmd.startswith("0x"):
                issues.append(f"Binary command not hex/numeric: {func['function_name']} = {cmd}")
    return issues


def _validate_ascii(functions: list[dict]) -> list[str]:
    """ASCII protocol validation."""
    issues = []
    for func in functions:
        cmd = func.get("command_id", "")
        if not cmd:
            issues.append(f"ASCII command missing: {func['function_name']}")
    return issues


def _cross_validate(functions: list[dict], protocol_family: str) -> list[str]:
    """Cross-checks that don't necessarily indicate errors but are warnings."""
    warnings = []

    # Check read/write distribution
    read_count = sum(1 for f in functions if f["function_category"] == "read")
    write_count = sum(1 for f in functions if f["function_category"] != "read")

    if read_count == 0:
        warnings.append("No read functions found — all functions classified as control/write")
    if write_count == 0 and len(functions) > 3:
        warnings.append("No write/control functions found — all functions classified as read")

    # Check for duplicate function names
    names = [f["function_name"] for f in functions]
    seen = set()
    for name in names:
        if name in seen:
            warnings.append(f"Duplicate function name: {name}")
        seen.add(name)

    # Check for duplicate command IDs
    cmds = [f["command_id"] for f in functions]
    seen_cmds = set()
    for cmd in cmds:
        if cmd in seen_cmds:
            warnings.append(f"Duplicate command ID: {cmd}")
        seen_cmds.add(cmd)

    # Minimum function count
    if len(functions) < 3:
        warnings.append(f"Only {len(functions)} functions found — manual may have more commands")

    return warnings


def build_llm_validation_prompt(
    raw_table: str,
    issues: list[str],
    relevant_section: str,
) -> str:
    """Build prompt for LLM cross-validation (Layer 3).

    Args:
        raw_table: Current raw_table with issues.
        issues: List of issues found by layers 1-2.
        relevant_section: Relevant OCR text section.

    Returns:
        Prompt string for LLM.
    """
    issues_text = "\n".join(f"- {i}" for i in issues)

    return f"""以下是设备手册中提取的命令表，存在以下问题：

问题列表：
{issues_text}

当前提取的命令表：
{raw_table}

以下是手册原文中关于命令/寄存器的描述：
{relevant_section[:6000]}

请修正命令表，输出修正后的管道分隔格式：
identifier | name | read/write | data_type | range | unit | notes

要求：
1. 补充遗漏的命令
2. 修正读写分类（read/write/read-write）
3. 修正范围值格式（MIN-MAX 或 VAL1,VAL2,VAL3）
4. 命令名翻译为英文
5. 每行一个命令，用 \\n 分隔
6. 只输出修正后的命令表，不要其他内容"""
