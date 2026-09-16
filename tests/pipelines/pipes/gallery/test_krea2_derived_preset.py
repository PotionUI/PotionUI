"""
Krea-2 txt2img saves one gallery file per seed by default - the enhanced image
when Enhance is ON, the base image when it's OFF. The optional
`enhance_keep_base` / `face_detailer_keep_base` knobs each add one more saved
image per seed, and `param_emitter`'s `passes` counts the saves so every saved
index keeps its per-image rows. Renders the real pipeline.yml through the real
TemplateProcessor, i.e. the exact node graph `PipelineBuilder` would build.

With Enhance ON, the final `gallery` node's `image` input resolves to
`compare_enhance` (the `artifact`/compare pipe that forwards the enhanced
pass and, as a side effect, emits a before/after compare artifact) - never
directly to `generator` (the base pass): the base image is only ever saved
when its own opt-in knob asks for it.
"""
from pathlib import Path
from unittest.mock import Mock

import yaml

from src.features.generation.engine import validate_pipe_configuration
from src.pipelines.pipes.gallery.main import GalleryPipe
from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.platform.templating import TemplateProcessor

REPO = Path(__file__).resolve().parents[4]
PIPELINE_YML = (
    REPO / "content" / "presets" / "marketplace" / "Krea2" / "modes" / "txt2img" / "pipeline.yml"
)


def _deep_render(obj, tp, context):
    if isinstance(obj, str):
        if "{{" in obj or "{%" in obj:
            return tp.process_template(obj, dict(context))
        return obj
    if isinstance(obj, list):
        return [_deep_render(x, tp, context) for x in obj]
    if isinstance(obj, dict):
        return {k: _deep_render(v, tp, context) for k, v in obj.items()}
    return obj


_KREA2_ID = yaml.safe_load((PIPELINE_YML.parents[2] / "preset.yml").read_text())["id"]


def _bound(form_data):
    """Bind like a real request: every declared field carries its default, so the
    pipeline renders against the same values production does."""
    template = PresetTemplateLoader(["content/presets"]).load_preset_by_id(_KREA2_ID)
    raw = {
        "diffusion_model": "/models/krea2.safetensors",
        "text_encoder": "/models/krea2_te.safetensors",
        "vae": "/models/krea2_vae.safetensors",
        **form_data,
    }
    return dict(bind_form(template, "txt2img", None, raw, user_id=None, storage_dir=None).values)


def _context(form_data):
    # Same context shape PresetProcessor.process() builds for pipeline.yml
    # rendering (src/features/presets/processor.py).
    return {
        "form": form_data,
        "generation": {
            "prompts": {
                "first": {"positive": "a cat", "negative": ""},
                "pairs": [{"positive": "a cat", "negative": ""}],
                "positives": ["a cat"],
                "negatives": [""],
            }
        },
    }


def _pipeline():
    return yaml.safe_load(PIPELINE_YML.read_text())["pipeline"]


def _rendered_nodes(form_data, names, render_config_for=()):
    """Render `enabled`/`input` for every node whose name is in `names`, plus
    `configuration` for those whose id is also in `render_config_for` - the
    base generator's own config calls get_speed_profile(), which needs a real
    preset/speed_profiles context this test doesn't build, so it's skipped
    unless actually asserted on."""
    context = _context(_bound(form_data))
    tp = TemplateProcessor(settings=Mock())
    nodes = {}
    for node in _pipeline():
        if node["name"] not in names:
            continue
        node_id = node.get("id") or node["name"]
        entry = {
            "name": node["name"],
            "enabled": _deep_render(node.get("enabled"), tp, context),
            "input": _deep_render(node.get("input", []), tp, context),
        }
        if node_id in render_config_for:
            entry["configuration"] = _deep_render(node.get("configuration", {}), tp, context)
        nodes[node_id] = entry
    return nodes


def _node_order():
    return [n.get("id") or n["name"] for n in _pipeline()]


GALLERY_IDS = ("gallery", "gallery_before_enhance", "gallery_before_faces")


def _passes(form_data):
    raw = next(n for n in _pipeline() if n["name"] == "param_emitter")["configuration"]["passes"]
    value = _deep_render(raw, TemplateProcessor(settings=Mock()), _context(_bound(form_data)))
    if isinstance(value, dict):
        value = value["value"]
    return int(value)


def _enabled_galleries(nodes):
    return [gallery_id for gallery_id in GALLERY_IDS if nodes[gallery_id]["enabled"]]


def test_the_declared_gallery_nodes_are_the_final_save_and_two_opt_in_saves():
    gallery_nodes = [n for n in _pipeline() if n["name"] == "gallery"]
    assert [n["id"] for n in gallery_nodes] == list(GALLERY_IDS)


