import pytest

from src.pipelines.pipes._shared.video.ltx_common import snap_ltx_geometry

from .test_ltx25_ic_lora_captions import ltx25_template, process_images

ACTION = "The knight walks into the tavern and sits beside the fox"
NEUTRAL_SHEET = "a sheet of panels, each showing a character or a prop"
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted"


def pictures(*labels):
    return [{"path": f"/media/p{i}.png", **({"label": label} if label else {})} for i, label in enumerate(labels)]


def render(template, prompt=ACTION, negative="", **over):
    over.setdefault("input_video", None)
    pipes = process_images(template, "ingredients", [{"positive": prompt, "negative": negative}], **over)
    return pipes


def caption(template, prompt=ACTION, **over):
    return render(template, prompt, **over)["prompt_encoder"]["config"]["p_prompt"]["output"]


def test_the_caption_is_the_reference_sheet_then_the_generated_video(ltx25_template):
    text = caption(ltx25_template, pictures=pictures("a knight", "a red fox"))

    assert text == f"Reference sheet: two panels showing a knight and a red fox. Generated video: {ACTION}"


@pytest.mark.parametrize("full", [
    "Reference sheet: a knight and a fox. Generated video: they bow.",
    "reference sheet: my own words. Generated video: they bow.",
])
def test_a_prompt_that_starts_with_the_format_is_used_verbatim(ltx25_template, full):
    assert caption(ltx25_template, full, pictures=pictures("a knight")) == full


def test_the_form_has_no_text_box_besides_the_prompt(ltx25_template):
    from pathlib import Path

    import yaml

    names = []
    for path in Path("content/presets/marketplace/LTX-2.5/modes/ingredients/tabs").glob("*.yml"):
        names += [f.get("name") for f in yaml.safe_load(path.read_text())["fields"] if f.get("type") == "string"]

    assert names == []


def test_an_empty_description_is_written_from_the_picture_names(ltx25_template):
    text = caption(ltx25_template, pictures=pictures("a knight", "a red fox", "a brass lantern"))

    assert text == f"Reference sheet: three panels showing a knight, a red fox and a brass lantern. Generated video: {ACTION}"


def test_one_named_picture_is_one_panel(ltx25_template):
    text = caption(ltx25_template, pictures=pictures("a knight"))

    assert text == f"Reference sheet: one panel showing a knight. Generated video: {ACTION}"


@pytest.mark.parametrize("count, words", [(2, "two"), (3, "three"), (4, "four"), (5, "five"), (6, "six")])
def test_unnamed_pictures_are_described_by_their_count(ltx25_template, count, words):
    text = caption(ltx25_template, pictures=pictures(*[None] * count))

    assert text == f"Reference sheet: {words} panels, each showing a character or a prop. Generated video: {ACTION}"


def test_a_single_unnamed_picture_is_one_panel(ltx25_template):
    text = caption(ltx25_template, pictures=pictures(None))

    assert text == f"Reference sheet: one panel showing a character or a prop. Generated video: {ACTION}"


def test_partly_named_pictures_fall_back_to_the_count(ltx25_template):
    text = caption(ltx25_template, pictures=pictures("a knight", None))

    assert text == f"Reference sheet: two panels, each showing a character or a prop. Generated video: {ACTION}"


def test_a_ready_made_sheet_without_a_description_gets_the_neutral_one(ltx25_template):
    text = caption(ltx25_template, reference_sheet="/media/sheet.png")

    assert text == f"Reference sheet: {NEUTRAL_SHEET}. Generated video: {ACTION}"


def test_the_prompt_text_is_stripped_but_otherwise_kept(ltx25_template):
    text = caption(ltx25_template, "  The fox jumps.  ", reference_sheet="/media/sheet.png")

    assert text.endswith("Generated video: The fox jumps.")


def test_the_default_negative_fills_an_empty_negative_prompt(ltx25_template):
    pipes = render(ltx25_template, pictures=pictures(None))

    assert pipes["prompt_encoder"]["config"]["n_prompt"]["input"] == NEGATIVE


def test_a_written_negative_prompt_wins_over_the_default(ltx25_template):
    pipes = render(ltx25_template, negative="extra fingers", pictures=pictures(None))

    assert pipes["prompt_encoder"]["config"]["n_prompt"]["input"] == "extra fingers"


@pytest.mark.parametrize("seconds, frames", [(1, 25), (3, 73), (5, 121), (8, 193), (10, 241)])
def test_seconds_become_frames_on_the_legal_grid_at_24_fps(ltx25_template, seconds, frames):
    config = render(ltx25_template, pictures=pictures(None), duration=seconds)["generator"]["config"]

    assert config["frames"] == frames
    assert snap_ltx_geometry(768, 448, config["frames"])[2] == frames
    assert config["fps"] == 24.0


