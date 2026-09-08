#!/usr/bin/env python3
"""Generate `docs/pipes.md` and `docs/preset-context.md` from the live code.

    python scripts/pipes_reference.py                     # (re)write both files
    python scripts/pipes_reference.py --allow-import-errors  # write even if some
                                                               # pipes fail to import
                                                               # here (explicit
                                                               # "failed to import"
                                                               # sections for them)
    python scripts/pipes_reference.py --check              # exit 1 on drift

Both files are entirely generated: every fact in them is read off the running
code (`PipeCatalog`, `PipesDocumenter`, `TemplateFunctionsDocumenter`, the
preset linter's injected-form-key set, `DIRECTORY_TO_MODEL_TYPE`, the
field-type registry, and `content/presets/_shared/**`) — never hand-typed
prose. `--check` is wired into `scripts/docs_lint.py`, so drift between these
files and the code they describe fails the build the same way a typed-doc
schema error does.

The committed `docs/pipes.md` must never depend on which box generated it: a
pipe that fails to import (see docs/testing-notes.md's cv2/numpy
container-environment notes) is never silently dropped from the catalog scan
- `PipeCatalog` logs "Error loading pipe <module>: <error>" and simply omits
it, so `_ImportFailureCollector` below listens on that logger - but by
default a render with ANY import failure writes nothing and exits non-zero,
so a broken environment can't accidentally commit a partial file. Pass
`--allow-import-errors` to write anyway, with an explicit "failed to import"
section per unavailable pipe, for local debugging of the environment itself.
`--check` mirrors this asymmetry: a clean environment verifies the whole
file; an environment with import failures can't render (and so can't verify)
the sections for those pipes, so it reports a warning naming them and
verifies only the pipes it actually can render - it does not silently pass
everything, and a real drift in the pipes it CAN render is still an error.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from src.pipelines.catalog import PipeCatalog  # noqa: E402
from src.features.developer.pipes_documenter import PipesDocumenter  # noqa: E402
from src.features.developer.template_functions_documenter import (  # noqa: E402
    TemplateFunctionsDocumenter,
)
from src.features.presets.linter import _INJECTED_FORM_KEYS  # noqa: E402
from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE  # noqa: E402
from src.platform.plugins.field_types import FieldTypeRegistry  # noqa: E402
from src.features.fields.builtin import register_builtin_fields  # noqa: E402

PIPES_MD_PATH = REPO_ROOT / "docs" / "pipes.md"
CONTEXT_MD_PATH = REPO_ROOT / "docs" / "preset-context.md"

# `PresetProcessor.process` roots (src/features/presets/processor.py) that no
# shipped preset under content/presets/marketplace actually references today
# (a docraft-tracked separate card decides whether to remove them) - a manual
# audit finding, not something introspectable from the code, so it's kept as
# a short fixed annotation rather than derived.
_UNUSED_BY_SHIPPED_PRESETS = (
    "preset.speed_profiles", "preset.configuration",
    "generation.seed", "generation.quantity",
    "request.mode", "request.form_name",
    "path()", "icon()", "matches (filter)",
)


@dataclass
class CheckReport:
    """`check()`'s result: `errors` are drift/missing-file problems (fail the
    build); `warnings` are informational (e.g. an import-limited environment
    skipped some pipes) and never fail the build on their own."""
    errors: List[str] = dataclass_field(default_factory=list)
    warnings: List[str] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------- pipes

class _ImportFailureCollector(logging.Handler):
    """Captures `PipeCatalog`'s "Error loading pipe <module>: <error>" records."""

    _PREFIX = "Error loading pipe "

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.failures: Dict[str, str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message.startswith(self._PREFIX):
            return
        rest = message[len(self._PREFIX):]
        module_name, _, error = rest.partition(": ")
        name = _registry_name_from_module(module_name)
        self.failures[name] = " ".join(error.split())


def _registry_name_from_module(module_name: str) -> str:
    """Inverse of `PipeCatalog`'s own naming (see its module docstring):
    `pipes.<dir>` -> `<dir>`, `pipes.<dir>.<variant>` -> `<dir>/<variant>`."""
    parts = module_name.split(".")
    if parts and parts[0] == "pipes":
        parts = parts[1:]
    return "/".join(parts)


def discover_pipes(repo_root: Path) -> Tuple[PipeCatalog, Dict[str, str]]:
    """Discover every core/custom pipe - same construction as
    `tests/pipelines/test_pipe_default_config_is_json.py` (no plugin
    registry: the generated doc describes the shipped surface, not whatever
    happens to be enabled on the machine that regenerates it)."""
    collector = _ImportFailureCollector()
    catalog_logger = logging.getLogger("src.pipelines.catalog")
    catalog_logger.addHandler(collector)
    try:
        catalog = PipeCatalog(
            str(repo_root / "src" / "pipelines" / "pipes"),
            str(repo_root / "pipes" / "custom"),
        )
        catalog.discover_pipes()
    finally:
        catalog_logger.removeHandler(collector)
    return catalog, collector.failures


def _family_of(pipe_name: str) -> str:
    return pipe_name.split("/", 1)[0]


def _anchor(pipe_name: str) -> str:
    return "pipe-" + re.sub(r"[^a-z0-9]+", "-", pipe_name.lower()).strip("-")


def _display(path: Path) -> str:
    """`path`, relative to `REPO_ROOT` for a readable message when it is
    underneath it (the real case); the raw path otherwise (e.g. a test's
    `tmp_path`-relocated `PIPES_MD_PATH`, which `relative_to` can't express)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _md_cell(text: Any) -> str:
    return " ".join(str(text if text is not None else "").split()).replace("|", "\\|")


def _fmt_value(value: Any) -> str:
    import json
    if value is None:
        return "—"
    return f"`{_md_cell(json.dumps(value))}`"


def _bool(value: bool) -> str:
    return "yes" if value else "no"


def _inputs_table(inputs: List[Dict[str, Any]]) -> List[str]:
    if not inputs:
        return ["_No inputs._", ""]
    lines = ["**Inputs**", "", "| Name | IO Type | Required | Array | Description |", "|---|---|---|---|---|"]
    for spec in inputs:
        lines.append(
            f"| `{spec['name']}` | `{spec['io_type']}` | {_bool(spec['required'])} | "
            f"{_bool(spec['is_array'])} | {_md_cell(spec['description'])} |"
        )
    lines.append("")
    return lines


def _outputs_table(outputs: List[Dict[str, Any]]) -> List[str]:
    if not outputs:
        return ["**Outputs**: none.", ""]
    lines = ["**Outputs**", "", "| Name | IO Type | Array | Description |", "|---|---|---|---|"]
    for spec in outputs:
        lines.append(
            f"| `{spec['name']}` | `{spec['io_type']}` | {_bool(spec['is_array'])} | {_md_cell(spec['description'])} |"
        )
    lines.append("")
    return lines


def _configuration_table(configuration: List[Dict[str, Any]]) -> List[str]:
    if not configuration:
        return ["**Configuration**: none.", ""]
    lines = [
        "**Configuration**", "",
        "| Option | Type | Default | Required | Choices | Min | Max | Description |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for spec in configuration:
        choices = spec.get("choices")
        choices_cell = ", ".join(f"`{_md_cell(c)}`" for c in choices) if choices else "—"
        min_cell = spec["min_value"] if spec.get("min_value") is not None else "—"
        max_cell = spec["max_value"] if spec.get("max_value") is not None else "—"
        lines.append(
            f"| `{spec['name']}` | `{spec['param_type']}` | {_fmt_value(spec['default'])} | "
            f"{_bool(spec['required'])} | {choices_cell} | {min_cell} | {max_cell} | "
            f"{_md_cell(spec['description'])} |"
        )
    lines.append("")
    return lines


def _render_pipe_block(name: str, info: Dict[str, Any]) -> str:
    """One pipe's full subsection (heading + description + tables). Shared by
    the full render and the partial (import-limited) drift check, so both
    describe a given importable pipe identically."""
    lines = [f'### <a id="{_anchor(name)}"></a>`{name}`', ""]
    lines += [info["description"] or "_No description._", ""]
    lines += _inputs_table(info["inputs"])
    lines += _outputs_table(info["outputs"])
    lines += _configuration_table(info["configuration"])
    return "\n".join(lines).rstrip("\n")


def _render_import_error_block(name: str, error: str) -> str:
    return "\n".join([f'### <a id="{_anchor(name)}"></a>`{name}`', "", f"**Failed to import:** `{error}`"])


def render_pipes_md_from_catalog(catalog: PipeCatalog, import_errors: Dict[str, str]) -> str:
    """The rendering half of pipe-reference generation, taking an
    already-discovered catalog - split out so it can be exercised against a
    small fake catalog in tests without importing the whole repo pipe tree."""
    documenter = PipesDocumenter(catalog)
    by_name = {d["name"]: d for d in documenter.generate_documentation()["pipes"]}

    all_names = sorted(set(by_name) | set(import_errors))
    families: Dict[str, List[str]] = {}
    for name in all_names:
        families.setdefault(_family_of(name), []).append(name)

    lines: List[str] = [
        "---",
        "title: Pipes Reference",
        "category: Presets / Models",
        "category_order: 70",
        "order: 12",
        "---",
        "",
        "# Pipes Reference",
        "",
        "Generated by `scripts/pipes_reference.py` from the live pipe catalog "
        "(`src/pipelines/catalog.py`) — every pipe under `src/pipelines/pipes`, "
        "its `name:` key (as written in a `pipeline.yml` step), description, "
        "inputs, outputs, and configuration. Regenerate with "
        "`python scripts/pipes_reference.py`; `--check` (wired into "
        "`scripts/docs_lint.py`) fails the build when this file drifts from "
        "the code. See the [Preset Authoring Guide](presets.md) for how these "
        "are wired into a `pipeline.yml`, and Help → Documentation → Pipes in "
        "the running app for the same data against that install's actually "
        "enabled plugins.",
        "",
    ]

    if import_errors:
        lines += [
            f"**{len(import_errors)} pipe(s) failed to import in the environment that generated "
            "this page** (see `docs/testing-notes.md`) and are listed below with the import "
            "error in place of their inputs/outputs/configuration.",
            "",
        ]

    lines += ["## Families", "", "| Family | Pipes |", "|---|---|"]
    for family in sorted(families):
        links = ", ".join(f"[`{name}`](#{_anchor(name)})" for name in sorted(families[family]))
        lines.append(f"| `{family}` | {links} |")
    lines.append("")

    for family in sorted(families):
        lines += [f"## {family}", ""]
        for name in sorted(families[family]):
            if name in import_errors:
                lines.append(_render_import_error_block(name, import_errors[name]))
            else:
                lines.append(_render_pipe_block(name, by_name[name]))
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _check_pipes_md_partial(catalog: PipeCatalog, import_errors: Dict[str, str]) -> List[str]:
    """Drift check for an import-limited environment: verify only the pipes
    that DID import, by checking that each one's freshly-rendered block
    appears verbatim in the committed file - never renders (and so never
    judges) the pipes this environment can't import."""
    rel = _display(PIPES_MD_PATH)
    if not PIPES_MD_PATH.exists():
        return [f"{rel}: missing - run `python scripts/pipes_reference.py`"]

    committed = PIPES_MD_PATH.read_text(encoding="utf-8")
    documenter = PipesDocumenter(catalog)
    by_name = {d["name"]: d for d in documenter.generate_documentation()["pipes"]}

    problems: List[str] = []
    for name in sorted(by_name):
        if _render_pipe_block(name, by_name[name]) not in committed:
            problems.append(
                f"{rel}: pipe '{name}' section is out of date or missing - "
                "run `python scripts/pipes_reference.py`"
            )
    return problems


# ---------------------------------------------------------------- context.md

def _template_functions_sections() -> List[str]:
    doc = TemplateFunctionsDocumenter().generate_documentation()
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for entry in doc["functions"]:
        by_category.setdefault(entry["category"], []).append(entry)

    lines: List[str] = []
    for category in doc["categories"]:
        lines += [f"### {category}", ""]
        for entry in by_category[category]:
            alias = f" (alias `{entry['alias']}`)" if entry.get("alias") else ""
            lines += [f"**`{entry['name']}`**{alias} — `{entry['signature']}`", "", entry["description"], ""]
            for example in entry.get("examples") or []:
                lines.append(f"- `{example['code']}` → {example['result']}")
            if entry.get("examples"):
                lines.append("")
    return lines


def _shared_option_samples(repo_root: Path) -> List[Tuple[str, str]]:
    shared_root = repo_root / "content" / "presets" / "_shared"
    samples: List[Tuple[str, str]] = []
    for path in sorted(shared_root.rglob("*.yml")):
        rel = path.relative_to(repo_root).as_posix()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except Exception as e:  # noqa: BLE001 - reported inline, not raised
            samples.append((rel, f"<failed to parse: {e}>"))
            continue
        if not isinstance(data, list):
            samples.append((rel, f"<not a list: {type(data).__name__}>"))
            continue
        preview = "; ".join(_md_cell(item) for item in data[:3])
        samples.append((rel, preview or "_empty_"))
    return samples


def render_context_md(repo_root: Path) -> str:
    registry = FieldTypeRegistry()
    register_builtin_fields(registry)
    field_type_names = sorted(d.type_name for d in registry.all())

    lines: List[str] = [
        "---",
        "title: Preset Context Cheat Sheet",
        "category: Presets / Models",
        "category_order: 70",
        "order: 13",
        "---",
        "",
        "# Preset Context Cheat Sheet",
        "",
        "Generated by `scripts/pipes_reference.py` from the live templating/preset "
        "surface. Regenerate with `python scripts/pipes_reference.py`; `--check` "
        "(wired into `scripts/docs_lint.py`) fails the build when this file drifts "
        "from the code. See the [Preset Authoring Guide](presets.md#template-contexts) "
        "for the narrative version of this reference.",
        "",
        "## Globals, filters and template context roots",
        "",
        "The Jinja globals/filters below are registered in "
        "`src/platform/templating/processor.py`; the `Template Context` entries are "
        "the `pipeline.yml` context roots built by `src/features/presets/processor.py`. "
        "Descriptions and signatures come straight from "
        "`src.features.developer.template_functions_documenter.TemplateFunctionsDocumenter` "
        "- the same source Help → Documentation → Template Functions serves.",
        "",
    ]
    lines += _template_functions_sections()

    lines += [
        "**Present, unused by any shipped preset today** (a separate card decides "
        "whether to remove them - kept documented rather than silently dropped): "
        + ", ".join(f"`{item}`" for item in _UNUSED_BY_SHIPPED_PRESETS) + ".",
        "",
        "## `@config:<key>` indirection",
        "",
        "Not a Jinja construct: a bare string-prefix match (`str.startswith('@config:')`) "
        "resolved in `src/features/presets/configuration.py`'s `resolve_filter_tags`/"
        "`resolve_field_filter_tags` at form-schema time, against the preset's admin-set "
        "`configuration:` values (see \"Configuration (admin-set)\" in the Preset Authoring "
        "Guide). A key with no `@config:` prefix, or a value the preset's stored "
        "configuration doesn't have, both resolve to \"no filtering\" - a typo in the key "
        "falls through silently rather than erroring.",
        "",
        "## Injected form keys",
        "",
        "Runtime-injected documents that arrive as ordinary keys of the `form` context "
        "root without being declared as form fields (`src/features/presets/linter.py`'s "
        "`_INJECTED_FORM_KEYS` - the preset linter never flags a `{{ form.<name> }}` "
        "reference to one of these as an undeclared field):",
        "",
        ", ".join(f"`{key}`" for key in sorted(_INJECTED_FORM_KEYS)),
        "",
        "## `model_type` values",
        "",
        f"The {len(DIRECTORY_TO_MODEL_TYPE)} model types a `model`/`lora_picker` field's "
        "`configuration.model_type` may select, and the depot directory each maps to "
        "(`src/platform/filesystem/model_types.py`):",
        "",
        "| `model_type` | Directory |",
        "|---|---|",
    ]
    for directory, model_type in sorted(DIRECTORY_TO_MODEL_TYPE.items(), key=lambda kv: kv[1]):
        lines.append(f"| `{model_type}` | `{directory}/` |")
    lines.append("")

    lines += [
        "## Built-in field types",
        "",
        f"The {len(field_type_names)} core field type names registered by "
        "`src/features/fields/builtin.py` (the live, authoritative list is served at "
        "`GET /api/fields/types`; a plugin can add more via its manifest's `field_types:` "
        "section):",
        "",
        ", ".join(f"`{name}`" for name in field_type_names),
        "",
        "## `content/presets/_shared/**` option files",
        "",
        "Shared option-file vocabulary referenced from any preset via `{{ paths._shared }}` "
        "(see \"External option files\" in the Preset Authoring Guide). First three entries "
        "of each file:",
        "",
        "| File | First entries |",
        "|---|---|",
    ]
    for rel, preview in _shared_option_samples(repo_root):
        lines.append(f"| `{rel}` | {preview} |")
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# -------------------------------------------------------------------- driver

def check(repo_root: Path = REPO_ROOT) -> CheckReport:
    """Compare the committed docs against a fresh render.

    `docs/preset-context.md` never depends on which pipes import, so it is
    always compared byte-for-byte. `docs/pipes.md` is compared byte-for-byte
    too UNLESS this environment fails to import some pipes, in which case a
    full compare would spuriously fail on pipes this box simply can't
    render - the check then verifies only the pipes that DID import (a real
    drift among those is still an error) and reports the rest as a warning,
    never silently passing the whole file.
    """
    report = CheckReport()

    context_rel = _display(CONTEXT_MD_PATH)
    fresh_context = render_context_md(repo_root)
    if not CONTEXT_MD_PATH.exists():
        report.errors.append(f"{context_rel}: missing - run `python scripts/pipes_reference.py`")
    elif CONTEXT_MD_PATH.read_text(encoding="utf-8") != fresh_context:
        report.errors.append(f"{context_rel}: out of date - run `python scripts/pipes_reference.py`")

    catalog, import_errors = discover_pipes(repo_root)
    pipes_rel = _display(PIPES_MD_PATH)

    if not import_errors:
        fresh_pipes = render_pipes_md_from_catalog(catalog, {})
        if not PIPES_MD_PATH.exists():
            report.errors.append(f"{pipes_rel}: missing - run `python scripts/pipes_reference.py`")
        elif PIPES_MD_PATH.read_text(encoding="utf-8") != fresh_pipes:
            report.errors.append(f"{pipes_rel}: out of date - run `python scripts/pipes_reference.py`")
        return report

    report.warnings.append(
        f"drift check skipped for {len(import_errors)} pipe(s) that failed to import here "
        f"({', '.join(sorted(import_errors))}) - see docs/testing-notes.md"
    )
    report.errors.extend(_check_pipes_md_partial(catalog, import_errors))
    return report


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="Exit non-zero if the committed files are out of date.")
    ap.add_argument(
        "--allow-import-errors", action="store_true",
        help=(
            "Write docs/pipes.md even if some pipes fail to import in this environment, "
            "with an explicit \"failed to import\" section for each. Without this flag, "
            "any import failure writes nothing and exits non-zero."
        ),
    )
    args = ap.parse_args(argv)

    if args.check:
        report = check(REPO_ROOT)
        for w in report.warnings:
            print(f"WARN {w}")
        for e in report.errors:
            print(f"ERROR {e}")
        if report.errors:
            return 1
        suffix = " (partial - see warnings above)" if report.warnings else ""
        print(f"docs/pipes.md and docs/preset-context.md are up to date.{suffix}")
        return 0

    catalog, import_errors = discover_pipes(REPO_ROOT)
    if import_errors and not args.allow_import_errors:
        for name, error in sorted(import_errors.items()):
            print(f"ERROR {name}: failed to import: {error}")
        print(
            f"\n{len(import_errors)} pipe(s) failed to import in this environment - wrote "
            "nothing (see docs/testing-notes.md). Fix the environment and rerun, or pass "
            "--allow-import-errors to write docs/pipes.md with an explicit \"failed to "
            "import\" section for each of them."
        )
        return 1

    pipes_md = render_pipes_md_from_catalog(catalog, import_errors)
    context_md = render_context_md(REPO_ROOT)
    PIPES_MD_PATH.write_text(pipes_md, encoding="utf-8")
    CONTEXT_MD_PATH.write_text(context_md, encoding="utf-8")
    print(f"wrote {_display(PIPES_MD_PATH)}")
    print(f"wrote {_display(CONTEXT_MD_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
