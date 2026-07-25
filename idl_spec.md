**YAML IDL Spec**

**1. Scope**
This is a custom schema IDL written in YAML.
YAML is only the carrier syntax. The schema language is defined here.
Specific example names are not definitive.

**2. YAML Subset**
- YAML version: `1.2`
- Allowed: mappings, sequences, plain scalars, quoted scalars, comments
- Additional 4-space indentation levels are legal for readability even when they do not add structure, as long as the YAML structure is unchanged
- Forbidden: anchors, aliases, tags, flow-style objects/lists, duplicate keys, tabs
- Unknown keys are errors
- string literals must be quoted with `"` in schema examples and authored schema

**3. Top-Level Document**

A schema document is either a **class expansion file** (`type: schema`) or a **module manifest** (`type: module`). They share the same IDL syntax for enums, maps, and models, but differ in header fields and resolution rules.

Common top-level keys (both types):
- `version` — required
- `type` — required, either `schema` or `module`
- `package` — required, the document's package/output module identifier
- `tags` — required, list of profile tags for subset selection (see section 17.3)
- `enums`
- `maps`
- `models`

**3.1. Class expansion file** (`type: schema`) — additional keys:
- `root` — required; the primary model owned by this schema file

**3.2. Module manifest** (`type: module`) — additional keys:
- `description` — optional, human-readable package purpose
- `requires` — optional, list of tags or module packages this module depends on
- `extend_variants` — optional, grafts new variants onto existing parents (see section 18.4)

Example class expansion file:
```yaml
version: 1
type: schema
package: state
root: State
tags:
  - core

enums: 
maps: 
models:
  State:
    description: "State data describing current object condition and telemetry."
    parent: Information
```

Minimal schema file:
```yaml
version: 1
type: schema
package: object
root: Object
tags:
  - core

models:
  Object:
    description: "Top-level object branch."
    parent: Root
    variants:
      - Entity
      ...
```

Example module manifest:
```yaml
version: 1
type: module
package: ew
description: "Electronic warfare actions, effects, protection, and spectrum management."
tags:
  - plugin123
requires:
  - core
```

**4. Names**
- Identifier regex: `^[A-Za-z_][A-Za-z0-9_]*$`
- Names must be unique across `enums`, `maps`, and `models`
- Field names use the same identifier regex
- All model and enum names are globally unique and imported flat — the ontological hierarchy is encoded in parent/variants relationships, not in qualified import paths
- For schema files, `package` must match the schema file basename without `.schema.yaml`
- For module files, `package` must be globally unique across loaded packages

**5. Primitive Types**
Built-in primitive types:
- `string`
- `int`
- `int8`
- `int16`
- `int32`
- `int64`
- `uint8`
- `uint16`
- `uint32`
- `uint64`
- `float`
- `bool`
- `bytes`
- `any`

**6. Type Expressions**
Valid type expressions:
- `string`
- `TaskType`
- `GlobalPosition`
- `list[string]`
- `list[GlobalPosition]`
- `map[string, string]`
- `map[string, GlobalPosition]`
- `tuple[float, float]`
- `tuple[string, int, bool]`
- `optional string`
- `optional list[string]`
- `const TaskType`
- `const map[string, GeometryTypes]`

Rules:
- `optional` means the field may be absent
- `const` means the field value is fixed and not user-supplied
- `optional const ...` is invalid
- `tuple[T1, T2, ...]` is an ordered fixed-length heterogeneous container
- no inline anonymous object types
- write imports, then `\n##Enums\n`, then enums, then `\n##models\n`, then models
- One newline between enums, two newlines between models
- Order by ontological order
- Use IntEnum for where only one aspect of the same attritube can be true
- Use List[IntEnum] as a bitflag for where many aspects of the same attritube can be true
- Preserve descriptive comments

**7. Enums**
Syntax:
```yaml
enums:
  EnumName:
    - VALUE = 0
    - NEXT
    - OTHER = 7
    - FLAG_A = 1 << 1
    - FLAG_B = 1 << 2
```

Rules:
- Each item is `NAME`, `NAME = INT`, `NAME = "string"`, or `NAME = 1 << INT`
- First unassigned item gets `0`
- Later unassigned items auto-increment from previous
- Any enum using `1 << INT` values is generated as a bitflag enum
- Enum numeric values must be unique
- Enum names must be unique within the enum

