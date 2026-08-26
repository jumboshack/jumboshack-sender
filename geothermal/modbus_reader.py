"""Read the full WaterFurnace register set over Modbus → structured JSON.

Reads the registers, assembles a parser.parse_log()-shaped dict, and calls
converter.build_record() — producing the same JSON shape as the reference
aurora_fetch tool (validated field-for-field against it).

The bitfield/config decoders below (system outputs, AXB outputs, DIP switch, IZ2
zone config, numeric scaling) are faithful ports of the `waterfurnace_aurora` gem
(v1.4.11), MIT-licensed by Cody Cutrer — see THIRD-PARTY-NOTICES.md.

READ-ONLY (uses modbus_client, which has no write functions).
"""

from datetime import datetime

from . import converter
from .modbus_client import make_instrument, read_block, read_one

# Register sets — mirror pi/geothermal_unit*.sh (170-253 deliberately skipped: crashes the ABC).
LABELED = [745, 746, 747, 19, 30, 33, 54, 400, 401, 740, 741, 900, 903, 1104, 1109,
           1110, 1111, 1113, 1114, 1115, 1116, 1117, 1124, 1134, 1135, 1136,
           31007, 31008, 31009, 31010, 31011, 31012]
_RAW_SPEC = "1146-1147,1148-1149,1150-1151,1152-1153,1154-1155,1156-1157,1164-1165,31109"
_NEW_SPEC = ("2-36,50-52,88-126,320-326,340-348,400-419,460-462,483,501-508,564-567,"
             "600-699,800-829,901,908-909,1103-1126,3000-3002,3027,3200-3253,3322-3332,"
             "3400-3431,3500-3524,3800-3809,3818-3834,3900-3914,12005-12006,12309-12310,"
             "31003,31005,31013-31024,31200-31215")

# ── decoders ported from waterfurnace_aurora 1.4.11 (registers.rb) ──
SYSTEM_OUTPUTS = [(0x01, "cc"), (0x02, "cc2"), (0x04, "rv"), (0x08, "blower"),
                  (0x10, "eh1"), (0x20, "eh2"), (0x200, "accessory"),
                  (0x400, "lockout"), (0x800, "alarm")]
AXB_OUTPUTS = [(0x01, "dhw"), (0x02, "loop_pump"), (0x04, "diverting_valve"),
               (0x08, "dehumidifer_reheat"), (0x10, "accessory2")]
_ACC_RELAY = {0: "compressor", 1: "slow_opening_water_valve", 2: "humidifier", 3: "blower"}
_HEATING_MODE = {0: "off", 1: "auto", 2: "cool", 3: "heat", 4: "eheat"}
_CALLS = {0: "standby", 1: "unknown1", 2: "h1", 3: "h2", 4: "h3", 5: "c1", 6: "c2", 7: "unknown7"}

SIGNED_TENTHS = {19, 740, 747, 900, 903, 1109, 1110, 1111, 1113, 1114, 1124, 1134,
                 1135, 1136, 31007, 31010}
TENTHS = {401, 745, 746, 1115, 1116, 1117}


def _expand(spec):
    out = []
    for p in spec.split(","):
        p = p.strip()
        if "-" in p:
            a, b = p.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif p:
            out.append(int(p))
    return out


def _s16(v):
    v &= 0xffff
    return v - 0x10000 if v >= 0x8000 else v


def _from_bitmask(value, flags):
    res = []
    for bit, name in flags:
        if value & bit == bit:
            res.append(name)
        value &= ~bit
    value &= 0xffff
    if value:
        res.append("0x%04x" % value)
    return res


def _ruby(d):
    return "{" + ", ".join(f":{k}=>:{v}" if isinstance(v, str) else f":{k}=>{v}"
                           for k, v in d.items()) + "}"


