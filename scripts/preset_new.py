#!/usr/bin/env python3
"""
Preset scaffolder.

Emits a minimal, schema-valid preset skeleton that passes `scripts/preset_lint.py`.

Usage:
    python scripts/preset_new.py <Model>/<variant> [options]

Example:
    python scripts/preset_new.py MyModel/standard --category image --modes txt2img
    python scripts/preset_new.py MyModel/official --engine comfyui --category video --modes txt2vid,img2vid
    python scripts/preset_new.py MyZImage/standard --family z_image

The generated directory has no engine segment - nothing parses one; the
authoritative value is `engine:` in preset.yml (see --engine below).

Options:
    --category   One of: image | video | audio | 3d | utility   (default: image)
    --modes      Comma-separated list of mode names          (default: txt2img)
    --engine     Engine value written into preset.yml         (default: native)
    --family     A native pipe family (e.g. z_image, flux, krea2) to scaffold the
                 standard native chain for, wired to that family's real
                 model_loader/<family> and generator/<family> pipes - discovered
                 from the pipe catalog's light-scan tier, never hardcoded. See
                 "Discovering families" below. Ignored when --engine is comfyui.
    --name       Human-readable display name (default: "<Model> <variant>")
    --root       Presets root directory (default: content/presets/marketplace; use
                 content/presets/local for a user-owned preset that isn't shipped)
    --force      Overwrite an existing preset directory

The generated layout (see docs/presets/tutorial.md and docs/pipes.md for the full
authoring reference):

    <root>/<Model>/<variant>/
    ├── preset.yml
    └── modes/<mode>/
        ├── pipeline.yml
        ├── form.yml
        └── tabs/main.yml            # or tabs/{generation,lora,advanced}.yml with --family

Discovering families:
    A "family" is a native pipe family whose loader is `model_loader/<family>`. The
    scaffolder never hardcodes a family list - it light-scans
    `src/pipelines/pipes/model_loader/` (a directory walk, no imports - the same
    tier `PipeCatalog._do_light_scan` uses) for the requested name, then finds its
    matching `generator/<name-or-something-containing-name>` pipe the same way,
    then imports ONLY those two pipe modules to read their real
    `PipeConfigSpec`/`PipeOutputSpec` lists (steps/guidance-or-cfg/sampler/
    resolution defaults, the model-loader's required dict-typed config keys, and
    whether it accepts `loras`) - never guessed or hand-maintained.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.presets.schema import CATEGORIES, validate_manifest  # noqa: E402
from src.platform.util.ids import generate_ulid  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_PIPES_DIR = REPO_ROOT / "src" / "pipelines" / "pipes"


def _preset_yml(preset_id: str, name: str, category: str, engine: str, modes: List[str]) -> str:

    modes_yaml = "\n".join(f"  - {m}" for m in modes)
    return f"""########
## {name} preset configuration
## Authoring reference: docs/presets/tutorial.md
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
# docs/presets/pipelines.md): form.<field>, request.mode, request.form_name,
# generation.prompts.*, generation.profile, preset.id, preset.name, preset.vars,
# runtime.settings.*, paths.preset, plus the globals path(), icon() and
# get_speed_profile().
"""

    if engine == "comfyui":
        return header + f"""#
# Export the workflow as API-format JSON into files/workflows/, drop it next to
# this file, and map its node inputs via `field_mappings`
# ([source_template, "node_id.inputs.field", type]). See docs/presets/comfyui.md
# and content/plugins/marketplace/comfyui-backend for worked examples.
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
"""

    return header + """#
# Replace the placeholder below with the real pipes for this model
# (e.g. checkpoint_loader -> prompt_encoder -> seed_generator -> generator -> gallery).
pipeline:
  - name: "gallery"
    id: "gallery"
    enabled: true
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


# --------------------------------------------------------------- --family


@dataclass
class _FamilyPipes:
    family: str
    loader_name: str          # e.g. "model_loader/z_image"
    generator_name: str       # e.g. "generator/z_image"
    model_slots: List[str]    # loader's required dict-typed config keys, in declared order
    has_loras: bool           # loader accepts a `loras` list config
    has_text_encoder_output: bool  # loader outputs `text_encoder` (wire into prompt_encoder)
    steps_default: int
    guidance_key: str         # "guidance" or "cfg" - whichever the generator actually declares
    guidance_default: float
    sampler_default: str
    sampler_choices: List[str]
    resolution_default: str


