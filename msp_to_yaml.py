"""
Convert MSP messages JSON + INAV enums JSON to a simplified YAML format.

Usage:
    python msp_to_yaml.py [msp_messages.json] [inav_enums.json] [output.yaml]

Defaults to files in the same directory.
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
DEFAULT_MSP = BASE.parent / "docs" / "Inav" / "msp_messages.json"
DEFAULT_ENUMS = BASE.parent / "docs" / "Inav" / "inav_enums.json"


def format_type(field: dict, enum_name_map: dict) -> str:
    ctype = field.get("ctype", "unknown")
    if field.get("polymorph"):
        ctype = "Varies"
    if field.get("array"):
        size = field.get("array_size", 0)
        if size:
            ctype = f"{ctype}[{size}]"
        else:
            ctype = f"{ctype}[]"

    enum_ref = field.get("enum")
    desc = field.get("desc")

    if enum_ref:
        short = enum_name_map.get(enum_ref, enum_ref)
        ret = f"enum.{short} # {desc}"
    else:
        units = field.get("units")
        if units:
            if len(field.get("units"))>0:
                ret = f"{ctype}({units}) # {desc}"
        else:
            ret = f"{ctype} # {desc}"

    return ret


def format_payload_section(data: dict | None, enum_name_map: dict) -> dict | None:
    if data is None:
        return None
    fields = {}
    payload = data.get("payload")
    if payload is None:
        return None
    for item in payload:
        if "name" not in item:
            repeat_key = item.get("repeating", "N")
            for sub in item.get("payload", []):
                if "name" in sub:
                    fields[sub["name"]] = format_type(sub, enum_name_map)
            continue
        fields[item["name"]] = format_type(item, enum_name_map)
    return fields


def write_dict_block(lines: list, key: str, value: dict | None, indent: str):
    if value is None:
        lines.append(f"{indent}{key}: null")
    else:
        lines.append(f"{indent}{key}:")
        for k, v in value.items():
            lines.append(f"{indent}  {k}: {v}")


def find_common_prefix(names: list[str]) -> str:
    """Find the longest common CAPITAL_UNDERSCORE prefix shared by all names.

    Returns the prefix without trailing underscore, e.g. 'HW_SENSOR'.
    """
    if not names:
        return ""
    first = names[0]
    segments = first.split("_")
    common = ""
    for i, seg in enumerate(segments):
        candidate = "_".join(segments[:i + 1]) + "_"
        if all(name.startswith(candidate) for name in names):
            common = candidate.rstrip("_")
        else:
            break
    return common


def parse_enum_values(enum_name: str, enum_data: dict) -> tuple[str, list[str]]:
    """Parse enum entries, stripping the common prefix.

    Returns (short_name, entries) where short_name is the common prefix
    and entries are the stripped entry names with values.
    """
    raw_entries = []
    for key, value in enum_data.items():
        if key == "_source":
            continue
        if isinstance(value, list):
            value = value[0]
        raw_entries.append((key, value))

    names = [e[0] for e in raw_entries]
    prefix = find_common_prefix(names)

    entries = []
    for key, value in raw_entries:
        short = key[len(prefix) + 1:] if prefix else key
        entries.append(f"{short} = {value}")

    return prefix if prefix else enum_name, entries, bool(prefix)


def write_yaml(messages: dict, all_enums: dict, output_path: Path):
    # Compute short names for ALL enums
    enum_name_map = {}
    enum_entries = {}
    short_name_counts = {}
    short_name_assignments = {}

    has_prefix_map = {}
    for enum_name in all_enums:
        short_name, entries, has_prefix = parse_enum_values(enum_name, all_enums[enum_name])
        enum_entries[enum_name] = entries
        has_prefix_map[enum_name] = has_prefix

        if short_name not in short_name_counts:
            short_name_counts[short_name] = 0
            short_name_assignments[short_name] = []
        short_name_counts[short_name] += 1
        short_name_assignments[short_name].append(enum_name)

    # Resolve collisions: keep short name for unique ones, fall back to original for duplicates
    collision_comments = {}
    no_header_enums = {}
    for short_name, count in short_name_counts.items():
        if count == 1:
            orig = short_name_assignments[short_name][0]
            enum_name_map[orig] = short_name
            if not has_prefix_map.get(orig, True):
                no_header_enums[orig] = True
        else:
            for orig in short_name_assignments[short_name]:
                enum_name_map[orig] = orig
                collision_comments[orig] = short_name

    lines = []
    lines.append("messages:")
    for msg_name, msg in messages.items():
        lines.append(f"  {msg_name}:")
        req = format_payload_section(msg.get("request"), enum_name_map)
        rep = format_payload_section(msg.get("reply"), enum_name_map)
        write_dict_block(lines, "in", req, "    ")
        write_dict_block(lines, "out", rep, "    ")
        lines.append('')

    lines.append("")
    lines.append("enums:")
    for enum_name in sorted(all_enums.keys()):
        short = enum_name_map[enum_name]
        if enum_name in collision_comments:
            lines.append(f"  {short}: # collision: \"{collision_comments[enum_name]}\"")
        elif enum_name in no_header_enums:
            lines.append(f"  {short}: # no common prefix found")
        else:
            lines.append(f"  {short}:")
        for entry in enum_entries[enum_name]:
            lines.append(f"    - {entry}")
        lines.append('')

    output_path.write_text("\n".join(lines) + "\n")


def main():
    msp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MSP
    enums_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ENUMS
    output_path = Path(sys.argv[3]) if len(sys.argv) > 3 else msp_path.with_suffix(".yaml")

    with open(msp_path) as f:
        msp_data = json.load(f)["messages"]
    with open(enums_path) as f:
        enums_data = json.load(f)["enums"]

    messages = msp_data  # flat dict, keys are message names
    all_enums = enums_data  # flat dict, keys are enum names

    write_yaml(messages, all_enums, output_path)
    print(f"Wrote {len(messages)} messages and {len(all_enums)} enums to {output_path}")


if __name__ == "__main__":
    main()