**8. Constant Maps**
Syntax:
```yaml
maps:
  MapName:
    type: map[string, GeometryTypes]
    value:
      "Point": POINT
      "Polygon": POLYGON
```

Rules:
- `maps` defines named constant maps
- `type` must be `map[K, V]`
- `value` must be a YAML mapping
- keys and values must match the declared map type

**9. models**
Syntax:
```yaml
models:
  ModelName:
    description: "Human-readable model purpose."
    fields:
      field_a: string
      field_b: optional string
      field_c: float = 0.0
      field_d: const TaskType = MOVE
```

Rules:
- `description` is optional on ordinary models and required on a schema file's root model
- `parent` is optional
- `fields` is optional. If absent, the model defines no own fields (inherited fields from a parent still apply)
- `parent` must reference another model
- inheritance is single-parent only
- child fields are appended after parent fields
- redefining an inherited field is an error
- a model may define `variants` to create a typed child family (see section 13)
- a child model inherits all fields from its `parent`
- if the parent defines `variants`, the child is a typed variant of that parent
- a model may be both a child and a parent, allowing nested typed hierarchies
- branch models should stay flat and lightweight; inline nested authoring is reserved for final fielded leaves inside `variants`

**10. Field Shorthand**
Allowed shorthand forms:
- `name: Type`
- `name: optional Type`
- `name: Type = scalar_default`
- `name: Type = null`
- `name: optional Type = scalar_default`
- `name: const Type = scalar_or_enum_value`

Examples:
```yaml
task_id: string
remarks: optional string
speed_ms: float = 0.0
expires_at: int64 = null
task_type: const TaskType = MOVE
path: list[GlobalPosition]
```

Rules:
- shorthand defaults are only for scalar values, enum values, and `null`
- list/map/object defaults must use expanded form
- inline form is preferred for fields that only need a type, optionality, or scalar default
- expanded form is used when a field needs additional metadata

**11. Expanded Field Form**
Syntax:
```yaml
pwm:
  type: int
  min: 900
  max: 2100
  default: 1500

alphabet:
  type: list[string]
  default:
    - a
    - b
    - c
  description: Human text
```

Allowed keys:
- `type` required
- `default` optional
- `value` optional
- `description` optional, comments preferred if inline

Rules:
- `default` is for non-const fields
- `value` is for const fields
- `default` and `value` may not both appear
- `description` is free text
- `required` is not allowed; use `optional` in the type instead

Example const map field:
```yaml
geomap:
  type: const map[string, GeometryTypes]
  value:
    "Point": POINT
    "Polygon": POLYGON
```

**12. Field Presence Semantics**
- Required field: any field not marked `optional`, not `const`, and with no `default`
- Optional field: any field with `optional`
- Nullable field: any field with `default: null` or inline `= null`
- Defaulted field: omission is allowed; default is applied
- Const field: omission is allowed; fixed value is applied

**13. Variants**
A parent/variants relationship is:
- field inheritance
- a typed variant family, equivalent to a tagged union or protobuf `oneof`
- an implicit discriminator enum, derived from the variant model names

Syntax:
```yaml
models:
  Task:
    variants:
      - MoveTask
      - PatrolTask

  MoveTask:
    parent: Task
    fields:
      destination: GlobalPosition
      speed_ms: optional float

  PatrolTask:
    parent: Task
    fields:
      path: list[GlobalPosition]
      loiter_s: optional int

```

Rules:
- `variants` is a direct key on the model
- `variants` is always a list of declared child model names
- `variants:` with no entries is valid only as an extension point for modules
- each item must reference a declared child model
- each referenced child model must declare `parent: <parent model>`
- `variants` is only necessary for general ontological groups, not every model all the way down
- enum values auto-increment from 0 in declaration order
- the implicit enum is named `{ModelName}_type` (e.g., `Task` → `Task_type`) and is a first-class referenceable type
- each implicit enum member is derived from the child model name by dropping the parent model's prefix when present and converting to `SCREAMING_SNAKE_CASE` (`MoveTask` under `Task` becomes `MOVE`)
- the discriminator is reserved metadata on each child instance (accessible as `_type`). It cannot collide with user-defined field names
- the effective shape of a child model includes:
  - all inherited parent fields