def _dip(v):
    if v == 0x7fff:
        return "manual"
    return _ruby({
        "fp1": 30 if v & 0x01 else 15,
        "fp2": 30 if v & 0x02 else "off",
        "reversing_valve": "o" if v & 0x04 else "b",
        "accessory_relay": _ACC_RELAY[(v >> 3) & 0x3],
        "compressor": 1 if v & 0x20 else 2,
        "lockout": "continuous" if v & 0x40 else "pulse",
        "dehumidifier_reheat": "dehumidifier" if v & 0x80 else "reheat",
    })


def _zone1(v):
    fan = "continuous" if v & 0x80 else ("intermittent" if v & 0x100 else "auto")
    d = {"fan": fan, "on_time": ((v >> 9) & 0x7) * 5, "off_time": (((v >> 12) & 0x7) + 1) * 5,
         "cooling_target_temperature": ((v & 0x7e) >> 1) + 36,
         "heating_target_temperature_carry": v & 0x01}
    lo = v & ~0x7fff & 0xffff
    if lo:
        d["unknown"] = "0x%04x" % lo
    return d


def _zone2(v, carry):
    d = {"call": _CALLS[(v >> 1) & 0x7], "mode": _HEATING_MODE[(v >> 8) & 0x03],
         "damper": "open" if v & 0x10 else "closed"}
    if carry is not None:
        d["heating_target_temperature"] = ((carry << 5) | ((v & 0xf800) >> 11)) + 36
    lo = v & ~0xfb1e & 0xffff
    if lo:
        d["unknown"] = "0x%04x" % lo
    return d


def _labeled_string(reg, raw, all_raw):
    if reg == 30:
        return ", ".join(_from_bitmask(raw, SYSTEM_OUTPUTS))
    if reg == 1104:
        return ", ".join(_from_bitmask(raw, AXB_OUTPUTS))
    if reg == 33:
        return _dip(raw)
    if reg in (31008, 31011):
        return _ruby(_zone1(raw))
    if reg in (31009, 31012):
        prior = _zone1(all_raw.get(reg - 1, 0))
        return _ruby(_zone2(raw, prior.get("heating_target_temperature_carry")))
    if reg == 400:
        return "true" if raw else "false"
    if reg in SIGNED_TENTHS:
        return "%.1f" % (_s16(raw) / 10.0)
    if reg in TENTHS:
        return "%.1f" % (raw / 10.0)
    return str(raw)


def _read_block_set(inst, regs, maxblk=60):
    """Read many registers via contiguous FC3 blocks (fast), falling back to
    per-register reads on a block error (tolerant, like --ignore-missing-registers)."""
    regs = sorted(set(regs))
    out = {}
    i = 0
    while i < len(regs):
        j = i
        while j + 1 < len(regs) and regs[j + 1] == regs[j] + 1 and (regs[j] - regs[i]) < maxblk - 1:
            j += 1
        start, count = regs[i], regs[j] - regs[i] + 1
        blk = read_block(inst, start, count)
        if blk is not None:
            for k, v in enumerate(blk):
                out[start + k] = v & 0xffff
        else:
            for r in regs[i:j + 1]:
                v = read_one(inst, r, signed=False)
                if v is not None:
                    out[r] = v & 0xffff
        i = j + 1
    return out


def read_record(unit: int, port: str = "/dev/ttyUSB0") -> dict:
    """Read the full register set over Modbus and return the same JSON record
    aurora_fetch → convert.py produces (via converter.build_record, unchanged)."""
    inst = make_instrument(port=port)

    labeled_raw = {}
    for r in LABELED:
        v = read_one(inst, r, signed=False)
        if v is not None:
            labeled_raw[r] = v & 0xffff
    readings = {str(r): {"name": str(r), "raw": _labeled_string(r, v, labeled_raw)}
                for r, v in labeled_raw.items()}

    raw_regs = {str(r): v for r, v in
                _read_block_set(inst, _expand(_RAW_SPEC) + _expand(_NEW_SPEC)).items()}

    now = datetime.now().strftime("%a %m-%d-%Y %H:%M:%S EDT")
    log_data = {"query_start": now, "query_end": now,
                "readings": readings, "raw_registers": raw_regs}
    return converter.build_record(log_data, unit=unit)
