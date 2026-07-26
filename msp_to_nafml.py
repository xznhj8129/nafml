"""Convert INAV's msp.yaml into a NAFML document.

msp.yaml carries no message ids, so they are sourced from msp_messages.json.

Usage:
    python msp_to_nafml.py --out inav_msp.yaml
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import yaml

CTYPES = {
    "uint8_t": "uint8",
    "uint16_t": "uint16",
    "uint32_t": "uint32",
    "uint64_t": "uint64",
    "int8_t": "int8",
    "int16_t": "int16",
    "int32_t": "int32",
    "int64_t": "int64",
    "float": "float32",
    "double": "float64",
    "bool": "bool",
    "char": "char",
}

# C types INAV declares elsewhere; modelled as opaque aliases.
OPAQUE = {
    "boxBitmask_t": "uint64",
    "ledConfig_t": "uint32",
    "escSensorData_t": "uint32",
    "dronecanNodeStatus_t": "uint32",
}

FIELD_RE = re.compile(
    r"^(?P<base>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"
    r"(?P<array>\[[^\]]*\])?"
    r"(?:\((?P<unit>.*)\))?$"
)

INT_RANGES = {
    "uint8": (0, 2**8 - 1),
    "uint16": (0, 2**16 - 1),
    "uint32": (0, 2**32 - 1),
    "uint64": (0, 2**64 - 1),
    "int8": (-(2**7), 2**7 - 1),
    "int16": (-(2**15), 2**15 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "int64": (-(2**63), 2**63 - 1),
}

stats: Counter = Counter()
dropped: list[str] = []


def snake(name: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    if not text or text[0].isdigit():
        text = "f_" + text
    return text


def pick_storage(values: list[int], signed_hint: bool = False) -> str:
    low, high = min(values, default=0), max(values, default=0)
    order = ["int8", "int16", "int32", "int64"] if low < 0 else ["uint8", "uint16", "uint32", "uint64"]
    for name in order:
        lo, hi = INT_RANGES[name]
        if lo <= low and high <= hi:
            return name
    return "int64" if low < 0 else "uint64"


def convert_enums(raw: dict) -> tuple[dict, dict]:
    """Return (enums, bitmasks) in NAFML form."""
    enums: dict = {}
    bitmasks: dict = {}
    for name, members in raw.items():
        if not re.match(r"^[A-Za-z_]\w*$", name):
            dropped.append(f"enum {name}: name is not an identifier")
            continue
        parsed: list[tuple[str, object]] = []
        shifts: list[int] = []
        plain: list[int] = []
        ok = True
        for entry in members:
            if not isinstance(entry, str):
                ok = False
                break
            if "=" not in entry:
                parsed.append((entry.strip(), None))
                continue
            member, raw_value = [p.strip() for p in entry.split("=", 1)]
            if not re.match(r"^[A-Za-z_]\w*$", member):
                dropped.append(f"enum {name}.{member}: member name is not an identifier")
                ok = False
                break
            if raw_value == "":
                dropped.append(f"enum {name}.{member}: empty value")
                ok = False
                break
            shift = re.fullmatch(r"\(?\s*1\s*<<\s*(\d+)\s*\)?", raw_value)
            if shift:
                position = int(shift.group(1))
                shifts.append(position)
                parsed.append((member, ("shift", position)))
                continue
            literal = re.fullmatch(r"\(?\s*(-?\d+)\s*\)?", raw_value)
            if literal:
                value = int(literal.group(1))
                plain.append(value)
                parsed.append((member, value))
                continue
            hexval = re.fullmatch(r"\(?\s*(0[xX][0-9a-fA-F]+)\s*\)?", raw_value)
            if hexval:
                value = int(hexval.group(1), 16)
                plain.append(value)
                parsed.append((member, value))
                continue
            if re.fullmatch(r"[A-Za-z_]\w*", raw_value):
                parsed.append((member, ("alias", raw_value)))
                continue
            dropped.append(f"enum {name}.{member}: unsupported value {raw_value!r}")
            ok = False
            break
        if not ok or not parsed:
            stats["enums_dropped"] += 1
            continue

        # A bitmask may carry a single zero member meaning "no flags set"
        # (BOOT_EVENT_FLAGS_NONE = 0). Anything else mixing shifts with plain
        # values is not a bitmask we can represent.
        is_bitmask = bool(shifts) and all(v == 0 for v in plain)
        if shifts and not is_bitmask:
            dropped.append(f"enum {name}: mixes bit shifts and plain values")
            stats["enums_dropped"] += 1
            continue

        # members named with the enum's own prefix get it stripped
        # msp.yaml names an enum either by its C prefix (SERVO, HW_SENSOR) with
        # members already stripped, or by its C typedef (gpsFixType_e), where
        # any shared prefix has to be recovered from the members themselves.
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            prefix = name
        else:
            prefix = common_prefix([m for m, _ in parsed])

        def strip(text: str) -> str:
            for candidate in (prefix, name):
                if candidate and text.startswith(candidate + "_"):
                    trimmed = text[len(candidate) + 1 :]
                    if re.match(r"^[A-Za-z_]\w*$", trimmed):
                        return trimmed
            return text

        label_of = {member: strip(member) for member, _ in parsed}
        labels = set(label_of.values())

        values = []
        zero_member = None
        for member, value in parsed:
            label = label_of[member]
            if is_bitmask and value == 0:
                zero_member = label
                continue
            if value is None:
                values.append(label)
            elif isinstance(value, tuple) and value[0] == "shift":
                values.append(f"{label} = {value[1]}")
            elif isinstance(value, tuple) and value[0] == "alias":
                # msp.yaml strips the prefix from member names but not from the
                # alias targets, which still carry the full C identifier
                target = label_of.get(value[1], strip(value[1]))
                if target not in labels:
                    dropped.append(f"enum {name}.{member}: alias target {value[1]!r} not found")
                    continue
                values.append(f"{label} = {target}")
            else:
                values.append(f"{label} = {value}")

        if is_bitmask:
            storage = pick_storage([2 ** max(shifts) if shifts else 0])
            body = {"storage": storage, "values": values}
            if zero_member:
                body = {"zero": zero_member, **body}
            if prefix:
                body = {"prefix": prefix, **body}
            bitmasks[name] = body
            stats["bitmasks"] += 1
        else:
            storage = pick_storage(plain or [0])
            body = {"storage": storage, "values": values}
            if prefix:
                body = {"prefix": prefix, **body}
            enums[name] = body
            stats["enums"] += 1
    return enums, bitmasks


def common_prefix(names: list[str]) -> str:
    if len(names) < 2:
        return ""
    parts = [n.split("_") for n in names]
    shared: list[str] = []
    for index in range(min(len(p) for p in parts)):
        token = parts[0][index]
        if all(p[index] == token for p in parts) and any(len(p) > index + 1 for p in parts):
            shared.append(token)
        else:
            break
    # never consume the whole name of the shortest member
    while shared and any(len(p) <= len(shared) for p in parts):
        shared.pop()
    return "_".join(shared)


def convert_field(msg: str, name: str, spec: str, known_enums: set[str]) -> tuple[str, object] | None:
    match = FIELD_RE.match(spec.strip())
    if not match:
        dropped.append(f"{msg}.{name}: unparsed type {spec!r}")
        return None
    base, array, unit = match.group("base"), match.group("array"), match.group("unit")

    if base.startswith("enum."):
        enum_name = base.split(".", 1)[1]
        if enum_name not in known_enums:
            dropped.append(f"{msg}.{name}: unknown enum {enum_name}")
            return None
        nafml_base = enum_name
    elif base in CTYPES:
        nafml_base = CTYPES[base]
    elif base in OPAQUE:
        nafml_base = base
    elif base == "Varies":
        # spec section 16: untyped payloads are modelled as opaque bytes
        nafml_base = "uint8"
        array = "[]"
        unit = None
    else:
        dropped.append(f"{msg}.{name}: unknown base type {base!r}")
        return None

    suffix = ""
    if array is not None:
        inner = array[1:-1].strip()
        suffix = f"[{inner}]" if inner else "[]"
    type_text = nafml_base + suffix

    if unit:
        return snake(name), {"type": type_text, "unit": unit.strip()}
    return snake(name), type_text


def convert_payload(msg: str, side: str, spec, known_enums: set[str]) -> object:
    if spec is None:
        return None
    fields: dict = {}
    items = list(spec.items())
    for index, (name, type_spec) in enumerate(items):
        if not isinstance(type_spec, str):
            dropped.append(f"{msg}.{side}.{name}: non-string type")
            continue
        converted = convert_field(f"{msg}.{side}", name, type_spec, known_enums)
        if converted is None:
            continue
        key, value = converted
        base = value if isinstance(value, str) else value["type"]
        # an open-ended array is only legal as the final field
        if base.endswith("[]") and index != len(items) - 1:
            trimmed = base[:-2] + "[1]"
            if isinstance(value, str):
                value = trimmed
            else:
                value["type"] = trimmed
            stats["open_arrays_pinned"] += 1
            dropped.append(
                f"{msg}.{side}.{name}: open-ended array is not the final field; pinned to [1]"
            )
        while key in fields:
            key += "_x"
        fields[key] = value
    if not fields:
        return None
    return {"fields": fields}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msp-yaml", type=Path, default=Path("msp.yaml"))
    parser.add_argument("--msp-json", type=Path, default=Path("msp_messages.json"))
    parser.add_argument("--out", type=Path, default=Path("inav_msp.yaml"))
    args = parser.parse_args()

    source = yaml.safe_load(args.msp_yaml.read_text())
    ids = {k: v["code"] for k, v in json.loads(args.msp_json.read_text())["messages"].items()}

    enums, bitmasks = convert_enums(source.get("enums") or {})
    known = set(enums) | set(bitmasks)

    messages: dict = {}
    for name, body in (source.get("messages") or {}).items():
        if name not in ids:
            dropped.append(f"message {name}: no id in msp_messages.json")
            stats["messages_dropped"] += 1
            continue
        entry = {
            "id": ids[name],
            "request": convert_payload(name, "in", body.get("in"), known),
            "reply": convert_payload(name, "out", body.get("out"), known),
        }
        messages[name] = entry
        stats["messages"] += 1

    document = {
        "version": 1,
        "package": "inav.msp",
        "aliases": dict(OPAQUE),
        "enums": enums,
        "bitmasks": bitmasks,
        "structs": {},
        "messages": messages,
    }
    document = {k: v for k, v in document.items() if v not in ({}, [])}

    text = yaml.safe_dump(document, sort_keys=False, width=200, default_flow_style=False)
    args.out.write_text(text)

    print(f"wrote {args.out}")
    for key, value in sorted(stats.items()):
        print(f"  {key}: {value}")
    print(f"  notes: {len(dropped)}")
    Path("conversion_notes.txt").write_text("\n".join(dropped) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
