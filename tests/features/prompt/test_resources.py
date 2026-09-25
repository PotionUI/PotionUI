import pytest

from src.features.forms.binding import FormBindingError
from src.features.generation.dto import PromptPair, SegmentInput
from src.features.presets import PresetTemplateLoader
from src.features.prompt.resources import (
    describe_prompt_resources,
    media_item_keys,
    resolve_generation_prompts,
    resolve_prompt_pairs,
    resolve_prompt_resources,
    resolve_segment_texts,
)

RESOURCES = [
    {"field": "references", "kind": "image", "label": "Pictures", "token": "<Picture @>"},
    {"field": "reference_videos", "kind": "video", "token": "<Video @>"},
]


def _upload(path):
    return {"path": path, "relative_path": path, "type": "image", "name": path.rsplit("/", 1)[-1]}


FORM = {
    "references": [_upload("storage/uploads/a.png"), _upload("storage/uploads/b.png"), "storage/uploads/c.png"],
    "reference_videos": [_upload("storage/uploads/walk.mp4")],
    "steps": 24,
}


class TestMediaItemKeys:
    def test_string_item_is_its_own_key(self):
        assert media_item_keys("storage/uploads/a.png") == ("storage/uploads/a.png",)

    def test_object_item_keys_follow_relative_path_path_url(self):
        item = {"relative_path": "r.png", "path": "/abs/r.png", "url": "/api/files/r.png"}
        assert media_item_keys(item) == ("r.png", "/abs/r.png", "/api/files/r.png")

    def test_empty_and_unknown_items_have_no_key(self):
        assert media_item_keys("") == ()
        assert media_item_keys({"name": "x"}) == ()
        assert media_item_keys(3) == ()


class TestResolvePromptResources:
    def test_markers_render_each_items_position(self):
        text, problems = resolve_prompt_resources(
            "@[references:storage/uploads/b.png] meets @[references:storage/uploads/a.png] "
            "and @[references:storage/uploads/c.png]",
            RESOURCES, FORM,
        )
        assert problems == {}
        assert text == "<Picture 2> meets <Picture 1> and <Picture 3>"

    def test_reordering_the_field_renumbers_the_prompt(self):
        reordered = {**FORM, "references": list(reversed(FORM["references"]))}
        text, _ = resolve_prompt_resources(
            "@[references:storage/uploads/a.png] then @[references:storage/uploads/c.png]",
            RESOURCES, reordered,
        )
        assert text == "<Picture 3> then <Picture 1>"

    def test_each_field_counts_on_its_own(self):
        text, problems = resolve_prompt_resources(
            "@[reference_videos:storage/uploads/walk.mp4] with @[references:storage/uploads/a.png]",
            RESOURCES, FORM,
        )
        assert problems == {}
        assert text == "<Video 1> with <Picture 1>"

    def test_plain_tokens_are_left_alone(self):
        text, problems = resolve_prompt_resources(
            "<Picture 2> stands left of @[references:storage/uploads/a.png]; <Video 9> too",
            RESOURCES, FORM,
        )
        assert problems == {}
        assert text == "<Picture 2> stands left of <Picture 1>; <Video 9> too"

    def test_single_item_field_is_position_one(self):
        text, _ = resolve_prompt_resources(
            "@[references:only.png]", RESOURCES, {"references": _upload("only.png")},
        )
        assert text == "<Picture 1>"

    def test_marker_matches_an_items_path_when_relative_path_differs(self):
        form = {"references": [{"path": "/data/u/a.png", "relative_path": "uploads/a.png"}]}
        text, problems = resolve_prompt_resources("@[references:/data/u/a.png]", RESOURCES, form)
        assert problems == {}
        assert text == "<Picture 1>"

    def test_removed_item_is_reported_against_its_field(self):
        text, problems = resolve_prompt_resources(
            "@[references:storage/uploads/gone.png]", RESOURCES, FORM,
        )
        assert text == "@[references:storage/uploads/gone.png]"
        assert list(problems) == ["references"]
        assert "removed" in problems["references"][0]

    def test_marker_on_an_emptied_field_is_reported(self):
        _, problems = resolve_prompt_resources(
            "@[references:storage/uploads/a.png]", RESOURCES, {"references": []},
        )
        assert list(problems) == ["references"]

    def test_unmapped_field_is_reported(self):
        _, problems = resolve_prompt_resources(
            "@[reference_audios:storage/uploads/voice.wav]", RESOURCES,
            {**FORM, "reference_audios": ["storage/uploads/voice.wav"]},
        )
        assert list(problems) == ["reference_audios"]
        assert "cannot reference" in problems["reference_audios"][0]

    def test_text_without_markers_is_untouched(self):
        assert resolve_prompt_resources("a cat, {red|blue} hat", RESOURCES, FORM) == ("a cat, {red|blue} hat", {})


