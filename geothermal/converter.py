"""Build a structured JSON record from decoded register data."""
import os
from typing import Optional

from .parser import parse_ruby_hash, parse_output_list, extract_float, extract_int
from .extra_registers import build_extra
from .version import __version__


def _agent_version() -> str:
    """Pipeline version stamped into each reading (env override wins, e.g. a git sha)."""
    return os.environ.get("GEO_AGENT_VERSION") or __version__


def _unit_location(unit: int) -> str:
    """Human-readable label for a unit, stamped into each reading. Override per
    device with the JUMBOSHACK_UNIT_LOCATION env var (e.g. 'Basement heat pump')."""
    return os.environ.get("JUMBOSHACK_UNIT_LOCATION") or f"Unit {unit}"

# Semantic names for the raw register IDs fetched in the second aurora_fetch call.
# Registers 1154/1156 are the high words of 32-bit pairs; 1155/1157 are the
# meaningful values aurora_fetch exposes for heat of extraction/rejection.
_REGISTER_NAMES = {
    "1146": "unknown_1146",
    "1147": "compressor_watts",
    "1148": "unknown_1148",
    "1149": "blower_watts",
    "1150": "unknown_1150",
    "1151": "unknown_1151",
    "1152": "unknown_1152",
    "1153": "total_watts",
    "1154": "heat_extraction_high_word",
    "1155": "heat_extraction_btuh",
    "1156": "heat_rejection_high_word",
    "1157": "heat_rejection_btuh",
    "1164": "unknown_1164",
    "1165": "loop_pump_watts",
    "31109": "dehumidifier_mode_raw",
}

# Isentropic exponent for R-410A: (k-1)/(k*η) where k=1.176, η=0.8 (isentropic efficiency)
_ISENTROPIC_EXPONENT = (1.176 - 1.0) / (1.176 * 0.8)


def calc_discharge_temp(
    suction_temp_f: Optional[float],
    suction_psi: Optional[float],
    discharge_psi: Optional[float],
) -> Optional[float]:
    """Estimate compressor discharge temperature using isentropic compression.

    Replicates the formula from the legacy Groovy convert.groovy script.
    Returns None if any input is None (e.g. compressor is off and registers are absent).
    """
    if suction_temp_f is None or suction_psi is None or discharge_psi is None:
        return None
    try:
        rankine = suction_temp_f + 459.67
        p_suction = suction_psi + 14.7      # gauge → absolute
        p_discharge = discharge_psi + 14.7
        result = rankine * (p_discharge / p_suction) ** _ISENTROPIC_EXPONENT - 459.67
        return round(result, 1)
    except (ZeroDivisionError, ValueError, OverflowError):
        return None


