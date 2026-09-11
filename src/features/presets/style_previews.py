"""Pure helpers for rendering a preset's `styles.yml` previews.

Rendering itself is a script (`scripts/preset_styles_render.py`), not an app
endpoint - it drives a real generation through the same headless orchestrator
client `scripts/preset_test_suite.py` uses. This module holds only the parts
that don't need a running generation: prompt assembly, the downscale + WebP
encode, and the `styles.yml` `preview:` field fill.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image

from src.features.presets.templates import default_form_name

STYLE_PREVIEW_LONG_EDGE_DEFAULT = 320
STYLE_PREVIEW_SEED_DEFAULT = 1
STYLE_PREVIEW_WEBP_QUALITY = 80
# The field name presets use for their output-count control (see e.g.
# content/presets/marketplace/Anima/modes/txt2img/tabs/generation.yml) -
# forced to 1 so "take the first output image" is well-defined.
STYLE_PREVIEW_QUANTITY_FIELD = "quantity"


def build_style_prompt(style: Dict[str, Any], defaults: Dict[str, str]) -> Tuple[str, str]:
    """`(prompt, negative)` for one style.

    `prompt` = `defaults["prompt_prefix"]` + `prepend` + the resolved example
    prompt + `append`, where the resolved example prompt is the style's own
    `example_prompt` when non-empty, else `defaults["example_prompt"]` - so
    every style renders the same scene (only the style differs) unless it
    declares its own. `negative` joins the non-empty parts of
    `[defaults["negative"], style["negative"]]` with `", "` - the preset's
    own recommended negative (e.g. Anima's quality-score exclusions) first,
    the style's own negative after. `defaults` is a preset's
    `styles.yml` `preview:` block (`PresetTemplate.styles_preview`); pass
    `{"prompt_prefix": "", "negative": "", "example_prompt": ""}` for none.
    """
    example_prompt = style.get("example_prompt") or defaults.get("example_prompt", "")
    prompt = f"{defaults.get('prompt_prefix', '')}{style.get('prepend', '')}{example_prompt}{style.get('append', '')}"
    parts = [defaults.get("negative") or "", style.get("negative") or ""]
    negative = ", ".join(p for p in parts if p)
    return prompt, negative


def preview_rel_path(style_id: str) -> str:
    return f"public/styles/{style_id}.webp"


def form_has_field(preset, mode: str, field_name: str) -> bool:
    mode_template = preset.modes.get(mode)
    if mode_template is None or not mode_template.forms:
        return False
    form_name = default_form_name(mode_template)
    form = next((f for f in mode_template.forms if f.name == form_name), mode_template.forms[0])
    return _fields_contain(form.fields, field_name)


def _fields_contain(fields, name: str) -> bool:
    for field in fields or []:
        if field.name == name:
            return True
        if isinstance(field.children, list) and _fields_contain(field.children, name):
            return True
    return False


def downscale_and_save_webp(img: Image.Image, dest_path: Path, long_edge: int) -> None:
    img = img.convert("RGB")
    width, height = img.size
    scale = long_edge / max(width, height)
    if scale < 1:
        img = img.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_path, format="WEBP", quality=STYLE_PREVIEW_WEBP_QUALITY)


def write_webp(source_path: Path, dest_path: Path, long_edge: int) -> None:
    with Image.open(source_path) as img:
        downscale_and_save_webp(img, dest_path, long_edge)


_STYLE_ENTRY_RE_TEMPLATE = r'^(\s*)-\s*id:\s*["\']?{}["\']?\s*$'
_STYLE_ENTRY_START_RE = re.compile(r'^\s*-\s*id:\s*')
_PREVIEW_LINE_RE = re.compile(r'^(\s*)preview:\s*.*$')


def set_style_preview(styles_path: Path, style_id: str, preview_path: str) -> None:
    """Round-trip `styles.yml` to set one style's `preview:` field in place.

    A targeted text edit rather than a full YAML dump/reformat - this repo
    has no `ruamel.yaml` dependency for a real comment/order-preserving
    round-trip, so every other entry's formatting is left untouched by
    editing only the lines belonging to `style_id`'s block.
    """
    lines = styles_path.read_text().split("\n")

    entry_re = re.compile(_STYLE_ENTRY_RE_TEMPLATE.format(re.escape(style_id)))
    start = next((i for i, line in enumerate(lines) if entry_re.match(line)), None)
    if start is None:
        raise ValueError(f"style id '{style_id}' not found in {styles_path}")

    end = start + 1
    while end < len(lines) and not _STYLE_ENTRY_START_RE.match(lines[end]):
        end += 1

    for i in range(start + 1, end):
        match = _PREVIEW_LINE_RE.match(lines[i])
        if match:
            lines[i] = f'{match.group(1)}preview: "{preview_path}"'
            styles_path.write_text("\n".join(lines))
            return

    indent_match = re.match(r'^(\s*)-\s*', lines[start])
    dash_indent = indent_match.group(1) if indent_match else ""
    key_indent = dash_indent + "  "
    lines.insert(end, f'{key_indent}preview: "{preview_path}"')
    styles_path.write_text("\n".join(lines))


def select_styles(styles: List[Dict[str, Any]], style_ids: List[str] = None) -> List[Dict[str, Any]]:
    """`styles` filtered to `style_ids` (all of them when omitted).

    Raises ValueError for an empty `styles` list or an unknown id in
    `style_ids` - both are request errors, not per-style failures.
    """
    styles_by_id = {style["id"]: style for style in styles}
    if not styles_by_id:
        raise ValueError("preset has no styles.yml")

    if not style_ids:
        return list(styles_by_id.values())

    unknown = [sid for sid in style_ids if sid not in styles_by_id]
    if unknown:
        raise ValueError(f"Unknown style id(s): {', '.join(unknown)}")
    return [styles_by_id[sid] for sid in style_ids]
