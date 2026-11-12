import json

import pytest

from scripts import preset_render
from src.features.kits import operations
from src.features.kits.expansion import KitExpander, field_policies
from src.features.kits.generation import expand_request
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    MAX_AUDIO_REFERENCES,
    MAX_IMAGE_REFERENCES,
    MAX_REFERENCES,
    MAX_VIDEO_REFERENCES,
)
from tests.features.kits.support import KitsTestBase
from tests.features.kits.test_expansion import native, request

H3_ID = "01KX47H3MINIMAX000000000VA"

OLD_STYLE = {
    "references": [
        native("uploads/portrait.png"),
        native("uploads/street.png"),
        native("uploads/walk.mp4", "video"),
        native("uploads/voice.wav", "audio"),
    ],
}
OLD_STYLE_PACKED = {
    "references": [
        {"type": "image", "path": "uploads/portrait.png"},
        {"type": "image", "path": "uploads/street.png"},
    ],
    "reference_videos": [{"type": "video", "path": "uploads/walk.mp4"}],
    "reference_audios": [{"type": "audio", "path": "uploads/voice.wav"}],
}


@pytest.fixture(scope="module")
def h3():
    presets, _errors = preset_render.load_all_presets(include_plugins=False)
    preset = preset_render.find_preset(presets, H3_ID)
    return preset, preset_render.build_processor()


def render(h3, form):
    preset, processor = h3
    base = preset_render.build_fixture_form_data(preset, "refs")
    rendered = preset_render.render_preset_mode(processor, preset, "refs", {**base, **form})
    assert "error" not in rendered, rendered.get("error")
    return rendered


def packed(rendered):
    out = {}
    for pipe in rendered["pipes"]:
        if pipe["name"] == "media_loader" and pipe["enabled"]["value"]:
            out[pipe["id"]] = _media(pipe)
    return out


def _media(pipe):
    return pipe["config"]["media"]["value"]


class TestPresetLimitsMatchThePipe:

    def test_the_prompt_resource_limits_are_the_generators_own(self, h3):
        preset, _processor = h3
        policy = field_policies(preset.prompt_resources["refs"])["references"]

        assert dict(policy.limits) == {
            "image": MAX_IMAGE_REFERENCES, "video": MAX_VIDEO_REFERENCES, "audio": MAX_AUDIO_REFERENCES,
        }
        assert policy.total == MAX_REFERENCES


class TestOldStyleReferencesStayExactlyAsTheyWere(KitsTestBase):

    @pytest.fixture(autouse=True)
    def _inject(self, h3):
        self.h3 = h3

    def setUp(self):
        super().setUp()
        self.expander = KitExpander(self.repo)

    def test_fixed_old_style_inputs_render_the_same_packed_media(self):
        assert packed(render(self.h3, OLD_STYLE)) == OLD_STYLE_PACKED

    def test_passing_through_kit_resolution_changes_nothing_bit_for_bit(self):
        text = "@[references:uploads/street.png] and @[references:uploads/walk.mp4]"
        before = json.dumps(render(self.h3, OLD_STYLE), sort_keys=True)
        req = request(text, "", OLD_STYLE)

        expansion = expand_request(self.expander, self.user_id, req, self.h3[0], "refs", {})

        assert req.form_data is OLD_STYLE
        assert req.prompts[0].positive == text
        assert not expansion.touched
        assert json.dumps(render(self.h3, req.form_data), sort_keys=True) == before

    def test_a_loaded_but_uncited_kit_leaves_the_conditioning_identical(self):
        kit = operations.create_kit(self.repo, self.user_id, "Mara")
        for name in ("a", "b", "c"):
            operations.add_media_item(self.repo, self.user_id, kit.id, self.upload(f"{name}.png").id, name=name)
        before = json.dumps(render(self.h3, OLD_STYLE), sort_keys=True)
        req = request("a street", "", OLD_STYLE)

        expand_request(self.expander, self.user_id, req, self.h3[0], "refs", {"references": [kit.id]})

        assert json.dumps(render(self.h3, req.form_data), sort_keys=True) == before


class TestOnlyWhatIsCitedIsPacked(KitsTestBase):

    @pytest.fixture(autouse=True)
    def _inject(self, h3):
        self.h3 = h3

    def setUp(self):
        super().setUp()
        self.expander = KitExpander(self.repo)
        self.kit = operations.create_kit(self.repo, self.user_id, "Mara")

    def item(self, name, kind="image"):
        extension = {"image": "png", "video": "mp4", "audio": "wav"}[kind]
        upload = self.upload(f"{name}.{extension}", kind)
        return operations.add_media_item(self.repo, self.user_id, self.kit.id, upload.id, name=name)

    def go(self, text, form_data=None, loaded=True):
        req = request(text, "", dict(form_data or {"references": []}))
        rows = {"references": [self.kit.id]} if loaded else {}
        expansion = expand_request(self.expander, self.user_id, req, self.h3[0], "refs", rows)
        return req, expansion

    def test_a_cited_item_is_packed_after_the_natives_and_uncited_ones_are_not(self):
        face = self.item("face")
        for name in ("spare1", "spare2", "spare3"):
            self.item(name)

        req, _ = self.go(f"@[references:kit:{self.kit.id}/{face.id}] by the harbour", OLD_STYLE)
        rendered = render(self.h3, req.form_data)

        assert packed(rendered)["references"] == [
            {"type": "image", "path": "uploads/portrait.png"},
            {"type": "image", "path": "uploads/street.png"},
            {"type": "image", "path": "uploads/face.png"},
        ]
        assert "spare" not in json.dumps(rendered)

    def test_the_prompt_tokens_count_the_packed_pictures_only(self):
        from src.features.prompt.resources import resolve_prompt_resources

        face = self.item("face")
        self.item("spare")

        req, _ = self.go(f"@[references:kit:{self.kit.id}/{face.id}]", {"references": [native("uploads/own.png")]})
        final, problems = resolve_prompt_resources(req.prompts[0].positive, self.h3[0].prompt_resources["refs"], req.form_data)

        assert (final, problems) == ("<Picture 2>", {})

    def test_a_bare_kit_stops_at_the_generators_limits_and_counts_nothing_beyond(self):
        for index in range(11):
            self.item(f"pic{index:02d}")
        for index in range(4):
            self.item(f"clip{index}", "video")
        self.item("voice", "audio")

        req, expansion = self.go(f"@[references:kit:{self.kit.id}]")
        rendered = packed(render(self.h3, req.form_data))

        assert [e["path"] for e in rendered["references"]] == [f"uploads/pic{index:02d}.png" for index in range(9)]
        assert [e["path"] for e in rendered["reference_videos"]] == [f"uploads/clip{index}.mp4" for index in range(3)]
        assert "reference_audios" not in rendered
        assert sum(len(group) for group in rendered.values()) == MAX_REFERENCES
        assert [(n.item, n.code) for n in expansion.notices] == [
            ("pic09", "limit"), ("pic10", "limit"), ("clip3", "limit"), ("voice", "limit"),
        ]

    def test_citing_one_item_at_a_time_is_never_counted_with_the_rest_of_the_kit(self):
        items = [self.item(f"pic{index}") for index in range(11)]

        req, expansion = self.go(f"@[references:kit:{self.kit.id}/{items[10].id}]")

        assert expansion.notices == []
        assert [e["path"] for e in packed(render(self.h3, req.form_data))["references"]] == ["uploads/pic10.png"]