def build_record(log_data: dict, unit: int) -> dict:
    """Build the complete JSON record from a parsed log dict.

    Args:
        log_data: Output of parser.parse_log().
        unit:     Unit number (1, 2, …) — stamped into the reading's metadata.

    Returns:
        JSON-serializable dict matching the schema used by the application.
    """
    rd = log_data["readings"]
    rr = log_data["raw_registers"]

    def rfloat(reg_id):
        r = rd.get(str(reg_id))
        return extract_float(r["raw"]) if r else None

    def rint(reg_id):
        r = rd.get(str(reg_id))
        return extract_int(r["raw"]) if r else None

    def rbool(reg_id):
        r = rd.get(str(reg_id))
        return r["raw"].strip().lower() == "true" if r else None

    def rraw(reg_id):
        r = rd.get(str(reg_id))
        return r["raw"] if r else None

    # System outputs (register 30) and AXB outputs (register 1104)
    sys_outputs = parse_output_list(rraw(30) or "")
    axb_outputs = parse_output_list(rraw(1104) or "")

    # Humidity control
    # reg 362 (Active Dehumidify) not supported by this firmware — omitted
    dehumidify_active = None
    # reg 31109: 0x4000 = auto_dehumidification, 0x8000 = auto_humidification
    _mode_int = rr.get("31109")
    dehumidifier_mode = (
        "auto_dehumidification" if _mode_int == 0x4000
        else "auto_humidification" if _mode_int == 0x8000
        else None
    )
    # AXB bit 0x08: dehumidifier/reheat relay physically energized (gem has typo: "dehumidifer")
    dehumidifier_relay = "dehumidifer_reheat" in axb_outputs

    # Compressor stage: cc2 = stage 2, cc (solo) = stage 1, absent = 0
    if "cc2" in sys_outputs:
        comp_stage = 2
    elif any(s == "cc" for s in sys_outputs):
        comp_stage = 1
    else:
        comp_stage = 0
    comp_call = comp_stage > 0
    reversing_valve_on = "rv" in sys_outputs

    # Zone config: registers 31007-31012
    def _parse_zone(amb_reg, cfg1_reg, cfg2_reg):
        cfg = {
            **parse_ruby_hash(rraw(cfg1_reg) or ""),
            **parse_ruby_hash(rraw(cfg2_reg) or ""),
        }
        return {"ambient_temp_f": rfloat(amb_reg), "config": cfg}

    zone1 = _parse_zone(31007, 31008, 31009)
    zone2 = _parse_zone(31010, 31011, 31012)
    z1_cfg = zone1["config"]

    # Discharge temp calculation from suction/discharge pressures and suction temp
    suction_t = rfloat(1113)
    suction_p = rfloat(1116)
    discharge_p = rfloat(1115)
    discharge_t_calc = calc_discharge_temp(suction_t, suction_p, discharge_p)

    # Named raw registers with semantic labels
    named_registers = {
        _REGISTER_NAMES.get(reg_id, f"reg_{reg_id}"): {"register": int(reg_id), "value": value}
        for reg_id, value in rr.items()
    }

    # Extra registers (VS drive, amps, FP2, superheat, outdoor temp, config, …)
    # decoded to labelled engineering values. Purely additive; excludes the raw
    # set the primary record already uses so this is just the newly-captured data.
    additional_sensors = build_extra(rr, exclude=set(_REGISTER_NAMES))

    return {
        "metadata": {
            "unit": f"unit{unit}",
            "unit_location": _unit_location(unit),
            "query_start": log_data["query_start"],
            "query_end": log_data["query_end"],
            "software_version": _agent_version(),
        },
        "thermostat": {
            "ambient_temperature_f": rfloat(747),
            "relative_humidity_pct": rint(741),
            "mode": z1_cfg.get("mode"),
            "call": z1_cfg.get("call"),
            "heat_setpoint_f": rfloat(745),
            "cool_setpoint_f": rfloat(746),
            "damper": z1_cfg.get("damper"),
        },
        "heat_pump": {
            "total_watts": rr.get("1153"),
            "system_outputs": sys_outputs,
            "axb_outputs": axb_outputs,
            "water": {
                "entering_f": rfloat(1111),
                "leaving_f": rfloat(1110),
                "flow_gpm": rfloat(1117),
                "heat_rejection_btuh": rr.get("1157"),
                "heat_extraction_btuh": rr.get("1155"),
            },
            "air": {
                "return_f": rfloat(740),
                "supply_f": rfloat(900),
            },
            "blower": {
                "call": "blower" in sys_outputs,
                "watts": rr.get("1149"),
                "speed": rint(54),
                "speed_max": 7,
            },
            "loop_pump": {
                "call": "loop_pump" in axb_outputs,
                "watts": rr.get("1165"),
                "flow_gpm": rfloat(1117),
            },
            "compressor": {
                "call": comp_call,
                "watts": rr.get("1147"),
                "stage": comp_stage,
                "reversing_valve": reversing_valve_on,
                "reversing_valve_mode": "Cool" if reversing_valve_on else "Heat",
                "suction_temp_f": suction_t,
                "suction_pressure_psi": suction_p,
                "discharge_pressure_psi": discharge_p,
                "discharge_temp_calculated_f": discharge_t_calc,
                "superheat_f": rfloat(903),
                "saturated_evaporator_temp_f": rfloat(1124),
                "saturated_condenser_temp_f": rfloat(1134),
                "subcooling_cooling_f": rfloat(1136),
                "subcooling_heating_f": rfloat(1135),
            },
            "domestic_hot_water": {
                "enabled": rbool(400),
                "setpoint_f": rfloat(401),
                "temp_f": rfloat(1114),
                "pump": "dhw" in axb_outputs,
            },
        },
        "humidity_control": {
            "dehumidify_active":  dehumidify_active,
            "mode":               dehumidifier_mode,
            "relay_active":       dehumidifier_relay,
        },
        "dip_switch": parse_ruby_hash(rraw(33) or ""),
        "zones": {
            "zone1": zone1,
            "zone2": zone2,
        },
        "raw_registers": named_registers,
        "additional_sensors": additional_sensors,
    }