def _light_scan_pipe_names(subdir: str) -> List[str]:
    """Pipe registry keys under `src/pipelines/pipes/<subdir>/*/main.py`, without
    importing anything - the same convention `PipeCatalog._do_light_scan` uses."""
    d = CORE_PIPES_DIR / subdir
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "main.py").exists())


def discover_family(name: str) -> Tuple[str, str]:
    """Resolve `--family <name>` to (loader_pipe_name, generator_pipe_name) by
    light-scanning `model_loader/` and `generator/` - never a hardcoded family list.

    A family is one that has `model_loader/<name>`. Its generator is
    `generator/<name>` when that exact pipe exists, otherwise the
    alphabetically-first `generator/<X>` whose name contains `<name>` (every
    shipped video/audio family's generator is named this way, e.g.
    model_loader/wan22 -> generator/txt2vid_wan22).
    """
    loaders = _light_scan_pipe_names("model_loader")
    if name not in loaders:
        available = ", ".join(loaders) or "(none found)"
        raise ValueError(
            f"no model_loader/{name} pipe found. Available families "
            f"(light-scanned from src/pipelines/pipes/model_loader/): {available}"
        )

    generators = _light_scan_pipe_names("generator")
    if name in generators:
        generator = name
    else:
        candidates = sorted(g for g in generators if name in g)
        if not candidates:
            raise ValueError(
                f"model_loader/{name} exists but no generator/<name> or "
                f"generator/*{name}* pipe was found - wire the generator by hand "
                f"(see docs/presets/pipelines.md#the-standard-native-chain)."
            )
        generator = candidates[0]

    return f"model_loader/{name}", f"generator/{generator}"


def _load_family_pipes(family: str) -> _FamilyPipes:
    loader_name, generator_name = discover_family(family)

    # Import only these two resolved pipe modules (PipeCatalog's light scan
    # already told us exactly where they live) to read their real specs.
    from src.pipelines.catalog import PipeCatalog

    catalog = PipeCatalog(str(CORE_PIPES_DIR), str(REPO_ROOT / "pipes" / "custom"))
    loader_cls = catalog.get_pipe(loader_name)
    generator_cls = catalog.get_pipe(generator_name)
    if loader_cls is None or generator_cls is None:
        raise ValueError(f"failed to import {loader_name!r} / {generator_name!r} for introspection")

    loader_config = loader_cls.configuration()
    model_slots = [c.name for c in loader_config if c.required and c.param_type is dict]
    has_loras = any(c.name == "loras" for c in loader_config)
    has_text_encoder_output = any(o.name == "text_encoder" for o in loader_cls.outputs())

    gen_config: Dict[str, object] = {c.name: c for c in generator_cls.configuration()}
    steps_spec = gen_config.get("steps")
    guidance_key = "guidance" if "guidance" in gen_config else ("cfg" if "cfg" in gen_config else "guidance")
    guidance_spec = gen_config.get(guidance_key)
    sampler_spec = gen_config.get("sampler")
    resolution_spec = gen_config.get("resolution")

    return _FamilyPipes(
        family=family,
        loader_name=loader_name,
        generator_name=generator_name,
        model_slots=model_slots,
        has_loras=has_loras,
        has_text_encoder_output=has_text_encoder_output,
        steps_default=int(steps_spec.default) if steps_spec and steps_spec.default is not None else 20,
        guidance_key=guidance_key,
        guidance_default=float(guidance_spec.default) if guidance_spec and guidance_spec.default is not None else 4.0,
        sampler_default=str(sampler_spec.default) if sampler_spec and sampler_spec.default else "euler",
        sampler_choices=list(sampler_spec.choices) if sampler_spec and sampler_spec.choices else ["euler"],
        resolution_default=str(resolution_spec.default) if resolution_spec and resolution_spec.default else "1024x1024",
    )


