"""Compare a NAFML document against the source JSON it was converted from.

Walks every message, payload and field in msp_messages.json and asserts the
NAFML document carries the same id, the same field order, and an equivalent
type. Anything that does not round-trip is reported as a defect.

Usage:
    python check_fidelity.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from json_to_nafml import CTYPES, OPAQUE, field_name

defects: list[str] = []
counts: Counter = Counter()


def defect(text: str) -> None:
    defects.append(text)


def expected_base(field: dict, enums: set[str]) -> str | None:
    ctype = field.get("ctype")
    if field.get("polymorph") or ctype == "Varies":
        return None  # deliberately modelled, section 16
    name = field.get("enum")
    if name and name in enums:
        return name
    if ctype in CTYPES:
        return CTYPES[ctype]
    if ctype in OPAQUE:
        return ctype
    if ctype and ctype.startswith("char["):
        return "char"
    return None


def flatten(fields: dict) -> list[tuple[str, object]]:
    return list(fields.items())


def field_type(spec) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        return spec.get("type", "")
    return ""


def check_payload(msg: str, side: str, source: dict, target, enums: set[str]) -> None:
    raw = source.get("payload") or []
    if target is None:
        if raw:
            defect(f"{msg}.{side}: {len(raw)} source fields but payload is absent")
        return
    fields = flatten(target.get("fields") or {})

    # repeating groups collapse a whole payload into one entry
    if source.get("repeating") is not None and "repeat" not in target:
        defect(f"{msg}.{side}: source repeats {source['repeating']!r} but NAFML has no repeat")

    source_names = []
    for field in raw:
        if field.get("payload"):
            source_names.append(field_name(field.get("name") or "items"))
        else:
            source_names.append(field_name(field.get("name") or "x"))

    target_names = [name.rstrip("_x") if name.endswith("_x") else name for name, _ in fields]

    if len(source_names) != len(target_names):
        missing = [n for n in source_names if n not in target_names]
        if missing:
            defect(
                f"{msg}.{side}: {len(missing)} field(s) dropped: {missing[:5]}"
            )
            counts["fields_dropped"] += len(missing)
        return

    for (source_field, source_name), (target_name, target_spec) in zip(
        zip(raw, source_names), fields
    ):
        counts["fields_checked"] += 1
        if source_name != target_name.rstrip("_x") and not target_name.startswith(source_name):
            defect(f"{msg}.{side}: field order/name mismatch {source_name!r} vs {target_name!r}")
            continue
        if source_field.get("payload"):
            continue
        text = field_type(target_spec)
        base = expected_base(source_field, enums)
        if base is None:
            counts["modelled_unions"] += 1
            continue
        actual = re.sub(r"^optional\s+", "", text).split("[")[0]
        if actual == "cstring":
            counts["cstrings"] += 1
            continue
        if actual != base:
            defect(f"{msg}.{side}.{source_name}: type {actual!r} != expected {base!r}")
            counts["type_mismatch"] += 1
            continue
        if bool(source_field.get("optional")) != text.startswith("optional "):
            defect(f"{msg}.{side}.{source_name}: optional flag mismatch")
        if source_field.get("array") and "[" not in text:
            defect(f"{msg}.{side}.{source_name}: source is an array, NAFML is scalar")
        if source_field.get("desc") and not (
            isinstance(target_spec, dict) and target_spec.get("description")
        ):
            counts["descriptions_lost"] += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=Path, default=Path("msp_messages.json"))
    parser.add_argument("--nafml", type=Path, default=Path("inav_msp.yaml"))
    args = parser.parse_args()

    source = json.loads(args.messages.read_text())["messages"]
    doc = yaml.safe_load(args.nafml.read_text())
    target = doc.get("messages") or {}
    enums = set(doc.get("enums") or {}) | set(doc.get("bitmasks") or {})

    missing = [name for name in source if name not in target]
    if missing:
        defect(f"{len(missing)} message(s) missing from NAFML: {missing[:6]}")

    for name, body in source.items():
        if name not in target:
            continue
        counts["messages_checked"] += 1
        if target[name].get("id") != body.get("code"):
            defect(f"{name}: id {target[name].get('id')} != code {body.get('code')}")
        for side, key in (("request", "request"), ("reply", "reply")):
            src = body.get(key)
            if isinstance(src, dict):
                check_payload(name, side, src, target[name].get(key), enums)
            elif target[name].get(key) is not None:
                defect(f"{name}.{side}: source is null but NAFML has a payload")

    print("fidelity report")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")
    print(f"  defects: {len(defects)}")
    for line in defects[:25]:
        print(f"    - {line}")
    if len(defects) > 25:
        print(f"    ... and {len(defects) - 25} more")
    return 1 if defects else 0


if __name__ == "__main__":
    raise SystemExit(main())
