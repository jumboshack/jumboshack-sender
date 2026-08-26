"""Read-only Modbus RTU client for the WaterFurnace Aurora ABC.

Serial parameters confirmed from reference/modbus/ (a working modpoll setup):
RTU, 19200 baud, 8 data bits, EVEN parity, 1 stop bit, slave address 1,
/dev/ttyUSB0, FC3 (read holding registers), zero-based (PDU) addressing — so
WaterFurnace register N is read at Modbus address N (matches `modpoll -0`).

READ-ONLY BY DESIGN. There are intentionally NO write functions in this module.
Jumboshack is a monitoring product; we never command the equipment.
"""

import minimalmodbus
import serial


def make_instrument(port: str = "/dev/ttyUSB0", address: int = 1,
                    baud: int = 19200, timeout: float = 1.0):
    inst = minimalmodbus.Instrument(port, address, mode=minimalmodbus.MODE_RTU)
    inst.serial.baudrate = baud
    inst.serial.bytesize = 8
    inst.serial.parity = serial.PARITY_EVEN
    inst.serial.stopbits = 1
    inst.serial.timeout = timeout
    inst.clear_buffers_before_each_transaction = True
    return inst


def read_one(inst, reg: int, signed: bool = True):
    """Read a single holding register (FC3). Returns the 16-bit int, or None if the
    register can't be read (firmware gap / unsupported) — tolerant like aurora_fetch
    --ignore-missing-registers."""
    try:
        return inst.read_register(reg, number_of_decimals=0, functioncode=3, signed=signed)
    except Exception:
        return None


def read_many(inst, regs, signed: bool = True) -> dict:
    """{reg: value|None} reading each register individually (per-register error
    isolation, so one bad register never aborts the batch)."""
    return {r: read_one(inst, r, signed=signed) for r in regs}


def read_block(inst, start: int, count: int):
    """Contiguous block read (FC3) → list of ints, or None on error. For efficient
    bulk reads once the transport is validated."""
    try:
        return inst.read_registers(start, count, functioncode=3)
    except Exception:
        return None