def _family_pipeline_yml(mode: str, fp: _FamilyPipes) -> str:
    loras_config = ""
    if fp.has_loras:
        loras_config = """
      loras:
        "@loop":
          items: "{{ form.loras }}"
          template:
            file_path: "{{ item.model }}"
            weight: "{{ item.strength }}\""""

    slot_lines = "\n".join(
        f'      {slot}: {{ file_path: "{{{{ form.{slot} }}}}", name: "{{{{ form.{slot} }}}}" }}'
        for slot in fp.model_slots
    )

    te_input = (
        f'\n    - ["text_encoder", "{fp.loader_name}", "text_encoder"]'
        if fp.has_text_encoder_output else ""
    )

    param_lines = "\n".join(f'        - ["model", "{{{{ form.{slot} }}}}"]' for slot in fp.model_slots)

    return f"""# Pipeline for the '{mode}' mode - the standard native chain (see
# docs/presets/pipelines.md#the-standard-native-chain), wired to the real
# {fp.loader_name} / {fp.generator_name} pipes discovered from the pipe catalog.
#
# model_loader/{fp.family} -> prompt_encoder -> seed_generator -> dynamic_prompts_renderer
#   -> from_iotype -> param_emitter -> generator/{fp.family} -> gallery
pipeline:
  - name: "{fp.loader_name}"
    enabled: true
    configuration:
{slot_lines}{loras_config}

  - name: "prompt_encoder"
    input:{te_input}
    enabled: true
    configuration:
      p_prompt:
        input: "{{{{ generation.prompts.first.positive }}}}"
        output: "{{{{ generation.prompts.first.positive }}}}"
      n_prompt:
        input: "{{{{ generation.prompts.first.negative }}}}"
        output: "{{{{ generation.prompts.first.negative }}}}"
      pairs: "{{{{ generation.prompts.pairs }}}}"
      quantity: "{{{{ form.quantity }}}}"
      guidance_scale: "{{{{ form.{fp.guidance_key} }}}}"

  - name: "seed_generator"
    enabled: true
    configuration:
      seed: "{{{{ form.seed }}}}"
      quantity: "{{{{ form.quantity }}}}"

  - name: "dynamic_prompts_renderer"
    id: "dynamic_prompts_renderer"
    enabled: true
    configuration:
      pairs: "{{{{ generation.prompts.pairs }}}}"
      quantity: "{{{{ form.quantity }}}}"

  - name: "from_iotype"
    id: "from_iotype"
    enabled: true
    input:
      - ["seed", "seed_generator", "seed"]
    configuration:
      from: "seed"

  - name: "param_emitter"
    id: "param_emitter"
    enabled: true
    input:
      - ["seed", "from_iotype", "seed"]
    configuration:
      quantity: "{{{{ form.quantity }}}}"
      parameters:
{param_lines}
        - ["positive_prompt", "{{{{ generation.prompts.positives }}}}"]
        - ["negative_prompt", "{{{{ generation.prompts.negatives }}}}"]
        - ["{fp.guidance_key}", "{{{{ form.{fp.guidance_key} }}}}"]
        - ["steps", "{{{{ form.steps }}}}"]
        - ["sampler", "{{{{ form.sampler }}}}"]
        - ["resolution", "{{{{ form.resolution }}}}"]

  - name: "{fp.generator_name}"
    input:
      - ["model", "{fp.loader_name}", "model"]
      - ["conditioning", "prompt_encoder", "conditioning"]
      - ["seed", "seed_generator", "seed"]
    enabled: true
    configuration:
      steps: "{{{{ form.steps }}}}"
      {fp.guidance_key}: "{{{{ form.{fp.guidance_key} }}}}"
      sampler: "{{{{ form.sampler }}}}"
      resolution: "{{{{ form.resolution }}}}"
      quantity: "{{{{ form.quantity }}}}"

  - name: "gallery"
    input:
      - ["image", "{fp.generator_name}", "image"]
      - ["seed", "seed_generator", "seed"]
    enabled: true
"""


def _family_form_yml(mode: str, fp: _FamilyPipes) -> str:
    tabs = ['''      - type: "tab"
        label: "Generation"
        children: "{{ paths.preset }}/modes/''' + mode + '''/tabs/generation.yml"''']
    if fp.has_loras:
        tabs.append('''      - type: "tab"
        label: "LoRA"
        children: "{{ paths.preset }}/modes/''' + mode + '''/tabs/lora.yml"''')
    tabs.append('''      - type: "tab"
        label: "Advanced"
        audience: "advanced"
        children: "{{ paths.preset }}/modes/''' + mode + '''/tabs/advanced.yml"''')

    tabs_yaml = "\n".join(tabs)
    return f'''name: "custom"
fields:
  - type: "tabs"
    children:
{tabs_yaml}
'''


