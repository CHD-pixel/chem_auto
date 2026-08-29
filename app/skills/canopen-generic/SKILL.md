---
name: canopen-generic
description: CANopen CiA 301/401/402 over CAN bus. Motor controllers, I/O modules, sensors. Uses canopen-python library.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`canopen`

## library
`canopen` (canopen-python) on top of `python-can` hardware abstraction.

## transport_config
- transport_type: `can`
- default_bitrate: 500000
- bustype: `pcan` (configurable: socketcan, pcan, usb2can, etc.)

## core_methods
| method | purpose |
|--------|---------|
| `connect(channel, bustype, bitrate) -> None` | open CAN interface, create Network |
| `close() -> None` | NMT stop + disconnect |
| `_sdo_read(index, subindex=0) -> Any` | read Object Dictionary via SDO upload |
| `_sdo_write(index, subindex, value) -> None` | write OD via SDO download |
| `_nmt_command(state) -> None` | NMT: START, STOP, PRE-OPERATIONAL, RESET, RESET_COMM |
| `_pdo_read(pdo_number) -> dict` | read received TPDO (real-time cyclic data) |
| `_pdo_write(pdo_number, data) -> None` | write RPDO (real-time output) |
| `_emcy_subscribe(callback) -> None` | register EMCY emergency message handler |
| `startup() -> None` | RESET → wait bootup → PRE-OP → SDO config → OPERATIONAL |
| `shutdown() -> None` | STOP → disconnect |
| `store_configuration() -> None` | save to non-volatile memory (OD 0x1010 sub1 = "save") |
| `restore_defaults() -> None` | factory reset (OD 0x1011 sub1 = "load") |

## protocol_action_binding format
```
"sdo_read:0x6041;subindex:0;dtype:uint16"              # read statusword
"sdo_write:0x6040;subindex:0;dtype:uint16"             # write controlword
"sdo_read:0x606C;subindex:0;dtype:int32;scale:0.1"    # read velocity (read-only OD)
"sdo_write:0x60FF;subindex:0;dtype:int32"              # write target velocity
"pdo_rx:1;variable:0x60FF;dtype:int32"                 # receive via RPDO1
```

| field | purpose |
|-------|---------|
| `sdo_read` / `sdo_write` / `pdo_rx` / `pdo_tx` | access method |
| OD index + subindex | Object Dictionary location |
| `dtype` | uint8, uint16, uint32, int8, int16, int32, float32 |
| `scale` | physical value multiplier (optional) |

**Important**: many OD entries are read-only (e.g. 0x6041 statusword, 0x606C velocity). Use `sdo_read` only for these. Write bindings must target writable entries (0x6040 controlword, 0x60FF target velocity).

## Object Dictionary ranges
**CiA 301 (all devices):** 0x1000-0x1FFF (device type, identity, heartbeat)
**CiA 401 (I/O):** 0x6000 (dig_in), 0x6200 (dig_out), 0x6401 (analog_in), 0x6411 (analog_out)
**CiA 402 (drives):** 0x6040 (controlword), 0x6041 (statusword), 0x6060 (op mode), 0x606C (velocity), 0x607A (target position), 0x6083 (acceleration)

## implementation_strategy
- read via SDO → `query_command`
- read via PDO (cyclic) → `query_command` with PDO cache
- write config → `direct_command` (SDO download)
- write real-time → PDO (when mapped)
