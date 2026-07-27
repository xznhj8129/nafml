"""Convert INAV's extracted JSON into a NAFML document.

Reads the artifacts the msp_sdk generator produces directly, rather than the
lossy msp.yaml intermediate:

    msp_messages.json   message schema (ids, payloads, repeating, descriptions)
    inav_enums.json     enums with full C member names
    inav_defines.py     4900+ resolved #define values

Usage:
    python json_to_nafml.py --out out/
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path

import yaml

DEFAULT_DEFINES = Path(
    "/media/anon/WD2TB/DataVault/TechProjects/Software/GitRepos"
    "/inav_development/msp_sdk/generator/inav_defines.py"
)

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

# C types declared elsewhere in INAV; modelled as opaque aliases.
OPAQUE = {
}

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

IDENT = re.compile(r"^[A-Za-z_]\w*$")

stats: Counter = Counter()
notes: list[str] = []


def note(text: str) -> None:
    notes.append(text)


# --------------------------------------------------------------------------
# expression evaluation
# --------------------------------------------------------------------------

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.LShift,
    ast.RShift,
    ast.BitOr,
    ast.BitAnd,
    ast.BitXor,
    ast.USub,
    ast.UAdd,
    ast.Invert,
)


def safe_eval(expr: str, symbols: dict[str, int]) -> int | None:
    """Evaluate a C constant expression against known symbols."""
    text = expr.strip()
    if not text:
        return None
    text = re.sub(r"\b(\d+)[uUlL]+\b", r"\1", text)  # strip integer suffixes
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None
        if isinstance(node, ast.Name) and node.id not in symbols:
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, int):
            return None
    try:
        result = eval(  # noqa: S307 - AST is whitelisted above
            compile(tree, "<const>", "eval"), {"__builtins__": {}}, dict(symbols)
        )
    except Exception:
        return None
    return result if isinstance(result, int) else None


def load_defines(path: Path) -> dict[str, int]:
    if not path.exists():
        note(f"defines file not found: {path}")
        return {}
    out: dict[str, int] = {}
    for name, raw in re.findall(r"^\s{4}([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", path.read_text(), re.M):
        if raw == "None":
            continue
        try:
            value = ast.literal_eval(raw)
        except Exception:
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            out[name] = value
    return out


def configurable_defines(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        name
        for name, raw in re.findall(
            r"^\s{4}([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", path.read_text(), re.M
        )
        if raw == "None"
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def field_name(raw: str, where: str = "") -> str:
    """Carry the source name through untouched.

    These names come from INAV's C source (`boardIdentifier`, `commCapabilities`)
    and must stay greppable across the schema, the C headers and every generated
    library. Only names the extractor invented as prose need repair.
    """
    if IDENT.match(raw):
        return raw
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", raw)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"field_{cleaned}"
    note(f"{where}: field name {raw!r} is not an identifier; using {cleaned!r} (fix upstream)")
    stats["nonidentifier_names"] += 1
    return cleaned


def pick_storage(values: list[int]) -> str:
    low, high = min(values, default=0), max(values, default=0)
    order = (
        ["int8", "int16", "int32", "int64"]
        if low < 0
        else ["uint8", "uint16", "uint32", "uint64"]
    )
    for name in order:
        lo, hi = INT_RANGES[name]
        if lo <= low and high <= hi:
            return name
    return "int64" if low < 0 else "uint64"


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
    while shared and any(len(p) <= len(shared) for p in parts):
        shared.pop()
    return "_".join(shared)


# --------------------------------------------------------------------------
# enums
# --------------------------------------------------------------------------


def convert_enums(raw: dict, defines: dict[str, int]) -> tuple[dict, dict]:
    enums: dict = {}
    bitmasks: dict = {}

    for name, body in raw.items():
        source = body.get("_source")
        members: list[tuple[str, str, str | None]] = []
        for member, value in body.items():
            if member == "_source":
                continue
            condition = None
            if isinstance(value, list):
                # ["(6)", "AFATFS_USE_FREEFILE"] - a conditionally compiled member
                value, condition = (list(value) + [None, None])[:2]
                stats["conditional_members"] += 1
            members.append((member, "" if value is None else str(value), condition))

        if not members:
            continue

        symbols = dict(defines)
        resolved: list[tuple[str, int | None, str | None, str | None]] = []
        shifts: list[int] = []
        plain: list[int] = []
        counter = 0
        failed = None

        for member, raw_value, condition in members:
            text = raw_value.strip()
            if text == "":
                value = counter  # C auto-increment
            else:
                value = safe_eval(text, symbols)
            if value is None:
                failed = f"{member} = {text!r}"
                break
            symbols[member] = value
            counter = value + 1
            is_shift = "<<" in text
            if is_shift:
                shifts.append(value.bit_length() - 1 if value else 0)
            else:
                plain.append(value)
            alias = text if IDENT.match(text) and text in symbols else None
            resolved.append((member, value, alias, condition))

        if failed:
            note(f"enum {name}: unresolved value {failed} (source {source})")
            stats["enums_dropped"] += 1
            continue

        powers = [v for _, v, _, _ in resolved if v and (v & (v - 1)) == 0]
        nonzero = [v for _, v, _, _ in resolved if v]
        is_bitmask = bool(shifts) and len(powers) == len(nonzero)
        if shifts and not is_bitmask:
            composites = [m for m, v, _, _ in resolved if v and (v & (v - 1))]
            note(f"enum {name}: composite mask members {composites[:4]} - emitted as an enum")
            is_bitmask = False

        names = [m for m, _, _, _ in resolved]
        prefix = common_prefix(names)

        def label(text: str) -> str:
            if prefix and text.startswith(prefix + "_"):
                trimmed = text[len(prefix) + 1 :]
                if IDENT.match(trimmed):
                    return trimmed
            return text

        values: list[str] = []
        zero_member = None
        emitted: dict[str, int] = {}
        for member, value, alias, condition in resolved:
            text = label(member)
            if condition:
                note(f"enum {name}.{member}: conditional on {condition!r}; value kept")
            if is_bitmask and value == 0:
                zero_member = text
                continue
            if alias and alias in [m for m, _, _, _ in resolved[: len(emitted)]]:
                values.append(f"{text} = {label(alias)}")
            elif value in emitted.values() and not is_bitmask:
                earlier = next(k for k, v in emitted.items() if v == value)
                values.append(f"{text} = {label(earlier)}")
            else:
                values.append(f"{text} = {value.bit_length() - 1 if is_bitmask else value}")
            emitted[member] = value

        if not values:
            note(f"enum {name}: no emittable members")
            stats["enums_dropped"] += 1
            continue

        storage = pick_storage(
            [2 ** max(shifts, default=0)] if is_bitmask else (plain or [0])
        )
        entry: dict = {"storage": storage, "values": values}
        if zero_member:
            entry = {"zero": zero_member, **entry}
        if prefix:
            entry = {"prefix": prefix, **entry}
        if source:
            entry = {"description": f"From {source}", **entry}

        if is_bitmask:
            bitmasks[name] = entry
            stats["bitmasks"] += 1
        else:
            enums[name] = entry
            stats["enums"] += 1

    return enums, bitmasks


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------


class MessageConverter:
    def __init__(self, enums: set[str], defines: dict[str, int], configurable: set[str]):
        self.enums = enums
        self.defines = defines
        self.configurable = configurable
        self.constants: dict[str, dict] = {}

    def want_constant(self, name: str) -> str | None:
        """Declare a constant for a #define, returning the name to reference."""
        if name in self.constants:
            return name
        if name in self.defines:
            value = self.defines[name]
            self.constants[name] = {"type": pick_storage([value]), "value": value}
            return name
        if name in self.configurable:
            self.constants[name] = {
                "type": "uint16",
                "configurable": name,
                "description": "Build-configuration dependent; supplied at generation time.",
            }
            stats["configurable_constants"] += 1
            return name
        return None

    def field_type(self, msg: str, field: dict) -> str | None:
        ctype = field.get("ctype")
        enum_name = field.get("enum")

        if field.get("polymorph") or ctype == "Varies":
            stats["polymorph_fields"] += 1
            # Section 16: model the dominant shape, keep the alternative in the
            # description. A self-delimiting variant is a cstring, not a tail.
            desc = (field.get("desc") or "").lower()
            if "null-terminated" in desc or "nul-terminated" in desc:
                return "cstring"
            return "uint8[]"

        if enum_name and enum_name in self.enums:
            base = enum_name
        elif ctype in CTYPES:
            base = CTYPES[ctype]
        elif ctype and ctype.startswith("char["):
            base = "char"
            field = {**field, "array": True, "array_size": int(ctype[5:-1])}
        else:
            base = ctype
            if enum_name:
                note(f"{msg}.{field.get('name')}: enum {enum_name!r} was not emitted")
            else:
                note(f"{msg}.{field.get('name')}: unknown ctype {ctype!r}")
            return base

        if not field.get("array"):
            return base

        # A variable-length char field that documents its own terminator is a
        # NUL-terminated string, not a run-to-end-of-payload array.
        if base == "char" and not (field.get("array_size") or 0):
            desc = (field.get("desc") or "").lower()
            if "null-terminated" in desc or "nul-terminated" in desc:
                stats["cstrings"] += 1
                return "cstring"

        define = field.get("array_size_define")
        if define:
            resolved = self.want_constant(define)
            if resolved:
                return f"{base}[{resolved}]"
            note(f"{msg}.{field.get('name')}: unresolved size define {define!r}")
        size = field.get("array_size") or 0
        return f"{base}[{size}]" if size else f"{base}[]"

    def repeat_value(self, msg: str, raw, preceding: list[str]):
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        text = str(raw).strip()
        if IDENT.match(text):
            if text in preceding:
                return text
            resolved = self.want_constant(text)
            if resolved:
                return resolved
        value = safe_eval(text, self.defines)
        if value and value > 0:
            return value
        stats["repeat_until_end"] += 1
        note(f"{msg}: repeat {text!r} is not resolvable; decoded as until_end")
        return "until_end"

    def convert_field(self, msg: str, field: dict, preceding: list[str]) -> tuple[str, object] | None:
        # nested repeating groups are unnamed in the source
        raw_name = field.get("name") or ("items" if field.get("payload") else "")
        name = field_name(raw_name, msg) if raw_name else ""
        if not name:
            return None

        if field.get("payload"):
            inner: dict = {}
            names: list[str] = []
            for sub in field["payload"]:
                got = self.convert_field(msg, sub, names)
                if got:
                    inner[got[0]] = got[1]
                    names.append(got[0])
            if not inner:
                return None
            stats["repeated_groups"] += 1
            return name, {
                "repeat": self.repeat_value(msg, field.get("repeating"), preceding),
                "fields": inner,
            }

        type_text = self.field_type(msg, field)
        if type_text is None:
            return None
        if field.get("optional"):
            type_text = f"optional {type_text}"

        body: dict = {"type": type_text}
        if field.get("units"):
            body["unit"] = field["units"]
        desc = field.get("desc")
        if field.get("bitmask") and "bitmask" not in (desc or "").lower():
            desc = f"{desc} Bitmask." if desc else "Bitmask."
        if desc:
            body["description"] = desc
            stats["descriptions"] += 1
        if "value" in field:
            body["value"] = field["value"]
            stats["fixed_values"] += 1

        if set(body) == {"type"}:
            return name, type_text
        return name, body

    def convert_payload(self, msg: str, side: str, spec) -> object:
        if not isinstance(spec, dict):
            return None
        fields: dict = {}
        names: list[str] = []
        raw_fields = spec.get("payload") or []
        for index, field in enumerate(raw_fields):
            got = self.convert_field(f"{msg}.{side}", field, names)
            if got is None:
                continue
            key, value = got
            while key in fields:
                key += "_x"
            base = value if isinstance(value, str) else value.get("type", "")
            if isinstance(base, str) and base.endswith("[]") and index != len(raw_fields) - 1:
                note(f"{msg}.{side}.{key}: open array is not final; source order preserved")
                stats["open_array_not_final"] += 1
            fields[key] = value
            names.append(key)
        if not fields:
            return None
        payload: dict = {"fields": fields}
        if spec.get("repeating") is not None:
            payload = {
                "repeat": self.repeat_value(f"{msg}.{side}", spec["repeating"], []),
                **payload,
            }
        return payload


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