def _family_generation_tab_yml(fp: _FamilyPipes) -> str:
    model_fields = "\n\n".join(
        f'''  - name: "{slot}"
    type: "model"
    label: "{slot.replace('_', ' ').title()}"
    required: true
    configuration:
      model_type: "{slot}"
      placeholder: "Select a {fp.family} {slot.replace('_', ' ')}..."'''
        for slot in fp.model_slots
    )

    return f'''fields:
  - type: "row"
    children:
      - name: "seed"
        type: "seed"
        label: "Seed"
        default: -1
        width: "3/5"
      - name: "quantity"
        type: "stepper"
        label: "Quantity"
        default: 1
        configuration: {{min: 1, max: 10, step: 1}}
        width: "2/5"

  - name: "resolution"
    type: "resolution"
    label: "Resolution"
    configuration:
      files:
        - {{ path: "{{{{ paths._shared }}}}/resolutions/sdxl.yml", group: "Standard" }}
    default: "{fp.resolution_default}"

{model_fields}
'''


def _family_lora_tab_yml() -> str:
    return '''fields:
  - name: "loras"
    type: "lora_picker"
    default: []
    label: "LoRAs"
    configuration:
      model_type: "lora"
      max_items: 6
'''


def _family_advanced_tab_yml(fp: _FamilyPipes) -> str:
    choices_yaml = "\n".join(
        f'        - {{ label: "{c.title()}", value: "{c}" }}' for c in fp.sampler_choices
    )
    return f'''fields:
  - name: "steps"
    type: "slider"
    label: "Steps"
    default: {fp.steps_default}
    configuration: {{min: 1, max: 150, step: 1}}

  - name: "sampler"
    type: "select"
    label: "Sampler"
    default: "{fp.sampler_default}"
    configuration:
      options:
{choices_yaml}

  - name: "{fp.guidance_key}"
    type: "slider"
    label: "{fp.guidance_key.upper() if fp.guidance_key == "cfg" else fp.guidance_key.title()}"
    default: {fp.guidance_default}
    configuration: {{min: 0, max: 30, step: 0.1}}
'''


def scaffold(target: Path, preset_id: str, name: str, category: str,
             engine: str, modes: List[str], force: bool,
             family: Optional[str] = None) -> List[Path]:
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")

    written: List[Path] = []

    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        written.append(path)

    write(target / "preset.yml", _preset_yml(preset_id, name, category, engine, modes))

    fp = _load_family_pipes(family) if (family and engine != "comfyui") else None

    for mode in modes:
        mode_dir = target / "modes" / mode
        if fp is not None:
            write(mode_dir / "pipeline.yml", _family_pipeline_yml(mode, fp))
            write(mode_dir / "form.yml", _family_form_yml(mode, fp))
            write(mode_dir / "tabs" / "generation.yml", _family_generation_tab_yml(fp))
            if fp.has_loras:
                write(mode_dir / "tabs" / "lora.yml", _family_lora_tab_yml())
            write(mode_dir / "tabs" / "advanced.yml", _family_advanced_tab_yml(fp))
        else:
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
    parser.add_argument("--family", default=None,
                         help="Native pipe family to scaffold the standard chain for (e.g. z_image, flux, krea2)")
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

    if args.family and engine == "comfyui":
        parser.error("--family scaffolds the native chain; it has no effect with --engine comfyui")

    if args.family:
        try:
            discover_family(args.family)
        except ValueError as e:
            print(f"Refusing to scaffold - {e}", file=sys.stderr)
            return 1

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
        written = scaffold(target, preset_id, name, args.category, engine, modes, args.force, family=args.family)
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Refusing to scaffold - {e}", file=sys.stderr)
        return 1

    print(f"Scaffolded preset '{preset_id}' at {target}")
    for path in written:
        print(f"  created {path}")
    print("\nNext steps:")
    if args.family:
        print(f"  1. Fill in the model pickers and check the {args.family} generator's config in docs/pipes.md.")
    else:
        print(f"  1. Fill in modes/<mode>/pipeline.yml with the real pipes for this model (see docs/presets/tutorial.md).")
    print(f"  2. Add/adjust form fields in modes/<mode>/form.yml (+ tabs/).")
    print(f"  3. Validate:  python scripts/preset_lint.py {target}")
    print(f"  4. Render:    python scripts/preset_render.py {target} {modes[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
