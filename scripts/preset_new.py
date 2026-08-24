#!/usr/bin/env python3
"""
Preset scaffolder.

Emits a minimal, schema-valid preset skeleton that passes `scripts/preset_lint.py`.

Usage:
    python scripts/preset_new.py <Model>/<variant> [options]

Example:
    python scripts/preset_new.py MyModel/standard --category image --modes txt2img
    python scripts/preset_new.py MyModel/official --engine comfyui --category video --modes txt2vid,img2vid

The generated directory has no engine segment - nothing parses one; the
authoritative value is `engine:` in preset.yml (see --engine below).

Options:
    --category   One of: image | video | audio | utility   (default: image)
    --modes      Comma-separated list of mode names          (default: txt2img)
    --engine     Engine value written into preset.yml         (default: native)
    --name       Human-readable display name (default: "<Model> <variant>")
    --root       Presets root directory (default: content/presets/marketplace; use
                 content/presets/local for a user-owned preset that isn't shipped)
    --force      Overwrite an existing preset directory

The generated layout (see docs/presets.md for the full authoring reference):

    <root>/<Model>/<variant>/
    ├── preset.yml
    └── modes/<mode>/
        ├── pipeline.yml
        ├── form.yml
        └── tabs/main.yml
"""

import argparse
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.presets.schema import CATEGORIES, validate_manifest  # noqa: E402
from src.platform.util.ids import generate_ulid  # noqa: E402


def _preset_yml(preset_id: str, name: str, category: str, engine: str, modes: List[str]) -> str:

    modes_yaml = "\n".join(f"  - {m}" for m in modes)
    return f"""########
## {name} preset configuration
## Authoring reference: docs/presets.md
########
schema: 1
id: "{preset_id}"
name: "{name}"
category: "{category}"
version: "1.0.0"
engine: "{engine}"
tags: []

# Preset-wide constants. Reference them in pipeline.yml as {{{{ preset.vars.<name> }}}}.
vars:
  default_steps: 30

modes:
{modes_yaml}
"""


def _pipeline_yml(mode: str, engine: str) -> str:
    header = f"""# Pipeline for the '{mode}' mode.
#
# `pipeline` is an ordered list of pipes. Each pipe supports:
#   name          (required) the registered pipe name, e.g. "generator/sdxl"
#   id            (optional) a stable id other pipes reference in their `input`
#   enabled       a real YAML bool, or an exact "{{{{ expression }}}}" that evaluates
#                 to a bool - omitted means enabled
#   input         list of [name, provider_pipe_id, provider_output_var]
#   configuration pipe config; values may be Jinja2 templates rendered at run time
#
# Jinja context available here (pipeline.yml only - see "Template contexts" in
# docs/presets.md): form.<field>, request.mode, request.form_name,
# generation.prompts.*, generation.seed, generation.quantity, preset.id,
# preset.name, preset.vars, preset.speed_profiles, preset.configuration,
# runtime.settings.*, paths.preset, plus the globals path(), icon() and
# get_speed_profile().
"""

    if engine == "comfyui":
        return header + f"""#
# Export the workflow as API-format JSON into files/workflows/, drop it next to
# this file, and map its node inputs via `field_mappings`
# ([source_template, "node_id.inputs.field", type]). See docs/presets.md and
# content/plugins/marketplace/comfyui-backend for worked examples.
pipeline:
  - name: "comfyui"
    id: "comfyui"
    enabled: true
    configuration:
      host: "127.0.0.1"
      port: 8188
      workflow_file: "{{{{ paths.preset }}}}/modes/{mode}/files/workflows/{mode}.json"
      field_mappings: []
      timeout: 300

  - name: "gallery"
    id: "gallery"
    enabled: true
    input:
      - ["image", "comfyui", "image"]
    configuration:
      mode: "save"
"""

    return header + """#
# Replace the placeholder below with the real pipes for this model
# (e.g. checkpoint_loader -> prompt_encoder -> seed_generator -> generator -> gallery).
pipeline:
  - name: "gallery"
    id: "gallery"
    enabled: true
    configuration:
      mode: "save"
"""


def _form_yml(mode: str) -> str:
    return f'''name: "default"
fields:
  - type: "tabs"
    children:
      - type: "tab"
        label: "Main"
        children: "{{{{ paths.preset }}}}/modes/{mode}/tabs/main.yml"
'''


def _tab_yml() -> str:
    return """fields:
  - name: "seed"
    type: "seed"
    label: "Seed"
    default: -1

  - name: "steps"
    type: "slider"
    label: "Steps"
    configuration:
      min: 1
      max: 100
      step: 1
    default: 30
"""


def scaffold(target: Path, preset_id: str, name: str, category: str,
             engine: str, modes: List[str], force: bool) -> List[Path]:
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")

    written: List[Path] = []

    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        written.append(path)

    write(target / "preset.yml", _preset_yml(preset_id, name, category, engine, modes))
    for mode in modes:
        mode_dir = target / "modes" / mode
        write(mode_dir / "pipeline.yml", _pipeline_yml(mode, engine))
        write(mode_dir / "form.yml", _form_yml(mode))
        write(mode_dir / "tabs" / "main.yml", _tab_yml())

    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="<Model>/<variant>, e.g. MyModel/standard")
    parser.add_argument("--category", default="image", choices=CATEGORIES)
    parser.add_argument("--modes", default="txt2img", help="Comma-separated mode names")
    parser.add_argument("--engine", default="native", help="Engine value written into preset.yml (default: native)")
    parser.add_argument("--name", default=None, help="Display name (default: '<Model> <variant>')")
    parser.add_argument("--root", default="content/presets/marketplace", help="Presets root directory")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing preset directory")
    args = parser.parse_args()

    parts = [p for p in args.path.strip("/").split("/") if p]
    if len(parts) != 2:
        parser.error("path must be exactly <Model>/<variant>")
    model, variant = parts

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes:
        parser.error("--modes must contain at least one mode name")

    engine = args.engine.strip()
    name = args.name or f"{model} {variant}"
    preset_id = generate_ulid()

    # Validate the manifest we are about to write before touching disk.
    manifest_data = {
        "schema": 1,
        "id": preset_id,
        "name": name,
        "category": args.category,
        "version": "1.0.0",
        "engine": engine,
        "tags": [],
        "vars": {"default_steps": 30},
        "modes": modes,
    }
    _, errors = validate_manifest(manifest_data)
    if errors:
        print("Refusing to scaffold - manifest would be invalid:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    target = Path(args.root) / model / variant
    try:
        written = scaffold(target, preset_id, name, args.category, engine, modes, args.force)
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Scaffolded preset '{preset_id}' at {target}")
    for path in written:
        print(f"  created {path}")
    print("\nNext steps:")
    print(f"  1. Fill in modes/<mode>/pipeline.yml with the real pipes for this model.")
    print(f"  2. Add form fields in modes/<mode>/form.yml (+ tabs/).")
    print(f"  3. Validate:  python scripts/preset_lint.py {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
