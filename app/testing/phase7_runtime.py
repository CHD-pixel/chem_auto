from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import sys
from types import ModuleType
from typing import Any


def _build_module_from_code(code: str, module_name: str = "candidate_driver_runtime") -> ModuleType:
    module = importlib.util.module_from_spec(
        importlib.machinery.ModuleSpec(module_name, loader=None)
    )
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(code, f"{module_name}.py", "exec"), module.__dict__)
        return module
    except Exception:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        raise


def _make_placeholder_value(parameter: inspect.Parameter) -> Any:
    if parameter.default is not inspect._empty:
        return parameter.default
    name = parameter.name.lower()
    annotation = parameter.annotation
    if name == "port":
        return "placeholder_port"
    if annotation in (float, "float") or "timeout" in name:
        return 1.0
    if annotation in (bool, "bool"):
        return False
    if annotation in (bytes, "bytes"):
        return b""
    if annotation in (int, "int"):
        return 1
    if "host" in name or "ip" in name:
        return "127.0.0.1"
    return "placeholder"
