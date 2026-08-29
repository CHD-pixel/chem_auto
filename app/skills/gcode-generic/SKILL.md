---
name: gcode-generic
description: G-code line-based text over serial. Syringe pumps, XYZ stages, liquid handlers, CNC. Supports GRBL, Marlin, Smoothieware dialects.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`gcode`

## transport_config
- transport_type: `serial`
- default_baudrate: 115200
- default_timeout: 5.0
- termination: `\n`

## dialect
G-code device firmware varies in response format. Select one:
- `grbl` — simple `ok`/`error:N` per line (most common in lab pumps/stages)
- `marlin` — `ok` means "queued" NOT "executed"; sends `echo:busy: processing` during blocking ops
- `smoothieware` — like GRBL but M3/M5 block until planner drains
- `tinyg` — JSON mode (`{"r":{...},"f":[...]}`) preferred; text mode secondary

## core_methods
| method | G-code | purpose |
|--------|--------|---------|
| `_send_gcode(command, wait_ok=True) -> dict` | — | send line + `\n`, wait for acknowledgment |
| `_send_gcode_query(command) -> str` | — | send, accumulate lines until `ok` |
| `_send_realtime(char) -> None` | `!` / `~` / `?` / `^X` | real-time character injection |
| `home() -> dict` | G28 | home all axes |
| `move_to(x, y, z, feedrate) -> dict` | G1 | linear move (absolute) |
| `move_rel(axis, distance, feedrate) -> dict` | G91 G0 | relative move |
| `dwell(ms) -> dict` | G4 P{ms} | pause (dispense time, reaction wait) |
| `set_absolute() -> None` | G90 | switch to absolute positioning |
| `set_relative() -> None` | G91 | switch to relative positioning |
| `set_position(**axes) -> None` | G92 | set current position (zeroing, calibration) |
| `get_position() -> dict` | M114 | parse X/Y/Z from response |
| `wait_for_moves() -> dict` | M400 | block until motion buffer empty |
| `enable_motors() -> None` | M17 | enable steppers |
| `disable_motors() -> None` | M18/M84 | free motors (safe idle) |
| `feed_hold() -> None` | `!` | pause motion |
| `cycle_start() -> None` | `~` | resume after feed hold |
| `emergency_stop() -> None` | M112 or `^X` | immediate stop |

## protocol_action_binding format
```
"G28"                                              # home
"G1;X{pos_x:.3f};Y{pos_y:.3f};F{feedrate:.0f}"    # linear move
"M114"                                             # position query
"G4;P{ms:.0f}"                                     # dwell
"M400"                                             # wait for moves
"M280;P{servo};S{position:.0f}"                    # servo/valve control
```

## acknowledgment model (per dialect)
- **GRBL**: `ok` means executed. `error:N` maps to specific error (error:1=bad command, error:5=soft limit, error:20=unsupported).
- **Marlin**: `ok N P B` means queued (N=line, P=planned, B=bytes). Use M400 to confirm execution complete. `echo:busy: processing` during blocking moves.
- **TinyG**: JSON footer with `"f":[1,0,0,4408]` status codes. 0=ok, non-zero=error.

## implementation_strategy
- status/position → `query_command`
- motion → `direct_command`
- setup → `direct_command`
- emergency → `sync_sequence`
