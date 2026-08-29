# ActionMethods Agent — Extract function definitions

## Task

Read the full OCR manual context. Discover ALL instrument-controllable functions.
For each function, output its metadata as JSON.

## Input

- `raw_command_table` — command/register table text (pipe-delimited, markdown, prose, or empty)
- `core_blueprint` — CoreBlueprint JSON (transport_config)
- `flat_device` — device identity (protocol_family)

## Output

```json
{
  "action_methods": {
    "function_name": {
      "function_name": "function_name",
      "signature": "def function_name(self) -> ReturnType",
      "purpose": "Brief description of what this function does",
      "function_category": "read | control | setup | status | cleanup | safety | unknown",
      "side_effect_level": "none | low | medium | high",
      "implementation_strategy": "direct_command | query_command | async_sequence | sync_sequence | software_postprocess | composite_workflow | stub",
      "depends_on": [],
      "parameter_constraints": {},
      "parameter_guard_required": false,
      "call_sequence_guard_required": false,
      "restore_strategy": "none | snapshot_and_restore | readback_verify | manual_review",
      "user_confirmation_required_after_call": false,
      "protocol_action_binding": ["<command_id from raw_command_table>"]
    }
  }
}
```

## Rules

1. **ALL identifiers in English.** Translate non-English names from the manual.
2. **One discoverable function = one action_method.** Do NOT fabricate. Do NOT skip.
3. **Naming**: `get_xxx`/`read_xxx` for reads, `set_xxx`/`write_xxx` for writes, `start_xxx`/`stop_xxx` for control.
4. **implementation_strategy**: read-only → `query_command`, write/execute → `direct_command`, multi-step → `sync_sequence`, pure computation → `software_postprocess`, insufficient evidence → `stub`.
5. **parameter_constraints**: If the manual specifies allowed values or ranges for a parameter, include it:
   - `{"param_name": {"allowed_values": [1,2,3]}}` or `{"param_name": {"min_value": 0, "max_value": 100}}`
   - Leave empty `{}` if no constraints found.
6. **parameter_guard_required**: Only true if the function has constrained parameters.
7. **call_sequence_guard_required**: Only true if `depends_on` is non-empty.
8. **user_confirmation_required_after_call**: Only true for control functions with `side_effect_level` medium/high. This is metadata for the test framework — do NOT add a `confirm` parameter to the function signature.
9. **protocol_action_binding**: The command/register ID(s) from `raw_command_table` that this function uses. This is the authoritative source for the code writer to construct protocol frames.
   - Binary protocols: hex command byte, e.g. `["0x02"]`, `["0x12"]`
   - Modbus: register address, e.g. `["1000"]`, `["1002"]`
   - SCPI/ASCII: raw command string, e.g. `["IN_NAME"]`, `["OUT_SP_1"]`
   - If a function uses multiple commands, list all: `["0x03", "0x04"]`
   - If the command is unclear, leave empty `[]` — the system has a fallback parser.

## Do NOT

- Fabricate functions that are not in the `raw_command_table`.
- Skip functions that ARE in the `raw_command_table`.
- Use non-English identifiers. Translate all names from the manual.
- Add `confirm` parameters to function signatures. Confirmation is metadata for the test framework, not a code parameter.
- Leave `protocol_action_binding` empty if the `raw_command_table` has the command ID — always bind the authoritative command.
- Output anything other than JSON. No markdown, no explanations.

## Self-Verification

1. Does every function have a meaningful purpose?
2. Are read/write classifications correct?
3. Did I miss any functions from the raw_command_table?
4. Do parameter_constraints match the range column in the raw_command_table?
5. Does every function have a `protocol_action_binding` with the correct command/register ID from the raw_command_table?

Output JSON only. No markdown, no explanations.
