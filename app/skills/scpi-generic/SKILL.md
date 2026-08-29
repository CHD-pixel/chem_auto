---
name: scpi-generic
description: SCPI IEEE 488.2 over serial/USB via PyVISA. Power supplies, DMMs, oscilloscopes, signal generators.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`scpi`

## library — CRITICAL: use PyVISA, do NOT send raw bytes

`pyvisa-py` provides a complete SCPI transport layer. **You MUST use `pyvisa.ResourceManager` + `open_resource()`.** PyVISA handles termination characters, binary block reads, and error queue access automatically.

**STRICTLY FORBIDDEN:**
- Do NOT use raw `serial.Serial` + manual `write(b"CMD\n")` — use PyVISA
- Do NOT manually handle `\n` termination — PyVISA does it
- Do NOT implement your own `read_until()` — use `resource.read_raw()` or `resource.read()`

**Correct pattern — wrap PyVISA resource:**
```python
import pyvisa

rm = pyvisa.ResourceManager('@py')
resource = rm.open_resource('ASRL3::INSTR')  # or TCPIP, USB, GPIB
resource.write('*IDN?')
response = resource.read()
```

## transport_config
- transport_type: `serial`
- default_baudrate: 9600
- default_timeout: 5.0
- termination: `\n`

## core_methods
| method | SCPI / IEEE 488.2 | purpose |
|--------|-------------------|---------|
| `write(command) -> None` | — | send command + termination |
| `query(command) -> str` | — | write + read until termination |
| `_check_error() -> None` | `:SYSTem:ERRor?` | loop until "0,No error"; raise SCPIError with all errors |
| `get_identification() -> str` | `*IDN?` | manufacturer, model, serial, firmware |
| `reset() -> None` | `*RST` | factory reset (does NOT clear error queue!) |
| `clear_status() -> None` | `*CLS` | clear status registers + error queue |
| `wait_for_opc() -> None` | `*OPC?` | block until all pending operations complete |
| `read_stb() -> int` | `*STB?` | read Status Byte register |
| `read_esr() -> int` | `*ESR?` | read & clear Event Status Register |
| `wait() -> None` | `*WAI` | wait for previous commands to finish (non-blocking variant of *OPC?) |
| `query_float(command) -> float` | — | shorthand: query + float() |
| `query_int(command) -> int` | — | shorthand: query + int() |
| `query_bool(command) -> bool` | — | shorthand: query + normalize ON/OFF/0/1 |

## protocol_action_binding format
Use raw SCPI command strings (no library prefixes). `{param}` placeholders use Python format-spec syntax:
```
":MEASure:VOLTage:DC?"              # query (read)
":SOURce:VOLTage {value:.4f}"       # write with float format
":SENSe:FUNCtion {mode}"            # write with enum (mode="VOLT"|"CURR"|"RES")
":INPut {state:ON|OFF}"             # write with boolean
```

## IEEE 488.2 error queue
Must loop until `0,"No error"`. A single `:SYSTem:ERRor?` call only pops one entry. Implementation:
```python
errors = []
while True:
    resp = self.query(":SYSTem:ERRor?")
    if resp.startswith("0,") or "No error" in resp:
        break
    errors.append(resp)
if errors:
    raise SCPIError(errors)
```

## compound commands
Multiple commands separated by `;`. Root reset with `;:`:
```
":SENSe:VOLTage:DC:RANGe 10;:MEASure:VOLTage:DC?"
```

## implementation_strategy
- read/status → `query_command`
- write/config → `direct_command`
- composite → `sync_sequence`
