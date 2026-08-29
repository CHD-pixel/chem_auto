---
name: binary-frame
description: Binary framed serial protocol for instruments with custom packet formats. Covers header+length+command+data+checksum patterns common in lab instruments.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`binary`

## library
Use `pyserial` for raw byte I/O. Do NOT use `minimalmodbus`, `pymodbus`, or `pyvisa`.

## protocol overview

Binary instruments communicate via structured packets:

```
[Header] [Length] [Command] [Data...] [Checksum]
```

**CRITICAL: ALL protocol parameters (header bytes, length field size, checksum algorithm, baudrate, timeout) MUST be read from the manual's protocol format section. Do NOT use any hardcoded values.**

- **Header**: Read the header bytes from the manual's frame format description.
- **Length**: Read the length field size (1 or 2 bytes) from the manual. This varies by instrument.
- **Length semantics**: Read from `flat_framing.length_semantics`. The length field value may count:
  (a) payload only (cmd+data), (b) payload+checksum, or (c) everything from Length to Checksum
  including Length itself. Check the manual's example packets to determine which convention.
- **Byte order**: Read from `flat_framing.byte_order`. The length field may use big-endian (most
  common) or little-endian. Use `struct.pack(">H", ...)` for big-endian or `struct.pack("<H", ...)`
  for little-endian.
- **Command**: Operation ID — read from the command table in the manual.
- **Data**: Payload bytes — format defined per command.
- **Checksum**: Read the checksum algorithm and parameters from the manual. Common types:
  - additive (direct sum of bytes)
  - crc16 (various polynomials)
  - xor
  - none

**CRITICAL: What bytes are included in the checksum?**
The manual specifies which bytes are checksummed. Common conventions:
- **"Length to Data"** (most common): checksum = sum(length_bytes + cmd + data) & 0xFF
- **"Cmd to Data"**: checksum = sum(cmd + data) & 0xFF
- **"Header to Data"**: checksum = sum(header + length + cmd + data) & 0xFF

ALWAYS check the manual's example packets to determine which convention is used.
The template assumes "Length to Data" — adjust `_compute_checksum` input if your manual uses a different convention.

## transport_config
- Read baudrate, timeout, parity, stopbits from the manual's serial interface parameters section.
- framing: binary, no line terminators.

## core_methods

| method | purpose |
|--------|---------|
| `connect()` | Open serial port |
| `disconnect()` | Close serial port |
| `_build_packet(cmd, data)` | Construct full packet: header+length+cmd+data+checksum |
| `_parse_response(raw)` | Validate header/checksum, extract command+data |
| `_send_recv(cmd, data=None)` | build → write → read header → read length → read payload+checksum → parse |
| `_compute_checksum(data)` | Compute checksum — use the exact algorithm from the manual |

The template provides a COMPLETE working implementation of `_build_packet`, `_parse_response`, and `_send_recv`. You MUST:
1. Replace `self._HEADER`, `self._LENGTH_FIELD_SIZE`, `self._CHECKSUM_SIZE` with actual values from the manual
2. Replace `_compute_checksum` with the algorithm from the manual
3. Do NOT change the parsing logic structure — it correctly reads header → length → payload → checksum
4. Do NOT hardcode fixed read sizes — `_send_recv` reads the length field dynamically

## protocol_action_binding format

```
"command_id:<hex>;response_len:<bytes>;desc:<description>"
```

| field | purpose |
|-------|---------|
| `command_id` | Command byte (hex) — read from the command table |
| `response_len` | Expected response data length in bytes — read from the command table |
| `data_type` | Response data type: uint16, int16, float32, string, bytes |
| `desc` | Command description — from the manual |

## implementation_strategy
- Read commands → `_send_recv(cmd_id)` → parse response data
- Write commands → `_send_recv(cmd_id, packed_data)` → check acknowledgment
- Async commands → send without waiting for response

## CRITICAL RULES
1. **Read ALL protocol parameters from the manual** — header, field sizes, checksum algorithm, baudrate, timeouts
2. **Do NOT use any values from this SKILL document** — this document only describes the code structure
3. **Verify checksum implementation** against the manual's example packets if available
