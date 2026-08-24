"""
Krea-2 txt2img saves exactly ONE gallery file per seed - the enhanced image
when Enhance is ON, the base image when it's OFF. There is no second
`gallery_enhanced` pipe (that design saved 2N files for N images, the back
half with no matching param_emitter row). Renders the real pipeline.yml
through the real TemplateProcessor, i.e. the exact node graph
`PipelineBuilder` would build.

With Enhance ON, the single `gallery` node's `image` input resolves to
`compare_enhance` (the `artifact`/compare pipe that forwards the enhanced
pass and, as a side effect, emits a before/after compare artifact) - never
directly to `generator` (the base pass), which is the whole point: the base
image is never wired to anything that saves it.
"""
from pathlib import Path
from unittest.mock import Mock

import yaml

from src.features.generation.generation import validate_pipe_configuration
from src.pipelines.pipes.gallery.main import GalleryPipe
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
    context = _context(form_data)
    tp = TemplateProcessor(settings_manager=Mock())
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


def test_only_one_gallery_pipe_declared():
    """No `gallery_enhanced` (or any second `gallery` node) exists - at most N
    files can ever be saved for N images, whatever Enhance is set to."""
    gallery_nodes = [n for n in _pipeline() if n["name"] == "gallery"]
    assert len(gallery_nodes) == 1
    assert gallery_nodes[0]["id"] == "gallery"


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