SPACED_SECTIONS = {"enums", "bitmasks", "structs", "messages"}


class IndentedDumper(yaml.SafeDumper):
    """Indent sequences under their key, matching the style in idl_spec.md."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def space_fields(lines: list[str]) -> list[str]:
    """Put a blank line between fields, but only after a multi-line one.

    Consecutive shorthand fields (`name: uint8`) stay tight; an expanded field
    that carries a type, unit and description gets separated from its neighbour.
    """
    child_indents: set[int] = set()
    for index, line in enumerate(lines[:-1]):
        if line.rstrip().endswith("fields:"):
            child_indents.add(indent_of(lines[index + 1]))

    out: list[str] = []
    previous_key: int | None = None
    for index, line in enumerate(lines):
        if indent_of(line) in child_indents and re.match(r"^\s*[A-Za-z_]\w*:", line):
            if previous_key is not None and index - previous_key > 1:
                out.append("")
            previous_key = index
        out.append(line)
    return out


def dump_entry(name: str, body: object) -> str:
    text = yaml.dump(
        {name: body},
        Dumper=IndentedDumper,
        sort_keys=False,
        width=1000,
        default_flow_style=False,
    ).rstrip("\n")
    lines = space_fields(text.split("\n"))
    return "\n".join(f"  {line}" if line else "" for line in lines)


def render_section(header: str, sections: dict) -> str:
    out: list[str] = [
        "# Generated by json_to_nafml.py. Do not edit.",
        "",
        f"version: 1",
        f"package: inav.msp",
    ]
    for section, body in sections.items():
        if not body:
            continue
        out.append("")
        out.append(f"{section}:")
        entries = [dump_entry(name, value) for name, value in body.items()]
        if section in SPACED_SECTIONS:
            for index, entry in enumerate(entries):
                if index:
                    out.append("")
                out.append(entry)
        else:
            out.extend(entries)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=Path, default=Path("msp_messages.json"))
    parser.add_argument("--enums", type=Path, default=Path("inav_enums.json"))
    parser.add_argument("--defines", type=Path, default=DEFAULT_DEFINES)
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args()

    defines = load_defines(args.defines)
    configurable = configurable_defines(args.defines)
    raw_enums = json.loads(args.enums.read_text())["enums"]
    raw_messages = json.loads(args.messages.read_text())["messages"]

    enums, bitmasks = convert_enums(raw_enums, defines)
    converter = MessageConverter(set(enums) | set(bitmasks), defines, configurable)

    messages: dict = {}
    for name, body in raw_messages.items():
        if not isinstance(body.get("code"), int):
            note(f"message {name}: no integer code")
            continue
        entry: dict = {"id": body["code"]}
        description = " ".join(
            part for part in (body.get("description"), body.get("notes")) if part
        ).strip()
        if body.get("not_implemented"):
            description = ("NOT IMPLEMENTED. " + description).strip()
        if body.get("replaced_by"):
            description = (description + f" Replaced by {', '.join(body['replaced_by'])}.").strip()
        if body.get("variants"):
            names = list(body["variants"])
            note(f"message {name}: {len(names)} length-dispatched variants {names[:3]}")
            stats["variant_messages"] += 1
            description = (
                description + f" Length-dispatched variants: {'; '.join(names)}."
            ).strip()
        if description:
            entry["description"] = description
        entry["request"] = converter.convert_payload(name, "request", body.get("request"))
        entry["reply"] = converter.convert_payload(name, "reply", body.get("reply"))
        messages[name] = entry
        stats["messages"] += 1

    args.out.mkdir(parents=True, exist_ok=True)

    # --- constants.yaml ---
    constants_section = {"constants": dict(sorted(converter.constants.items()))} if converter.constants else {}
    (args.out / "constants.yaml").write_text(render_section("constants", constants_section))

    # --- enums.yaml ---
    enums_section = {}
    if enums:
        enums_section["enums"] = enums
    if bitmasks:
        enums_section["bitmasks"] = bitmasks
    (args.out / "enums.yaml").write_text(render_section("enums", enums_section))

    # --- msp_v2.yaml ---
    msp_section = {"aliases": dict(OPAQUE)}
    if messages:
        msp_section["messages"] = messages
    (args.out / "msp_v2.yaml").write_text(render_section("msp_v2", msp_section))

    print(f"wrote {args.out}/constants.yaml")
    print(f"wrote {args.out}/enums.yaml")
    print(f"wrote {args.out}/msp_v2.yaml")
    for key, value in sorted(stats.items()):
        print(f"  {key}: {value}")
    print(f"  notes: {len(notes)}")
    Path("conversion_notes.txt").write_text("\n".join(notes) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
