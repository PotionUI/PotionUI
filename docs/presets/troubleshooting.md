---
title: Troubleshooting Presets
category: Presets / Models
category_order: 70
order: 22
---

# Troubleshooting Presets

An index of every preset-authoring error class: what raises it, and how to fix it. Start with
`python scripts/preset_lint.py <preset_dir>` (errors fail the run, warnings don't) and
`python scripts/preset_render.py <preset_dir> <mode>` (renders the real pipeline against
fixture form data — the fastest way to reproduce a build error without a live generation).

## Runtime build errors

**StrictUndefined (`pipeline.yml` build error).** Every `{{ ... }}`/`{% ... %}` reference in
`pipeline.yml` is evaluated against `StrictUndefined` (see
[Pipelines → Strict evaluation](pipelines.md#strict-evaluation--missing-values-are-build-errors)):
referencing a variable or dict key that doesn't exist **raises** instead of silently becoming
`None`/`""`. The error names `preset_id`, `source_file`, `mode`, `form_name`, `pipe_id`,
`config_path`, `expression`, and the underlying cause — the fix is almost always adding
`| default(...)`, or confirming the field actually exists in every form variant that shares this
pipeline (see [Pipelines → Strict evaluation](pipelines.md#strict-evaluation--missing-values-are-build-errors)
for the two cases where a guard is load-bearing vs. redundant).

**`form_validation_failed` (422).** A generation request whose submitted `form_data` fails
`bind_form` validation — required field missing, a value out of range, an unknown select option —
is rejected before it ever reaches `pipeline.yml`, with a structured `field_errors` map instead of
one opaque string. See [Forms → Form binding and validation](forms.md#form-binding-and-validation-bind_form)
for the full response shape.

**`validate_pipeline` errors (preset load).** A malformed `pipeline.yml` — an unknown pipe `name:`,
a `configuration:` key the pipe doesn't declare, an `input:` entry naming a pipe that was never
declared, `type_cast`/`transform` typos in a ComfyUI `field_mappings` entry — fails preset load
with an error naming the preset, mode, and pipe. Run `python scripts/preset_render.py <preset_dir>
<mode>` (see [Testing → Running the suite](testing.md)) to reproduce a build error locally with
fixture form data, without a live generation.

## Linter message classes

`scripts/preset_lint.py` (see [Testing → Linting (CLI)](testing.md#linting-cli)) groups its checks
by what they validate. The manifest/media/variant/speed-profile/plugin-contribution checks are
summarized in [Testing → Linting (CLI)](testing.md#linting-cli); the `tests.yml`, `pipeline.yml`,
and external-fragment-field checks below are broken out here since they're each their own small
catalog of message → cause → fix.

## `tests.yml` lint messages

`scripts/preset_lint.py` validates every `tests.yml` it finds alongside the schema checks above.
It:

- warns (informational only) when a preset has **no** `tests.yml` at all — most presets don't have
  one yet, this isn't an error;
- errors on a `tests.yml` that fails to parse or fails schema validation, naming the offending case;
- errors on **duplicate case names** within a file;
- errors on a **malformed `sha256`** (not 64 hex digits — the all-zero placeholder is exempt by
  construction, since it *is* 64 hex digits);
- errors when a case's `mode:` **isn't declared** in the preset's `preset.yml` `modes:`;
- errors when a `form:` key **collides with a `models:` key** in the same case;
- warns when a case uses the placeholder sha256 without the `needs-model` tag.

## `pipeline.yml` lint messages

`scripts/preset_lint.py` parses each mode's `pipeline.yml` as YAML (comments never trip a check)
and enforces the template contract described under [Pipelines → Template contexts](pipelines.md#template-contexts):

- **Deleted context is an error.** Any scalar whose `{{ }}`/`{% %}` regions reference the removed
  surface — `get_form(`, `value(`, `setting(`/`config(`, `contains(`, the `input.*` context, or a
  bare `@object:`/`@dict:` directive — is flagged with a migration hint. Strict evaluation makes
  every one of these a hard build failure, so lint catches them before a user does.
- **String `enabled:` must be an exact expression.** A pipe-level `enabled:` that is a string but
  not exactly one `{{ expression }}` block is an **error** — it would string-render instead of
  evaluating to a bool, so the pipe could never be enabled. Use a YAML bool or a single
  expression.
- **`@loop` `items:` strings must be exact expressions.** `items` is evaluated natively to a
  list/dict/range; a mixed/string template there is an **error** (a literal YAML list is fine).
- **`{{ form.<name> }}` references must resolve.** The linter walks the mode's real form tree —
  every variant, external `tabs/*.yml` fragments, and statically-expanded `@loop` field
  generators (so `controlnet_2_model` from a `count: 3` loop is known) — plus the
  runtime-injected keys (`video_director`, `timeline`, `llm`, `prompt_timeline`). A reference to
  a field that doesn't exist anywhere in that tree **and** has no `| default(...)` in its
  expression is a **warning**: under strict evaluation it becomes a runtime build error the first
  time the field is absent.

## Field `default:` lint messages in external tab fragments

`form.yml`'s own fields are schema-validated wherever the form is loaded, but most fields live in
external `tabs/*.yml` fragments referenced via `children:`. The linter follows those references
and runs each fragment's `fields:` through the same `FieldSpec` validation the loader uses, so a
typed-default mistake — `default: "30"` on a slider, `default: "true"` on a checkbox, Jinja inside
a `default:` — surfaces as a lint **error** with the schema's message instead of only failing at
preset load.


## ComfyUI-specific failures

Import-time lint errors (bad node references, type-cast mismatches, missing defaults) and
"runs in ComfyUI but fails here" failures (model naming, missing custom nodes, a UI export
instead of an API export) are their own catalog — see
[ComfyUI Presets → Troubleshooting](comfyui.md#troubleshooting).
