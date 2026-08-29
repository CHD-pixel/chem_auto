---
name: lxi-scpi
description: LXI instruments — SCPI over TCP/IP via PyVISA-py. Supports raw socket (5025), VXI-11, and HiSLIP transports. Oscilloscopes, DMMs, power supplies, signal generators with Ethernet.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`scpi` — same as scpi-generic. SCPI command set is transport-agnostic.

## library
`pyvisa-py` (pure-Python VISA backend). The previously common `python-vxi11` is abandoned since 2017; do NOT use it.

## transport_config
- transport_type: `tcp`
- protocol: `raw` (port 5025, standard), `vxi11` (legacy RPC portmapper), or `hislip` (modern LXI, faster)
- default_port: 5025
- default_timeout: 10.0
- termination: `\n`
- discovery: pyvisa-py + zeroconf + psutil enables automatic instrument discovery via VXI-11 portmap and mDNS

## core_methods
| method | SCPI / IEEE 488.2 | purpose |
|--------|-------------------|---------|
| `connect(host, port, protocol="raw") -> None` | — | open TCP socket or VXI-11/HiSLIP session |
| `close() -> None` | — | close connection |
| `write(command) -> None` | — | send command + termination |
| `query(command) -> str` | — | write + recv until termination |
| `_check_error() -> None` | `:SYSTem:ERRor?` | loop until 0, raise on errors |
| `get_identification() -> str` | `*IDN?` | manufacturer, model, serial, firmware |
| `reset() -> None` | `*RST` | factory reset |
| `clear_status() -> None` | `*CLS` | clear status + error queue |
| `self_test() -> int` | `*TST?` | returns 0=pass |
| `wait_for_opc() -> None` | `*OPC?` | block until operation complete |
| `read_stb() -> int` | `*STB?` | read Status Byte (efficient error pre-check) |
| `read_esr() -> int` | `*ESR?` | read & clear Event Status Register |
| `query_float(command) -> float` | — | convenience: query + float() |

## protocol_action_binding format
Identical to scpi-generic — raw SCPI strings with `{param}` format-spec placeholders:
```
":MEASure:VOLTage:DC?"
":SOURce:VOLTage {value:.4f}"
```

## transport variants
| protocol | port | Python API | notes |
|----------|------|-----------|-------|
| `raw` | 5025 | `socket.sendall()` / `socket.recv()` | zero dependencies; modern standard |
| `vxi11` | portmap:111 | `pyvisa-py TCPIP::host::INSTR` | legacy; slower due to RPC overhead |
| `hislip` | 4880 | `pyvisa-py TCPIP::host::hislip0::INSTR` | LXI 1.4+; overlap mode for throughput |

All three share identical `write()`/`query()` interface — only connection setup differs.

## instrument discovery
- VXI-11: UDP broadcast to port 111 (requires `psutil` for multi-interface)
- mDNS: `_lxi._tcp` and `_scpi-raw._tcp` service advertisements (requires `zeroconf`)
- Fallback: manual IP:port configuration

## implementation_strategy
- read/status → `query_command`
- write/config → `direct_command`
- composite → `sync_sequence`
