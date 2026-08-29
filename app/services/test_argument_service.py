from __future__ import annotations

import inspect
from typing import Any

from app.schemas.build_blueprint import BuildBlueprint
from app.schemas.safety_schema import FunctionSafetyRule, SafetySchema


def _coerce_numeric(parameter: inspect.Parameter, value: float) -> Any:
    annotation = parameter.annotation
    if annotation in (int, "int"):
        return int(round(value))
    if annotation in (float, "float"):
        return float(value)
    if isinstance(parameter.default, int):
        return int(round(value))
    if isinstance(parameter.default, float):
        return float(value)
    return value


def _coerce_to_parameter(parameter: inspect.Parameter, value: Any) -> Any:
    annotation = parameter.annotation
    if value is None:
        return None
    if annotation in (int, "int"):
        return int(round(float(value)))
    if annotation in (float, "float"):
        return float(value)
    if annotation in (bool, "bool"):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if annotation in (bytes, "bytes"):
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")
    if isinstance(parameter.default, int) and not isinstance(parameter.default, bool):
        return int(round(float(value)))
    if isinstance(parameter.default, float):
        return float(value)
    return value


def _safe_numeric_value_plain(
    *,
    parameter: inspect.Parameter,
    constraint: dict[str, Any],
) -> Any:
    min_value = constraint.get("min_value")
    max_value = constraint.get("max_value")
    if min_value is not None and max_value is not None:
        span = max_value - min_value
        ratio = 0.10
        candidate = min_value + (span * ratio)
        candidate = max(min_value, min(candidate, max_value))
        return _coerce_numeric(parameter, candidate)
    if min_value is not None:
        return _coerce_numeric(parameter, min_value)
    if max_value is not None:
        return _coerce_numeric(parameter, max_value)
    return None


def _minimum_constraint_value_plain(parameter: inspect.Parameter, constraint: dict[str, Any]) -> Any:
    allowed = constraint.get("allowed_values") or []
    if allowed:
        numeric_values = [v for v in allowed if isinstance(v, (int, float))]
        if numeric_values:
            return _coerce_to_parameter(parameter, min(numeric_values))
        return _coerce_to_parameter(parameter, allowed[0])
    min_v = constraint.get("min_value")
    if min_v is not None:
        return _coerce_to_parameter(parameter, min_v)
    default_v = constraint.get("default_value")
    if default_v is not None:
        return _coerce_to_parameter(parameter, default_v)
    return None


def _lookup_safety_rule(function_name: str, safety_schema_payload: dict[str, Any] | None) -> FunctionSafetyRule | None:
    if not isinstance(safety_schema_payload, dict):
        return None
    try:
        schema = SafetySchema.model_validate(safety_schema_payload)
    except Exception:
        return None
    return schema.function_safety.get(function_name)


