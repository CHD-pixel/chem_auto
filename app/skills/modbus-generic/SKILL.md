---
name: modbus-generic
description: Modbus RTU/TCP via pymodbus. Universal template for any Modbus device — temperature controllers, PLCs, sensors, actuators, VFDs.
compatibility: chem_auto >= 0.2.0
---

## base_protocol_style
`modbus`

## library — choose pymodbus or minimalmodbus based on instrument scale

**BOTH libraries are acceptable.** Do NOT reimplement MODBUS manually.

### minimalmodbus — for simple lab instruments (recommended default)

Use when:
- Serial RTU only (no TCP)
- Relatively few registers (< 30)
- Single instrument on the bus
- Data types include float32, int32, or strings
- Priority is simplicity and correctness

```python
import minimalmodbus

class YourDeviceDriver:
    def __init__(self, port, unit_id=1, ...):
        self._instr = minimalmodbus.Instrument(port, unit_id)
        self._instr.serial.baudrate = baudrate
        self._instr.serial.timeout = timeout

    # Built-in convenience methods — no BinaryPayloadDecoder needed:
    def _read_int16(self, addr):     return self._instr.read_register(addr, signed=True)
    def _read_uint16(self, addr):    return self._instr.read_register(addr, signed=False)
    def _read_float32(self, addr):   return self._instr.read_float(addr)
    def _read_string(self, addr, n): return self._instr.read_string(addr, n)

    def _write_int16(self, addr, val):   self._instr.write_register(addr, val)
    def _write_float32(self, addr, val): self._instr.write_float(addr, val)
    def _write_string(self, addr, val, n): self._instr.write_string(addr, val, n)

    # Multi-register reads use the PLURAL form:
    def _read_registers(self, addr, count): return self._instr.read_registers(addr, count)

    # CRITICAL: read_register() reads exactly ONE register.
    # It does NOT accept count, number_of_registers, or similar kwargs.
    # Use read_registers() (plural) for multi-register reads.
    # DO NOT call undefined helpers like _pack_float32 — use
    # write_float() from minimalmodbus for float32 writes.
```

### pymodbus — for industrial / complex setups

Use when:
- TCP/IP transport needed
- RS-485 bus with multiple slave devices
- Many registers (> 30) or complex register maps
- Async communication required
- Need server/simulator capabilities

```python
from pymodbus.client.sync import ModbusSerialClient

class YourDeviceDriver:
    def __init__(self, port, unit_id=1, ...):
        self._client = ModbusSerialClient(method='rtu', port=port, baudrate=baudrate, ...)
        self._unit = unit_id

    def _read_register(self, addr, count=1):
        result = self._client.read_holding_registers(addr, count, unit=self._unit)
        return result.registers
```

### STRICTLY FORBIDDEN for both:
- Do NOT write `_build_packet()` — library handles framing
- Do NOT write `_compute_crc()` or CRC lookup table — library handles CRC
- Do NOT manually construct MODBUS ADU frames with `struct.pack`
- Do NOT import `serial` directly — use the library's client class
- Do NOT mix pymodbus and minimalmodbus API patterns — pick ONE library and use
  its API consistently. minimalmodbus: `read_register(addr)` (single, no count param).
  pymodbus: `read_holding_registers(addr, count, unit=N)` (count IS valid here).

## transport_config
- transport_type: `serial` (RTU/ASCII) or `tcp`
- default_baudrate: 9600 (common: 4800, 9600, 19200, 38400, 115200)
- default_timeout: 2.0
- default_unit_id: 1 (range 1-247)
- serial: 8 data bits, parity varies (N/E/O), 1 stop bit typical
- tcp_port: 502

