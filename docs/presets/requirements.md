---
title: Requirements
category: Presets / Models
category_order: 70
order: 18
---

# Requirements

Two different things share vocabulary here — don't confuse them:

- **`requires:`** ([Hardware requirements](manifest.md#hardware-requirements)) — static,
  author-written VRAM/RAM guidance shown in the preset picker before a download. Pure
  metadata; never checked live, never reaches `pipeline.yml`.
- **`requirements:`** (this page) — a list of typed entries **checked live** against this
  running instance, surfaced to admins via `GET /api/presets/{preset_id}/requirements` and
  used to narrow generation routing away from a backend that can't actually run the preset.

## Requirements

`requirements:` is an optional top-level `preset.yml` list, distinct from `requires:` above:
where `requires:` is static guidance text shown before a download, `requirements:` is a list of
typed entries **checked live** against this instance, surfaced to admins via
`GET /api/presets/{preset_id}/requirements`. Each entry needs at least a `type:`; `hint:` (shown
alongside a failed check) and `optional: true` (this entry's absence doesn't count as a hard
failure) are accepted on every type.

```yaml
requirements:
  - type: binary
    name: ffmpeg
    hint: "Install ffmpeg and make sure it is on PATH"
  - type: binary
    names: [ffmpeg, ffmpeg.exe]   # first found on PATH wins - per-platform alternatives
  - type: python_package
    name: xformers
    version: ">=0.0.28"           # PEP 440 specifier; omit to accept any installed version
    optional: true
  - type: model
    tag: "flux-klein-9b"          # matches an admin Tag *name* - see below
  - type: model
    hash: "<sha256>"              # exact match against the depot
  - type: vram_min_gb
    gb: 16
  - type: platform
    os: [linux, darwin]           # linux | darwin | windows
```

Core ships five types (`src/features/presets/requirements/builtin.py`): `binary` (`shutil.which`,
or the first of `names:` found), `python_package` (`importlib.metadata` + a PEP 440 `version:`
specifier), `model` (a tag name or exact sha256 present and available in the depot — `tag:` matches
the same admin `Tag` concept a `configuration: {type: model_tags}` picker filter already uses, not
a marketplace-specific id), `vram_min_gb` (the resolved backend's physical VRAM total — see below),
and `platform` (`sys.platform`).
Engine-specific checks (a ComfyUI custom node or model) are not core — a plugin registers its own
under a `requirement_checkers:` manifest root (see `src.plugin_api.presets.RequirementChecker`).

`vram_min_gb` reads **physical device memory**, not a configured budget: it checks whichever
backend of the preset's engine is actually being asked (see "Host-scoped vs backend-scoped
checkers" below), against that specific backend INSTANCE's own hardware — never against the
backend's driver *name*, never just "some GPU exists on this host", and never just "an index
matches". Each backend resolves its own `ExecutionDeviceEvidence`
(`src.features.backends.base_backend.BaseBackend.resolve_execution_device()`):
`kind="this_host_gpu"` with a `gpu_index` (display/log only) and an `identity` — a stable hardware
UUID, not an index (`src.platform.runtime.gpu.DeviceIdentity`) — for a backend that actually runs
inference on one of this process's own GPUs (a `native` backend, from its admin-set `device:
cuda:N`); `kind="no_gpu"` for one explicitly configured with none at all (`device: cpu`) — definite
evidence, not "unknown"; `kind="remote"` for one that runs elsewhere (`native.remote`); and
`kind="unestablished"` (the default every plugin-provided backend gets unless it overrides
`resolve_execution_device()`, since an in-process backend that merely coordinates a pipeline talking
to some other server, like the ComfyUI plugin's own backend, is not thereby running inference on
this host's GPU).

A local reading is used only when BOTH the backend's own identity and this process's `GpuMonitor`
(`GpuMonitor.device_identity`, resolved once at init via NVML's UUID) report an identity, and those
identities are equal. An index match alone is deliberately never enough: an NVML enumeration index
and a CUDA ordinal are independently remappable (driver enumeration order; `CUDA_VISIBLE_DEVICES`),
so "index 0" on both sides is not proof they're the same physical card — a `native` backend
resolved to CUDA ordinal 0 could be remapped to a different GPU than the one this process's monitor
actually watches, and the check must read `unknown` for that case exactly as it does for a
genuinely different index. Anything short of an identity match (either side's identity
unestablished, or established but different) resolves to `unknown`, each with its own specific
`detail` explaining which — this process never substitutes a guess for hardware it cannot prove is
its own device.

**A `native` backend's `device` configured as a bare `"cuda"` (no explicit `:N`) never resolves an
identity at all.** That string is forwarded unchanged to the pipe that actually runs inference, and
torch resolves it at *execution* time to `torch.cuda.current_device()` for whichever thread runs
it — not necessarily index 0, and not something this check can know in advance without querying the
wrong thread at the wrong moment, which would prove nothing. `vram_min_gb` reads `unknown` for a
bare-`"cuda"` backend with a specific reason ("configured device 'cuda' has no explicit ordinal; the
worker's device cannot be established") rather than guessing index 0. An explicit `cuda:N` is
unaffected — that ordinal is fixed and its identity is resolved normally.

A preset's own `pipeline.yml` can also put a *different* device on a specific pipe:
`configuration: {device: ...}` on any pipe wins over the backend's configured device, since
`NativeBackend.prepare_pipes` only `setdefault`s its own onto a pipe's config
(`src/features/backends/native_backend.py`'s `prepare_pipes`; the `configuration:` block becomes
`pipe['config']` verbatim, unrendered, in `PresetProcessor._process_pipes` —
`src/features/presets/processor.py`). `vram_min_gb` checks for this STATICALLY, before any Jinja
rendering (independent of form data, since no requirements check has any to render with): a literal
override that disagrees with the backend's configured device, a templated value this check cannot
resolve without form data, or a non-string value (a dict/list/int — an authoring mistake, or an
unresolved indirection) all degrade an otherwise-matching reading to `unknown` rather than asserting
past authoring it cannot see the effect of or reason about. A literal override that already agrees,
or no override at all, is unaffected.

A passing `vram_min_gb` reading is evidence a card is physically present, not a guarantee a
given model fits: that is a function of the *loading budget* an admin configures per native backend
(`gpu_max_vram`, composed at load time via `effective_vram_budget_gb()` — see
[Backends](/admin?tab=docs&doc=dev/backends) "Why GPU settings live on the native backend"), which
can be set below the physical total. The static `requires:` block earlier in this document is a
third, unrelated thing again: author-written hardware guidance shown before a download, never
checked live.

### Host-scoped vs backend-scoped checkers

A checker declares a `scope` of `"host"` (the default) or `"backend"`. A `"host"` entry answers
something true of this *process* no matter which backend of the preset's engine ends up executing
it (a binary on `PATH`, an installed Python package) and is evaluated **once** per preset. A
`"backend"` entry's answer depends on *which* backend of the engine is asked — `comfyui-backend`'s
`comfyui_node`/`comfyui_model` checkers are `"backend"`-scoped, since one ComfyUI server can have a
custom node or model another doesn't, and so is core's own `vram_min_gb` (a local backend and a
remote-worker backend of the same engine do not share one VRAM reading) — and is evaluated **once
per enabled backend** of that engine. A plugin engine with several interchangeable backends should
mark its engine-specific checkers `"backend"`.

Every check resolves to one of three statuses: `ok`, `missing`, or `unknown` — `unknown` means the
check couldn't be evaluated here (a 5s per-check timeout, no local GPU reading, a checker type not
registered in this process, no backend of the engine to check a `"backend"`-scoped entry against)
and is never treated as `missing`. Results are cached per (preset, requirements-block content[,
backend]) — a host-scoped entry shares one cache slot across every backend, a backend-scoped entry
gets its own slot per backend; pass `?refresh=1` to force a fresh evaluation of all of them. The
preset list/detail endpoints expose the last-evaluated counts as `requirements_summary` (`{ok,
missing, unknown, optional_missing}`, `null` until first checked) for the engine's default backend
(or the preset's host-scoped entries alone, if the engine has no backend configured)
without ever running a check themselves. A `missing` entry marked `optional: true` is tallied under
`optional_missing`, not `missing` - an optional requirement's absence is advisory (see the
`optional:` field above), so it must not read as a hard failure in a "can I run this here" summary.

`GET /api/presets/{preset_id}/requirements[?backend_id=<id>][&refresh=1]` responds with one item per
`requirements:` entry, each labeled with its own `type`/`name`/`optional` alongside the check
outcome, plus a `backends` listing (one entry per enabled backend of the preset's engine, with its
own summary) so a UI can offer a per-backend selector:

```json
{
  "results": [
    {
      "type": "binary", "name": "ffmpeg", "optional": false,
      "status": "ok", "detail": "'ffmpeg' found on PATH (/usr/bin/ffmpeg)",
      "hint": null, "action": null
    },
    {
      "type": "model", "name": "flux-klein-9b", "optional": false,
      "status": "missing", "detail": "no available model tagged 'flux-klein-9b' found in the depot",
      "hint": null, "action": {"kind": "open_downloader", "payload": {"tag": "flux-klein-9b"}}
    },
    {
      "type": "python_package", "name": "xformers>=0.0.28", "optional": true,
      "status": "missing", "detail": "'xformers' is not installed",
      "hint": null, "action": null
    },
    {
      "type": "comfyui_node", "name": "FaceDetailer", "optional": false, "backend_id": "comfy-a",
      "status": "ok", "detail": "node 'FaceDetailer' is installed",
      "hint": null, "action": null
    }
  ],
  "summary": {"ok": 2, "missing": 1, "unknown": 0, "optional_missing": 1},
  "backends": [
    {"id": "comfy-a", "name": "Comfy A", "is_default": true, "summary": {"ok": 2, "missing": 1, "unknown": 0, "optional_missing": 1}},
    {"id": "comfy-b", "name": "Comfy B", "is_default": false, "summary": {"ok": 1, "missing": 2, "unknown": 0, "optional_missing": 1}}
  ],
  "checked_at": 1735689600.0
}
```

`results`/`summary` are the preset's host-scoped entries plus one chosen backend's own — the
requested `?backend_id=`, if it's an enabled backend of the preset's engine; otherwise the engine's
default backend; otherwise the enabled backend with the fewest hard misses. A `"backend"`-scoped
result item carries a `backend_id` field naming which backend it was checked against; a
`"host"`-scoped one never does. `backends` is empty when the preset's engine has no enabled backend
at all, in which case `results`/`summary` are the host-scoped entries alone.

`name` is a short, human label for the entry: each core checker derives its own (a binary's
`name`/first of `names`, `"<package><specifier>"`, a model's `tag`/short hash, `"<gb> GB"`, the
joined `os:` list). A plugin checker can supply one too by implementing `describe(spec) -> str`
(see `src.plugin_api.presets.RequirementChecker`); without it - or for a `type:` no checker is
registered for - the endpoint falls back to the entry's first string-valued field besides
`type`/`hint`/`optional`, else the type name itself.

### Requirements and generation routing

A backend whose last-checked requirements verdict has a hard (non-optional) `missing` entry is
excluded from generation routing for that preset: `GenerationOrchestrator` narrows its backend
candidates to those with no such cached miss (intersected with the existing model-availability
narrowing — see docs/models.md), the same way it narrows to backends holding every selected model.
A backend never checked yet counts as **unknown**, not missing — it stays a routing candidate, and a
background refresh is scheduled for it so a later generation benefits from a real verdict. Host-scoped
misses never narrow routing (they are evaluated once, so excluding on one would exclude every
backend) — surfacing those is the requirements panel's job, not routing's. If every enabled backend
of the engine has a hard miss, generation fails fast with an error naming each backend and its
missing requirements, rather than falling through to a backend that cannot actually run the preset.

