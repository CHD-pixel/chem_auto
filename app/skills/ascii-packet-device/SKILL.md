---
name: ascii-packet-device
description: Serial text and binary-framed protocols for lab instruments. Covers ASCII line (text commands with termination) and binary packet (framed with header/checksum) over RS-232/RS-485.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`ascii` — two variants:

**variant A: `ascii_line`** (most lab instruments — text commands with `\r`/`\n` termination)
- Commands: ASCII string + `\r` or `\n` termination
- Response: line-based, may include error codes (e.g. `-08` = invalid)
- No checksum, no framing headers

**variant B: `binary_frame`** (Huber LAI, custom binary protocols)
- Frame: header + length + command + data + checksum
- Byte stuffing may be needed if data bytes can match header/footer

## transport_config
- transport_type: `serial`
- default_baudrate: 9600
- default_timeout: 2.0
- termination: `\r` (most common) or `\r\n` or none (binary)
- inter_command_gap_ms: 50 (some instruments need 250ms between commands)

## core_methods — ascii_line
| method | purpose |
|--------|---------|
| `_send_command(command) -> str` | send `"CMD param\r"`, read until terminator, return response line |
| `_parse_response(response) -> dict` | extract status/values from response; handle error codes |
| `_read_response() -> str` | read until terminator with timeout (separate from send for retry) |

## core_methods — binary_frame
| method | purpose |
|--------|---------|
| `_build_packet(command_id, payload) -> bytes` | header + length + cmd + payload + checksum |
| `_parse_response(raw) -> dict` | validate header/checksum, extract status + data bytes |
| `_send_packet(command_id, payload) -> dict` | build → write → read → validate → parse |
| `_compute_checksum(data) -> int` | protocol-specific checksum |
| `_read_frame() -> bytes` | accumulate bytes until complete frame received (handle partial reads) |

## checksum priority (binary_frame)
**Most common in lab instruments: additive > CRC16-Modbus > XOR**

| algorithm | used by | implementation |
|-----------|---------|---------------|
| additive (8-bit) | Huber LAI, Zaber ASCII | `(-sum(data)) & 0xFF` (twos complement of sum) |
| CRC16-Modbus | Modbus RTU, industrial devices | poly 0x8005, init 0xFFFF, reflected |
| CRC16-CCITT | telecom-background instruments | poly 0x1021, init 0xFFFF |
| XOR | rare in practice | `functools.reduce(operator.xor, data)` |

## CRITICAL: command strings must come verbatim from the extraction
The `cmd` / `command_id` field MUST be copied exactly from the ProtocolSpec or
FunctionCatalog `protocol_actions` field. Do NOT modify, renumber, add @suffixes,
or guess command names. If the extraction says `CMD_READ_TEMP`, use `CMD_READ_TEMP` — not
`CMD_READ_SPEED` or `SET_TEMP_MODE`. Cross-reference the function purpose with the command
table from the manual context to ensure correct mapping.

## protocol_action_binding format
**ascii_line variant:**
```
"cmd:CMD_READ_TEMP"                    # query parameter
"cmd:CMD_WRITE_TEMP {value}"           # write with value substitution
"cmd:CMD_START;response:ok"            # command with expected response
```

**binary_frame variant:**
```
"command_id:0x10"                      # read command
"command_id:0x20;payload:>H"           # write with struct format for payload
```

| field | purpose |
|-------|---------|
| `cmd` / `command_id` | protocol command string or byte |
| `payload` | struct format string for data encoding (binary only) |
| `response` | expected response pattern (optional) |

## error handling
- Parse error codes from response (negative numbers like `-83`, `-08` are common)
- Timeout with inter-byte gap detection for variable-length responses
- Buffer flush before retry on checksum/frame errors

## implementation_strategy
- read/status → `direct_command`
- write/control → `direct_command`
- composite → `sync_sequence`
- software-only → `software_postprocess`
