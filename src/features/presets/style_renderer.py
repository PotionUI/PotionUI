"""Renders a preset's `styles.yml` previews.

Runs one real generation per style (see docs/presets/manifest.md "Styles") and saves
a downscaled WebP preview into the preset's `public/styles/` directory,
mirroring the orchestration pattern of `PhrasebookPreviewGenerator`
(`src/features/phrasebook/preview_generator.py`): one generation at a time,
an output callback that only waits for completion, then the saved file is
found through `file_repo.get_generation_files` rather than through the
callback's output payload.
"""
import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from src.platform.observability.logger import logger
from src.features.presets.templates import default_form_name

STYLE_PREVIEW_LONG_EDGE_DEFAULT = 320
STYLE_PREVIEW_SEED_DEFAULT = 1
STYLE_PREVIEW_WEBP_QUALITY = 80
# The field name presets use for their output-count control (see e.g.
# content/presets/marketplace/Anima/modes/txt2img/tabs/generation.yml) -
# forced to 1 so "take the first output image" is well-defined.
STYLE_PREVIEW_QUANTITY_FIELD = "quantity"

_GENERATION_TIMEOUT_SECONDS = 600.0


class PresetStyleRenderer:
    """Renders one or more of a preset's `styles.yml` previews on demand."""

    def __init__(self, preset_loader, generation_orchestrator, settings):
        self.preset_loader = preset_loader
        self.generation_orchestrator = generation_orchestrator
        self.settings = settings

    async def render_styles(
        self,
        preset_id: str,
        user_id: str,
        style_ids: Optional[List[str]] = None,
        long_edge: int = STYLE_PREVIEW_LONG_EDGE_DEFAULT,
        seed: int = STYLE_PREVIEW_SEED_DEFAULT,
    ) -> Dict[str, Any]:
        """Render `style_ids` (all styles when omitted) and return
        ``{"rendered": [ids], "failed": [{"id", "error"}]}``.

        Raises ValueError for a not-found preset, a preset with no
        `styles.yml`, or an unknown style id - all three are request errors,
        not per-style failures.
        """
        preset = self.preset_loader.load_preset_by_id(preset_id)
        if preset is None:
            raise ValueError(f"Preset '{preset_id}' not found")

        styles_by_id = {style["id"]: style for style in (preset.styles or [])}
        if not styles_by_id:
            raise ValueError(f"Preset '{preset_id}' has no styles.yml")

        if style_ids:
            unknown = [sid for sid in style_ids if sid not in styles_by_id]
            if unknown:
                raise ValueError(f"Unknown style id(s): {', '.join(unknown)}")
            targets = [styles_by_id[sid] for sid in style_ids]
        else:
            targets = list(styles_by_id.values())

        if not preset.modes:
            raise ValueError(f"Preset '{preset_id}' has no modes")
        mode = next(iter(preset.modes))

        rendered: List[str] = []
        failed: List[Dict[str, str]] = []

        for style in targets:
            try:
                await self._render_one(preset, mode, style, user_id, long_edge, seed)
                rendered.append(style["id"])
            except Exception as exc:
                logger.error(
                    f"Failed to render style preview '{style['id']}' for preset {preset_id}: {exc}"
                )
                failed.append({"id": style["id"], "error": str(exc)})

        if rendered:
            self.preset_loader.reload()

        return {"rendered": rendered, "failed": failed}

    async def _render_one(
        self, preset, mode: str, style: Dict[str, Any], user_id: str, long_edge: int, seed: int
    ) -> None:
        from src.features.generation.dto import GenerationRequest, PromptPair
        from src.features.generation.file_repository import file_repo

        prompt = f"{style.get('prepend', '')}{style['example_prompt']}{style.get('append', '')}"
        negative = style.get("negative") or ""

        form_data: Dict[str, Any] = {"seed": seed}
        if self._form_has_field(preset, mode, STYLE_PREVIEW_QUANTITY_FIELD):
            form_data[STYLE_PREVIEW_QUANTITY_FIELD] = 1

        request = GenerationRequest(
            preset_id=preset.id,
            prompts=[PromptPair(positive=prompt, negative=negative)],
            mode=mode,
            form_data=form_data,
        )

        completion = asyncio.Event()

        async def on_output(generation_id: str, output) -> None:
            if output is None:
                completion.set()

        result = await self.generation_orchestrator.start_generation(
            request, user_id, output_callback=on_output
        )
        generation_id = result["generation_id"]

        try:
            await asyncio.wait_for(completion.wait(), timeout=_GENERATION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise ValueError(f"Timed out waiting for generation {generation_id}")

        generation_files = file_repo.get_generation_files(generation_id)
        if not generation_files:
            raise ValueError(f"Generation {generation_id} produced no output file")

        storage_dir = Path(self.settings.get_file_storage_directory(user_id))
        source_path = storage_dir / generation_files[0].file_path

        preview_rel = f"public/styles/{style['id']}.webp"
        preset_dir = Path(preset.path)
        self._write_webp(source_path, preset_dir / preview_rel, long_edge)

        if not style.get("preview"):
            set_style_preview(preset_dir / "styles.yml", style["id"], preview_rel)

    def _form_has_field(self, preset, mode: str, field_name: str) -> bool:
        mode_template = preset.modes.get(mode)
        if mode_template is None or not mode_template.forms:
            return False
        form_name = default_form_name(mode_template)
        form = next((f for f in mode_template.forms if f.name == form_name), mode_template.forms[0])
        return _fields_contain(form.fields, field_name)

    def _write_webp(self, source_path: Path, dest_path: Path, long_edge: int) -> None:
        with Image.open(source_path) as img:
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


def _fields_contain(fields, name: str) -> bool:
    for field in fields or []:
        if field.name == name:
            return True
        if isinstance(field.children, list) and _fields_contain(field.children, name):
            return True
    return False


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