def test_only_the_final_gallery_saves_when_no_keep_base_knob_is_set():
    form_data = {"enhance_enabled": True, "face_detailer_enabled": True, "quantity": 2}
    assert _enabled_galleries(_rendered_nodes(form_data, {"gallery"})) == ["gallery"]
    assert _passes(form_data) == 1


def test_passes_always_matches_the_number_of_enabled_gallery_nodes():
    combinations = (
        {"enhance_enabled": True, "enhance_keep_base": True,
         "face_detailer_enabled": True, "face_detailer_keep_base": True},
        {"enhance_enabled": True, "enhance_keep_base": True,
         "face_detailer_enabled": True, "face_detailer_keep_base": False},
        {"enhance_enabled": False, "enhance_keep_base": True,
         "face_detailer_enabled": True, "face_detailer_keep_base": True},
        {"enhance_enabled": False, "enhance_keep_base": True,
         "face_detailer_enabled": False, "face_detailer_keep_base": True},
    )
    expected = (3, 2, 2, 1)
    for form_data, count in zip(combinations, expected):
        form_data = {**form_data, "quantity": 2}
        assert len(_enabled_galleries(_rendered_nodes(form_data, {"gallery"}))) == count, form_data
        assert _passes(form_data) == count, form_data


def test_a_keep_base_knob_alone_never_saves_without_its_feature():
    form_data = {"enhance_enabled": False, "enhance_keep_base": True,
                 "face_detailer_enabled": False, "face_detailer_keep_base": True, "quantity": 1}
    assert _enabled_galleries(_rendered_nodes(form_data, {"gallery"})) == ["gallery"]
    assert _passes(form_data) == 1


def test_the_final_gallery_saves_first_so_the_finished_image_is_file_zero():
    order = _node_order()
    assert order.index("gallery") < order.index("gallery_before_enhance")
    assert order.index("gallery") < order.index("gallery_before_faces")


def test_pipes_are_declared_after_everything_they_read_from():
    """Pipes execute in declared order (generation.py), so the enhance pass and
    the gallery (which can depend on it) must come after the base generator."""
    order = _node_order()
    for earlier, later in (
        ("generator", "gallery"),
        ("generator", "enhancer"),
        ("enhancer", "compare_enhance"),
        ("compare_enhance", "gallery"),
    ):
        assert order.index(earlier) < order.index(later), f"{later} must follow {earlier}"


def test_enhance_on_gallery_sources_the_enhanced_pass():
    nodes = _rendered_nodes(
        {"enhance_enabled": True, "quantity": 2},
        {"gallery", "artifact", "generator/krea2"},
        render_config_for={"gallery", "compare_enhance"},
    )

    gallery = nodes["gallery"]
    assert gallery["enabled"] is True
    image_input = next(i for i in gallery["input"] if i[0] == "image")
    assert image_input[1] == "compare_enhance", (
        "gallery must read from compare_enhance (the forwarded enhanced image), "
        "never directly from the base generator, when Enhance is ON"
    )

    compare = nodes["compare_enhance"]
    assert compare["enabled"] is True
    assert compare["configuration"]["mode"] == "compare"
    assert compare["configuration"]["output"] == "right"
    before = next(i for i in compare["input"] if i[0] == "before_image")
    after = next(i for i in compare["input"] if i[0] == "after_image")
    assert before[1:] == ["generator", "image"]
    assert after[1:] == ["enhancer", "image"]

    # GalleryPipe itself never sees a `derived` flag - the config the pipe
    # actually receives has no split between "primary" and "derived" saves.
    gallery_config = validate_pipe_configuration(GalleryPipe, gallery["configuration"])
    assert gallery_config["derived"] is False


def test_enhance_off_gallery_sources_the_base_pass():
    nodes = _rendered_nodes(
        {"quantity": 2},
        {"gallery", "artifact", "generator/krea2"},
    )

    gallery = nodes["gallery"]
    assert gallery["enabled"] is True
    image_input = next(i for i in gallery["input"] if i[0] == "image")
    assert image_input[1] == "generator"

    # The enhance pass and its compare bridge are both declared but inert.
    assert nodes["enhancer"]["enabled"] is False
    assert nodes["compare_enhance"]["enabled"] is False


def test_exactly_one_gallery_node_saves_regardless_of_enhance():
    """Pins the one-file-per-generation contract directly: whichever value
    `form.enhance_enabled` takes, the single `gallery` node is always
    enabled, and it is fed from whichever pipe actually produced the image
    the user receives."""
    expectations = (
        (False, "generator"),
        (True, "compare_enhance"),
    )
    for enhance_enabled, expected_source in expectations:
        nodes = _rendered_nodes(
            {"enhance_enabled": enhance_enabled, "quantity": 1},
            {"gallery"},
        )
        gallery = nodes["gallery"]
        assert gallery["enabled"] is True, f"enhance_enabled={enhance_enabled}: gallery must always be enabled"
        image_source = next(i for i in gallery["input"] if i[0] == "image")[1]
        assert image_source == expected_source