- the child model's own fields
- the parent model itself is a type and may be used in fields like any other model
- a model may define at most one `variants` block
- a child model may also define `variants` of its own, allowing nested typed hierarchies
- when naming a child model, if necessary, prefix only the parent's name
- source readability takes precedence over normalized expansion

**14. Comments**
- YAML `#` comments are allowed anywhere YAML allows them
- comments have no schema meaning

**15. Errors**
These are always errors:
- unknown top-level section
- unknown model/enum/map reference
- duplicate names
- invalid type expression
- parent is not a model
- redefining inherited fields
- `optional const ...`
- list/map/object inline defaults in shorthand form
- `required: true/false`
- `variants` contains a value that is not a declared model
- `variants` contains a model whose `parent` is not the declaring model
- `package` does not match the schema file basename without `.schema.yaml`
- missing `version`, `type`, `package`, or `tags`
- missing `root` on a schema file
- `root` on a schema file does not reference a declared model
- schema file root model is missing `description`
- top-level `description` on a schema file
- `root` on a module file
- `type` is not `schema` or `module`
- a module's `extend_variants` references a parent that has no `variants` block
- a module's `extend_variants` introduces a derived variant name that collides with an existing one
- a module's `requires` list is not satisfied at load time
- a module redefines or alters an existing model, enum, or field from core or another module
- a module's model declares a `parent` that does not exist in core schema or required modules

**16. Not Supported**
- anchors / aliases
- multiple inheritance
- anonymous inline models outside terminal variant leaves
- JSON-Schema keys like `$defs`, `oneOf`, `additionalProperties`

**17. File Organization**

Each schema file is a package centered on one primary model, not a subject-domain bucket. If a domain concept (e.g. electronic warfare) touches multiple ontological branches (Task, Intel, Plan, Instruction), those models belong in their respective class files, not in a single "ew.yaml."

Schema files live under `lib/schema/core/` and may be organized in subdirectories for readability. Directory placement is organizational only and is not part of the schema package. A schema file's `package` is the basename without `.schema.yaml`; `root` is the primary model in that package. The core directory layout should mirror the first-level ontology branches:

```text
schema/
  core/
    core.schema.yaml
    definition/
    struct/
    objects/
    control/
    communication/
    data/
  modules/
    plugin123/
      plugin123.schema.yaml
```

**17.1. One file, one package**
- Every schema file declares a `root` model: the default entry point for the package, not a hard boundary
- Files are not necessarily hard-bound to a model family; `command.schema.yaml` containing `Command` is a convenience, not a rule
- Split a file only when more compartmentalization is necessary
- A schema package may also contain local enums, maps, variants, and auxiliary models used by that package
- The model graph is declared only by `parent` and `variants`
- If a model branch has its own schema file, that is an organizational choice. The model graph still comes only from `parent` and `variants`.
- Enums used exclusively by models in the file are co-located. Enums shared across files belong in the root or a common ancestor file

**17.2. File header**
The header declares the package identity and primary model:

```yaml
version: 1
type: schema
package: information
root: Information
tags:
  - core
```

- `type` — always `schema` for class expansion files
- `package` — package/output module identifier, matching the schema file basename
- `root` — primary model declared in the file
- `tags` — profile tags that classify this file for subset selection (see 17.3)

**17.3. Tags and profiles**
Tags classify files by domain applicability. A **profile** is a named set of tag inclusion/exclusion rules that selects a subset of the schema.

Reserved tags:
- `core` — foundational, always included in every profile.

Tags are additive: a file tagged `[core, plugin123]` is both foundational and plugin123-relevant. A profile that excludes `plugin123` drops any file whose tags include `plugin123` (unless the file is also tagged `core`, in which case `core` takes precedence as the always-included foundation).

Profile resolution rules:
1. All files tagged `core` are always included
2. A profile declares which non-core tags to include or exclude
3. Include is the default — a profile only needs to declare exclusions
4. If a file carries no matching include tag and no matching exclude tag, it is included by default
5. A file excluded by a profile is entirely absent — its models, enums, and maps do not exist in that profile's schema