def _lookup_raw_table_constraint(
    *,
    function_name: str,
    parameter_name: str,
    raw_table: str,
    parse_func,
) -> dict[str, Any] | None:
    """Parse the raw command table to find a parameter constraint for a function.

    Used as a final fallback when both Blueprint and SafetySchema lack constraints.
    The raw_table is pipe-delimited text from flat_cmd_table extraction.
    """
    if not raw_table.strip():
        return None

    # Derive the table row name from the function name: strip get_/set_ prefix
    base_name = function_name
    for prefix in ("get_", "set_", "read_", "write_"):
        if base_name.startswith(prefix):
            base_name = base_name[len(prefix):]
            break

    # Split on both real newlines and literal \\n (from JSON string encoding)
    for line in raw_table.strip().replace("\\n", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "|---" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        while parts and not parts[0]:
            parts.pop(0)
        while parts and not parts[-1]:
            parts.pop()
        if len(parts) < 3:
            continue

        # Try to match by name column (parts[1] or anywhere after identifier)
        row_name = parts[1] if len(parts) > 1 else ""
        row_name_lower = row_name.lower().replace(" ", "_").replace("-", "_").replace("/", "_")
        if base_name.lower() not in row_name_lower and row_name_lower not in base_name.lower():
            # Also try matching by parsable function name from the row
            safe = row_name_lower.replace("/", "_").replace(".", "_")
            if base_name.lower() not in safe and safe not in base_name.lower():
                continue

        # Found the row — scan for a range-like column
        for pi in range(2, len(parts)):
            constraint = parse_func(parts[pi])
            if constraint is not None:
                return constraint

    return None


def _lookup_parameter_constraint(
    function_name: str,
    parameter_name: str,
    build_blueprint: BuildBlueprint,
    safety_schema_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    # Primary: constraints merged into BuildBlueprint.action_methods by BlueprintAssembler.
    action = build_blueprint.driver_blueprint.action_methods.get(function_name)
    if action is not None:
        bp_constraints = action.parameter_constraints
        if bp_constraints:
            if parameter_name in bp_constraints:
                return bp_constraints[parameter_name]
            # Fuzzy match: if the function has a single parameter and a single
            # constraint entry, the names just don't match (e.g. "temperature_c"
            # vs "temperature").  Use the only constraint available.
            if len(bp_constraints) == 1:
                only = next(iter(bp_constraints.values()))
                if isinstance(only, dict):
                    return only

    # Secondary fallback: SafetySchema (may use different function/parameter names).
    safety_rule = _lookup_safety_rule(function_name, safety_schema_payload)
    if safety_rule and safety_rule.parameter_constraints:
        sc = safety_rule.parameter_constraints
        if parameter_name in sc:
            pconst = sc[parameter_name]
            return pconst.model_dump() if hasattr(pconst, "model_dump") else pconst
        if len(sc) == 1:
            only = next(iter(sc.values()))
            return only.model_dump() if hasattr(only, "model_dump") else only

    return None


def build_test_arguments(
    *,
    function_name: str,
    signature: inspect.Signature,
    build_blueprint: BuildBlueprint,
    safety_schema_payload: dict[str, Any] | None,
    fallback_factory,
    flat_cmd_table_raw: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    arguments: dict[str, Any] = {}
    argument_sources: dict[str, str] = {}
    transport_kwargs = build_blueprint.driver_blueprint.transport_config.optional_constructor_args

    for name, parameter in signature.parameters.items():
        if name == "self":
            continue

        source = "fallback"
        value: Any
        constraint = _lookup_parameter_constraint(
            function_name, name, build_blueprint, safety_schema_payload,
        )

        if function_name == "__init__" and name in transport_kwargs:
            value = _coerce_to_parameter(parameter, transport_kwargs[name])
            source = "build_blueprint.transport_config"
        elif constraint and constraint.get("allowed_values"):
            value = _coerce_to_parameter(parameter, constraint["allowed_values"][0])
            source = "blueprint.allowed_values"
        elif constraint and constraint.get("default_value") is not None:
            value = _coerce_to_parameter(parameter, constraint["default_value"])
            source = "blueprint.default_value"
        elif constraint and (constraint.get("min_value") is not None or constraint.get("max_value") is not None):
            value = _safe_numeric_value_plain(parameter=parameter, constraint=constraint)
            if value is not None:
                source = "blueprint.range"
            else:
                value = fallback_factory(parameter)
        elif parameter.default is not inspect._empty:
            value = parameter.default
            source = "signature.default"
        elif function_name == "__init__" and name in {"baudrate", "timeout", "timeout_s"} and name in transport_kwargs:
            value = _coerce_to_parameter(parameter, transport_kwargs[name])
            source = "build_blueprint.transport_config"
        elif flat_cmd_table_raw and function_name != "__init__":
            # Final fallback: parse the raw command table for constraints
            from app.agents.build.code_writer_agent import _parse_range_to_constraint
            constraint = _lookup_raw_table_constraint(
                function_name=function_name,
                parameter_name=name,
                raw_table=flat_cmd_table_raw,
                parse_func=_parse_range_to_constraint,
            )
            if constraint and constraint.get("allowed_values"):
                value = _coerce_to_parameter(parameter, constraint["allowed_values"][0])
                source = "raw_table.allowed_values"
            elif constraint and (constraint.get("min_value") is not None or constraint.get("max_value") is not None):
                value = _safe_numeric_value_plain(parameter=parameter, constraint=constraint)
                if value is not None:
                    source = "raw_table.range"
                else:
                    value = fallback_factory(parameter)
            else:
                value = fallback_factory(parameter)
        else:
            value = fallback_factory(parameter)

        arguments[name] = value
        argument_sources[name] = source

    return arguments, argument_sources


def build_minimum_restore_arguments(
    *,
    function_name: str,
    signature: inspect.Signature,
    build_blueprint: BuildBlueprint,
    safety_schema_payload: dict[str, Any] | None,
    baseline_arguments: dict[str, Any],
    fallback_factory,
) -> tuple[dict[str, Any], dict[str, str], bool]:

    arguments: dict[str, Any] = {}
    argument_sources: dict[str, str] = {}
    restore_required = False

    for name, parameter in signature.parameters.items():
        if name == "self":
            continue

        constraint = _lookup_parameter_constraint(
            function_name, name, build_blueprint, safety_schema_payload,
        )
        source = "baseline_argument"
        value = baseline_arguments.get(name)

        if constraint is not None:
            minimum_value = _minimum_constraint_value_plain(parameter, constraint)
            if minimum_value is not None:
                value = minimum_value
                allowed = constraint.get("allowed_values", []) if isinstance(constraint, dict) else (getattr(constraint, "allowed_values", None) or [])
                min_v = constraint.get("min_value") if isinstance(constraint, dict) else getattr(constraint, "min_value", None)
                if allowed:
                    source = "blueprint.minimum_allowed_value"
                elif min_v is not None:
                    source = "blueprint.min_value"
                else:
                    source = "blueprint.default_value"
        elif parameter.default is not inspect._empty:
            value = parameter.default
            source = "signature.default"
        elif name not in baseline_arguments:
            value = fallback_factory(parameter)
            source = "fallback"

        if baseline_arguments.get(name) != value and source != "baseline_argument":
            restore_required = True

        arguments[name] = value
        argument_sources[name] = source

    return arguments, argument_sources, restore_required