class TestResolvePromptPairs:
    def test_rewrites_positive_and_negative_in_place(self):
        pairs = [
            PromptPair(positive="@[references:storage/uploads/b.png] waves", negative="not @[references:storage/uploads/a.png]"),
            PromptPair(positive="plain", negative=""),
        ]
        resolve_prompt_pairs(pairs, RESOURCES, FORM)
        assert (pairs[0].positive, pairs[0].negative) == ("<Picture 2> waves", "not <Picture 1>")
        assert pairs[1].positive == "plain"

    def test_dangling_marker_raises_a_field_level_binding_error(self):
        pairs = [
            PromptPair(positive="@[references:storage/uploads/gone.png] and @[references:storage/uploads/gone.png]"),
            PromptPair(positive="@[reference_videos:storage/uploads/walk.mp4]"),
        ]
        with pytest.raises(FormBindingError) as exc:
            resolve_prompt_pairs(pairs, RESOURCES, FORM)
        assert list(exc.value.field_errors) == ["references"]
        assert len(exc.value.field_errors["references"]) == 1
        assert "storage/uploads/gone.png" in exc.value.field_errors["references"][0]
        assert exc.value.errors[0].startswith("references: ")

    def test_unmapped_field_raises_a_binding_error(self):
        with pytest.raises(FormBindingError) as exc:
            resolve_prompt_pairs([PromptPair(positive="@[steps:24]")], RESOURCES, FORM)
        assert list(exc.value.field_errors) == ["steps"]

    def test_no_resources_mapped_rejects_any_marker(self):
        with pytest.raises(FormBindingError):
            resolve_prompt_pairs([PromptPair(positive="@[references:storage/uploads/a.png]")], [], FORM)


class TestResolveSegmentTexts:
    def test_segment_texts_render_and_dangling_markers_stay(self):
        segments = [
            SegmentInput(text="@[references:storage/uploads/c.png] smiles"),
            SegmentInput(text="@[references:storage/uploads/gone.png]", is_disabled=True),
        ]
        resolve_segment_texts(segments, RESOURCES, FORM)
        assert segments[0].text == "<Picture 3> smiles"
        assert segments[1].text == "@[references:storage/uploads/gone.png]"


@pytest.fixture(scope="module")
def minimax_h3():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return next(preset for preset in loader.presets if preset.name == "MiniMax-H3")


class TestMiniMaxH3Resources:
    def test_refs_segment_resolves_to_the_guides_tokens(self, minimax_h3):
        resources = minimax_h3.prompt_resources["refs"]
        form = {
            "references": [_upload("storage/uploads/woman.png"), _upload("storage/uploads/cafe.png")],
            "reference_videos": [_upload("storage/uploads/walk.mp4")],
            "reference_audios": ["storage/uploads/voice.wav"],
        }
        pairs = [PromptPair(positive=(
            "subject_definitions: <Subject 1> is the young woman in @[references:storage/uploads/woman.png]. "
            "@[references:storage/uploads/cafe.png] is the first frame of [Shot 1]. "
            "She walks like @[reference_videos:storage/uploads/walk.mp4] and speaks like "
            "@[reference_audios:storage/uploads/voice.wav]."
        ))]
        resolve_prompt_pairs(pairs, resources, form)
        assert pairs[0].positive == (
            "subject_definitions: <Subject 1> is the young woman in <Picture 1>. "
            "<Picture 2> is the first frame of [Shot 1]. "
            "She walks like <Video 1> and speaks like <Audio 1>."
        )

    def test_video_mode_maps_nothing(self, minimax_h3):
        with pytest.raises(FormBindingError) as exc:
            resolve_generation_prompts(
                minimax_h3, "video",
                [PromptPair(positive="@[references:storage/uploads/woman.png]")], None,
                {"references": ["storage/uploads/woman.png"]},
            )
        assert list(exc.value.field_errors) == ["references"]

    def test_generation_prompts_resolve_through_the_presets_mode(self, minimax_h3):
        pairs = [PromptPair(positive="@[references:storage/uploads/b.png]")]
        segments = [SegmentInput(text="@[references:storage/uploads/b.png]")]
        resolve_generation_prompts(
            minimax_h3, "refs", pairs, segments,
            {"references": ["storage/uploads/a.png", "storage/uploads/b.png"]},
        )
        assert pairs[0].positive == "<Picture 2>"
        assert segments[0].text == "<Picture 2>"


class TestResolveGenerationPromptsWithoutMarkers:
    def test_preset_is_not_consulted_when_no_prompt_has_a_marker(self):
        class NoResources:
            @property
            def prompt_resources(self):
                raise AssertionError("prompt_resources read for marker-free prompts")

        pairs = [PromptPair(positive="<Picture 1> in a cafe", negative="blurry")]
        resolve_generation_prompts(NoResources(), "refs", pairs, [SegmentInput(text="plain")], {})
        assert pairs[0].positive == "<Picture 1> in a cafe"


def test_describe_lists_each_item_with_its_current_token():
    form = {**FORM, "references": [{**_upload("storage/uploads/a.png"), "label": "the woman"}, *FORM["references"][1:]]}
    lines = describe_prompt_resources(RESOURCES, form)
    text = "\n".join(lines)
    assert '<Picture 1> "the woman"' in text
    assert "<Picture 2> b.png" in text
    assert "<Picture 3> c.png" in text
    assert "<Picture N> · Pictures (references), 3 items" in text
    assert "<Video N> · reference_videos (reference_videos), 1 item: <Video 1> walk.mp4" in text


def test_describe_flags_an_empty_field_and_skips_nothing_declared():
    lines = describe_prompt_resources(RESOURCES, {"references": [], "reference_videos": None})
    assert any("<Picture N>" in line and "no items yet" in line for line in lines)
    assert any("<Video N>" in line and "no items yet" in line for line in lines)
    assert describe_prompt_resources([], FORM) == []


def test_describe_caps_long_fields():
    many = [_upload(f"storage/uploads/{index}.png") for index in range(15)]
    lines = describe_prompt_resources(RESOURCES[:1], {"references": many})
    item_line = next(line for line in lines if line.startswith("- <Picture N>"))
    assert "<Picture 12> 11.png" in item_line
    assert "<Picture 13>" not in item_line
    assert "…3 more" in item_line