def test_five_seconds_is_the_121_frame_training_length_by_default(ltx25_template):
    assert render(ltx25_template, pictures=pictures(None))["generator"]["config"]["frames"] == 121


def test_the_defaults_are_the_trained_size_and_the_distilled_profile(ltx25_template):
    config = render(ltx25_template, pictures=pictures(None))["generator"]["config"]

    assert config["resolution"] == "768x448"
    assert config["steps"] == 8
    assert config["audio"] is False


def test_the_sheet_is_held_as_the_reference_for_the_whole_clip(ltx25_template):
    pipes = render(ltx25_template, pictures=pictures(None))

    assert pipes["generator"]["config"]["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "reference", "hold": True},
    ]
    assert {"name": "image", "provider": "image_sheet", "output_var": "image", "enabled": True} in pipes["generator"]["input"]


def test_the_ingredients_lora_runs_scoped_at_the_chosen_strength(ltx25_template):
    config = render(ltx25_template, pictures=pictures(None), effect_strength=0.8)["generator"]["config"]

    assert config["scoped_loras"] == [{"file_path": "/models/effect.safetensors", "weight": 0.8}]


def test_the_lora_strength_defaults_to_one(ltx25_template):
    config = render(ltx25_template, pictures=pictures(None))["generator"]["config"]

    assert config["scoped_loras"][0]["weight"] == 1.0


def test_pictures_go_to_the_loader_in_order_and_the_sheet_pipe_knows_how_many_are_panels(ltx25_template):
    pipes = render(ltx25_template, pictures=pictures("a", "b", "c"))

    assert [m["path"] for m in pipes["media_loader"]["config"]["media"]] == ["/media/p0.png", "/media/p1.png", "/media/p2.png"]
    assert pipes["image_sheet"]["config"]["pictures"] == 3
    assert pipes["image_sheet"]["config"]["resolution"] == "768x448"


def test_a_ready_made_sheet_is_loaded_after_no_panels(ltx25_template):
    pipes = render(ltx25_template, reference_sheet="/media/sheet.png")

    assert [m["path"] for m in pipes["media_loader"]["config"]["media"]] == ["/media/sheet.png"]
    assert pipes["image_sheet"]["config"]["pictures"] == 0


def test_the_sheet_is_fitted_to_the_picked_resolution(ltx25_template):
    pipes = render(ltx25_template, pictures=pictures(None), resolution="832x480")

    assert pipes["image_sheet"]["config"]["resolution"] == "832x480"
    assert pipes["generator"]["config"]["resolution"] == "832x480"


def test_the_mode_guide_keeps_the_writer_off_the_sheet_labels(ltx25_template):
    guide = " ".join(ltx25_template.llm["modes"]["ingredients"]["guide"].split())

    assert "never type those labels" in guide
    assert "never describe how they look" in guide
    assert "ingredients" not in ltx25_template.vars["optional_prompt_modes"]


def test_the_ingredients_lora_is_declared_for_the_downloader(ltx25_template):
    import yaml
    from pathlib import Path

    fields = yaml.safe_load(Path("content/presets/marketplace/LTX-2.5/files/form/models_effect.yml").read_text())["fields"]
    lora = next(f for f in fields if f["name"] == "effect_lora")
    generation = Path("content/presets/marketplace/LTX-2.5/modes/ingredients/tabs/generation.yml").read_text()

    assert lora["required"] is True
    assert "ltx-2.5-22b-ic-lora-ingredients-0.9.safetensors" in generation
    assert "Lightricks/LTX-2.5-22b-IC-LoRA-Ingredients" in generation


def manual_check():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("ltx_ingredients_check", Path("tests/manual/ltx_ingredients_check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("names", [[], ["a knight"], ["a knight", "a red fox"], ["a knight", "a red fox", "a brass lantern"]])
def test_the_manual_check_writes_the_same_caption_as_the_mode(ltx25_template, names):
    check = manual_check()
    labels = names or [None, None]
    expected = caption(ltx25_template, pictures=pictures(*labels))
    count = len(labels)

    assert check.caption(check.sheet_text(names, count), ACTION) == expected


def test_the_manual_check_writes_the_same_neutral_caption_for_a_ready_sheet(ltx25_template):
    check = manual_check()

    assert check.caption(check.sheet_text([], 0), ACTION) == caption(ltx25_template, reference_sheet="/media/sheet.png")


def test_the_manual_check_counts_frames_like_the_mode(ltx25_template):
    check = manual_check()

    for seconds in (1, 5, 10):
        assert check.frame_count(seconds) == render(ltx25_template, pictures=pictures(None), duration=seconds)["generator"]["config"]["frames"]


def test_the_manual_check_also_keeps_a_full_format_prompt_verbatim():
    check = manual_check()
    full = "Reference sheet: a fox. Generated video: it runs."

    assert check.caption("anything", full) == full
