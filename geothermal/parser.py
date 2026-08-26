"""Parse aurora_fetch log output into structured data."""
import re
from pathlib import Path
from typing import Optional


_TIMESTAMP_RE = re.compile(r'^\w{3} \d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2} \w+$')


def parse_ruby_hash(s: str) -> dict:
    """Convert a Ruby-style {:key=>value, ...} string into a Python dict.

    Handles symbol values (:foo), integers, and floats.
    """
    s = s.strip().lstrip("{").rstrip("}")
    result = {}
    for m in re.finditer(r':(\w+)=>([\w.:-]+|-?\d+(?:\.\d+)?)', s):
        key = m.group(1)
        raw = m.group(2)
        if raw.startswith(":"):
            raw = raw[1:]
        else:
            try:
                raw = int(raw)
            except ValueError:
                try:
                    raw = float(raw)
                except ValueError:
                    pass
        result[key] = raw
    return result


def parse_output_list(s: str) -> list:
    """Parse a comma-separated output string into a list.

    e.g. 'rv, blower, accessory, 0x4040' -> ['rv', 'blower', 'accessory', '0x4040']
    """
    return [tok.strip() for tok in s.split(",") if tok.strip()]


def extract_float(s: str) -> Optional[float]:
    """Extract the first float from a string like '76.6°F' or '202.4 psi'."""
    m = re.search(r'-?[\d.]+', s)
    return float(m.group()) if m else None


def extract_int(s: str) -> Optional[int]:
    """Extract the first integer from a string like '47%' or '7'."""
    m = re.search(r'-?\d+', s)
    return int(m.group()) if m else None


def parse_log(path) -> dict:
    """Parse an aurora_fetch log file into a structured dict.

    Args:
        path: Path or str pointing to the log file.

    Returns:
        {
            "query_start": str | None,   # first timestamp line
            "query_end":   str | None,   # first timestamp after the '---' separator
            "readings":    dict,         # reg_id -> {"name": str, "raw": str}
            "raw_registers": dict,       # reg_id -> int
        }

    Log format produced by the aurora_fetch shell pipeline:
        <start timestamp>
        Label Name (reg_id): value
        ...
        ---
        reg_id: int_value
        ...
        <end timestamp>
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="latin-1")
    lines = text.strip().splitlines()

    result = {
        "query_start": None,
        "query_end": None,
        "readings": {},
        "raw_registers": {},
    }

    in_registers = False
    first_line = True

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if first_line:
            result["query_start"] = line
            first_line = False
            continue

        if line == "---":
            in_registers = True
            continue

        if in_registers:
            # Take the first timestamp we see as the end time; ignore extras
            if _TIMESTAMP_RE.match(line):
                if result["query_end"] is None:
                    result["query_end"] = line
            else:
                m = re.match(r'^(\d+): (.+)$', line)
                if m:
                    reg_id = m.group(1)
                    val = m.group(2).strip()
                    try:
                        result["raw_registers"][reg_id] = int(val)
                    except ValueError:
                        result["raw_registers"][reg_id] = val
        else:
            # "Sensor Label (reg_id): value"
            m = re.match(r'^(.+?) \((\w+)\): (.+)$', line)
            if m:
                name = m.group(1).strip()
                reg_id = m.group(2)
                value = m.group(3).strip()
                result["readings"][reg_id] = {"name": name, "raw": value}

    return result