Example profiles:
```yaml
# Normal operations — no plugin123 concepts
profile: default
exclude:
  - plugin123

# Full plugin123 C2
profile: plugin123_c2
include_all: true

# Maritime ISR — maritime + core only
profile: maritime_isr
include:
  - core
  - maritime
exclude_untagged: true
```

**17.4. Model tree resolution**
The ontology tree is resolved from the model graph. `parent` declares inheritance; `variants` declares typed child families. There is no separate schema-file branch graph.

**18. Modules**

A module is a self-contained extension that grafts domain-specific models, enums, and maps onto the existing class tree without modifying core schema files. Modules are the mechanism for domain-specific, third-party, and classified extensions.

**18.1. What a module is**
- A separate YAML manifest file, not a class expansion file
- It declares new models, enums, and maps that attach to existing parents in the class tree
- It does not modify, override, or redefine anything in core schema files
- It is the extension boundary: adding capability means dropping in a module, not forking the tree

**18.2. Module manifest format**
Module files live under `lib/schema/modules/` as YAML files following the IDL syntax for enums, maps, and models, plus module-specific header fields. A domain module may be organized as a directory containing several `type: module` files. Directory placement is organizational only; each module file still has a globally unique `package`.

```yaml
version: 1
type: module
package: ew
description: "Electronic warfare actions, effects, protection, and spectrum management."
tags:
  - plugin123
requires:
  - core

enums:
  EWActionType:
    - JAM = 0
    - SPOOF
    - DECEIVE
    - INTERCEPT
    - DIRECTION_FIND
    - MONITOR
    - DENY

  JamType:
    - NOISE = 0
    - BARRAGE
    - SPOT
    - SWEEP
    - RESPONSIVE
    - FOLLOWER

models:
  EWActionTask:
    parent: Task
    fields:
      ew_action_type: optional EWActionType
      target_signal_id: optional string
      platform_id: optional string
      duration_s: optional float

  JamTask:
    parent: EWActionTask
    fields:
      jam_type: optional JamType
      frequency_range_hz: optional FloatRange

  DirectionFindingResult:
    parent: Intel
    fields:
      bearing_from_sensor_deg: optional float
      sensor_position: optional LLA
      estimated_emitter_position: optional LLA

  SpectrumAllocation:
    parent: Plan
    fields:
      frequency_range_hz: optional FloatRange
      assigned_to_id: optional string
      priority: optional int
```

Header fields (in addition to the common fields in section 3):
- `requires` — list of tags or module packages this module depends on. The resolver must include those dependencies before this module can be applied

**18.3. Rules**
- A module's models must declare `parent` referencing a model from the core schema or from a required module
- A module may define models that extend any branch of the class tree — this is the key difference from class expansion files, which are locked to one branch
- All names (models, enums, maps) must be globally unique across core schema and all loaded modules
- A module may add new variants to an existing parent's `variants` block via `extend_variants` (see 18.4)
- A module cannot redefine, remove, or alter existing models, enums, or fields
- Modules are optional — the core schema is complete and valid without any modules loaded
- A module's `requires` list must be satisfied before the module is loaded; unsatisfied dependencies are an error

**18.4. Extending variants**
When a module adds a new child to an existing parent that has a `variants` block, it uses `extend_variants` to graft new members onto the parent's discriminator:

```yaml
extend_variants:
  Task:
    - EWTask
  Intel:
    - DirectionFindingResult
    - ThreatEmitter
  Plan:
    - SpectrumAllocation
```

Rules:
- `extend_variants` keys must reference models from core schema or required modules
- The referenced parent must already have a `variants` block
- `extend_variants` values are lists of declared child model names
- New derived variant names must not collide with existing variant names on that parent
- The new variant enum values auto-increment from the last existing value on the parent
- Each model listed must declare `parent` matching the extended parent

**18.5. Module resolution**
When building a schema with modules:
1. Resolve the core schema files and model graph
2. For each selected module, verify `requires` are satisfied
3. Apply `extend_variants` to graft new variant members onto existing parents
4. Merge the module's models, enums, and maps into the global namespace
5. Validate: no name collisions, no orphan parents, no cycles

Module selection is independent of tag filtering but compatible with it:
- A module tagged `plugin123` is excluded when the profile excludes `plugin123`
- A module with no conflicting tags is included by default when explicitly selected
- Modules are never auto-included — they must be explicitly selected by name or by matching tag inclusion rules