## core_methods — delegate to pymodbus client internally
| method | calls pymodbus | purpose |
|--------|---------------|---------|
| `connect() -> None` | `if not self._instr.serial.is_open: self._instr.serial.open()` | open serial (defensive) |
| `disconnect() -> None` | `if self._instr.serial.is_open: self._instr.serial.close()` | close connection (defensive) |
| `_read_register(address, count=1) -> list[int]` | `self._client.read_holding_registers(address, count, slave=self._unit)` | read holding (0x03) |
| `_read_input_register(address, count=1) -> list[int]` | `self._client.read_input_registers(address, count, slave=self._unit)` | read input (0x04) |
| `_write_register(address, value) -> None` | `self._client.write_register(address, value, slave=self._unit)` | write single (0x06) |
| `_write_registers(address, values) -> None` | `self._client.write_registers(address, values, slave=self._unit)` | write multiple (0x10) |
| `_read_int16(address, signed=True) -> int` | via _read_register | 2 bytes → int |
| `_read_uint16(address) -> int` | via _read_register | 2 bytes → unsigned |
| `_read_float32(address, byte_order="big", word_order="big") -> float` | via _read_register + BinaryPayloadDecoder | 4 bytes → float |

## register address conventions
Modbus register addresses in manuals may use different numbering schemes.
The wire protocol always uses 0-indexed addresses:

| Manual notation | Wire address | Function | Example |
|----------------|--------------|----------|---------|
| 4xxxx (holding) | xxxx - 1 or xxxx - 40001 | 0x03/0x06/0x10 | 40003 → wire 2 or 2 |
| 3xxxx (input) | xxxx - 1 or xxxx - 30001 | 0x04 | 30001 → wire 0 |
| 0xNNNN (hex) | 0xNNNN | depends on context | 0x1000 → wire 0x1000 |
| Raw decimal | same | depends on context | 2004 → wire 2004 |

When the manual is unclear about the address base, check if register numbers
start at 1 or 0 in the documentation.

## register table pattern recognition
When parsing the assembled OCR context, MODBUS registers typically appear as
tables with columns like:

```
地址 | 名称 | 数据类型 | 读写 | 说明 | 范围/单位
Address | Name | DataType | R/W | Description | Range/Unit
```

Common column headers in Chinese PDFs:
- 地址/寄存器地址/Register Address → register address
- 名称/参数名称/Name → function name hint
- 数据类型/Data Type → uint16, int16, float32, etc.
- 读写/R/W/属性 → RO=read-only(0x04), RW=read-write(0x03+0x06)
- 说明/备注/Description → function purpose
- 范围/单位/Range/Unit → parameter constraints + scale

## deriving function names from register descriptions
- Strip units, punctuation, special chars from the register description
- Convert to snake_case: "Temperature Setpoint" → `temperature_setpoint`
- Prefix: read-only registers → `read_xxx` or `get_xxx`; writable → `set_xxx` for write
- If a register is read-write, create BOTH a read and write function

## protocol_action_binding format
Each register binding specifies exactly what the CodeWriterAgent needs:
```
"register:2004;function:0x03;dtype:int16;signed:true;scale:0.1;unit:C;desc:温度设定值"
"register:2005;function:0x03;dtype:uint16"
"register:2006;function:0x06;dtype:uint16;desc:启动/停止控制"
```

| field | required | values |
|-------|----------|--------|
| `register` | **yes** | 0-indexed wire address (int automatically converted to str by schema) |
| `function` | **yes** | 0x03=read_holding, 0x04=read_input, 0x06=write_single, 0x10=write_multiple |
| `dtype` | **yes** | int16, uint16, int32, uint32, float32, string |
| `count` | no | register count (default 1; 2 for 32-bit types) |
| `signed` | no | true/false (default false) |
| `scale` | no | multiplier to convert raw register value to physical unit |
| `unit` | no | physical unit (C, rpm, %, mL/min, etc.) |
| `desc` | no | register description from manual (helps CodeWriterAgent name methods) |
| `byte_order` | no | big (default), little — byte order within a 16-bit register |
| `word_order` | no | big (default), little — word order across 32-bit register pairs |

## pymodbus BinaryPayloadDecoder pattern
For 32-bit and float types, CodeWriterAgent MUST use BinaryPayloadDecoder:
```python
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

result = client.read_holding_registers(address, 2, slave=unit)
decoder = BinaryPayloadDecoder.fromRegisters(
    result.registers,
    byteorder=Endian.BIG,
    wordorder=Endian.BIG,
)
value = decoder.decode_32bit_float()
```

## implementation_strategy
- read holding registers → `direct_command` (function 0x03)
- read input registers → `direct_command` (function 0x04)
- write single register → `direct_command` (function 0x06)
- write multiple registers → `direct_command` (function 0x10)
