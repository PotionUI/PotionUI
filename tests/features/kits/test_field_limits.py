from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.preset_render import ROOT, load_all_presets
from src.features.kits import operations
from src.features.kits.expansion import field_policies
from src.features.kits.field_limits import DEFAULT_FIELD_CAP, form_limits
from src.features.kits.resolve import resolve_citations
from tests.features.kits.test_expansion import ExpansionBase, template as bare_template
from tests.features.kits.test_routes import RoutesBase


def media_field(name, **configuration):
    return SimpleNamespace(type="image", name=name, configuration=configuration, children=None)


def with_form(specs, *fields, mode="refs"):
    return SimpleNamespace(
        prompt_resources={mode: specs},
        modes={mode: SimpleNamespace(forms=[SimpleNamespace(fields=list(fields))])},
    )


IMAGES = [{"field": "images", "kind": "image", "token": "<image@>"}]


class TestFormLimits(ExpansionBase):

    def fill(self, count):
        for number in range(count):
            self.media(f"pic{number}")

    def go_with(self, template, text="@kits.mara", form_data=None):
        from src.features.kits.generation import expand_request
        from tests.features.kits.test_expansion import request
        req = request(text, "", form_data if form_data is not None else {"images": []})
        return req, expand_request(self.expander, self.user_id, req, template, "refs", {"images": [self.kit.id]})

    def test_a_whole_kit_stops_at_an_explicit_declared_limit(self):
        self.fill(10)
        spec = [{**IMAGES[0], "limit": 3}]

        req, expansion = self.go_with(with_form(spec, media_field("images", multi=True, max_items=10)))

        self.assertEqual(len(req.form_data["images"]), 3)
        self.assertEqual(self.codes(expansion), ["limit"] * 7)

    def test_without_a_declared_limit_the_form_maximum_applies(self):
        self.fill(10)

        req, expansion = self.go_with(with_form(IMAGES, media_field("images", multi=True, max_items=2)))

        self.assertEqual(len(req.form_data["images"]), 2)
        self.assertEqual(self.codes(expansion), ["limit"] * 8)

    def test_already_loaded_references_count_first(self):
        self.fill(10)
        loaded = [{"path": "uploads/x.png", "relative_path": "uploads/x.png", "type": "image"}]

        req, _ = self.go_with(with_form(IMAGES, media_field("images", multi=True, max_items=3)), form_data={"images": loaded})

        self.assertEqual(len(req.form_data["images"]), 3)

    def test_a_single_slot_field_takes_one(self):
        self.fill(4)

        req, _ = self.go_with(with_form(IMAGES, media_field("images")))

        self.assertEqual(len(req.form_data["images"]), 1)

    def test_with_no_limit_anywhere_a_small_default_applies(self):
        self.fill(DEFAULT_FIELD_CAP + 3)

        req, expansion = self.go_with(bare_template(IMAGES))

        self.assertEqual(len(req.form_data["images"]), DEFAULT_FIELD_CAP)
        self.assertEqual(self.codes(expansion), ["limit"] * 3)

    def test_per_kind_form_maximum_is_used(self):
        policy = field_policies(
            [{"field": "r", "kind": "image", "token": "<P @>"}, {"field": "r", "kind": "video", "token": "<V @>"}],
            form_limits(with_form([], media_field("r", multi=True, max_items_by_kind={"image": 4, "video": 1})), "refs"),
        )["r"]

        self.assertEqual((dict(policy.limits), policy.total), ({"image": 4, "video": 1}, None))

    def test_declared_limits_are_unchanged_when_the_form_allows_more(self):
        specs = [
            {"field": "r", "kind": "image", "token": "<P @>", "limit": 9, "limit_total": 12},
            {"field": "r", "kind": "video", "token": "<V @>", "limit": 3},
        ]
        form = form_limits(with_form(specs, media_field("r", multi=True, max_items_by_kind={"image": 9, "video": 3})), "refs")

        policy = field_policies(specs, form)["r"]

        self.assertEqual((dict(policy.limits), policy.total), ({"image": 9, "video": 3}, 12))

    def test_resolve_reports_the_form_limit_and_skips_the_rest(self):
        self.fill(6)
        owner = with_form(IMAGES, media_field("images", multi=True, max_items=2))

        result = resolve_citations(
            self.expander, self.user_id, owner.prompt_resources["refs"], "images", "", ["mara"], None,
            owner.prompt_resources["refs"], {}, form_limits(owner, "refs"),
        )

        self.assertEqual((len(result["items"]), result["limits"]["total"]), (2, 2))
        self.assertEqual([entry["code"] for entry in result["skipped"]].count("limit"), 4)


@pytest.fixture(scope="module")
def presets():
    found, _ = load_all_presets(presets_root=ROOT / "content" / "presets" / "marketplace", include_plugins=False)
    return {Path(p.path).name: p for p in found}


@pytest.mark.parametrize("name,mode,field", [("QwenImage-2.1", "combine", "images"), ("Flux3", "edit", "source_image")])
def test_shipped_presets_cap_a_whole_kit_at_ten_matching_their_form(presets, name, mode, field):
    preset = presets[name]
    policy = field_policies(preset.prompt_resources[mode], form_limits(preset, mode))[field]

    assert dict(policy.limits) == {"image": 10}
    assert form_limits(preset, mode)[field].total == 10


def test_h3_keeps_its_declared_limits(presets):
    preset = presets["MiniMax-H3"]

    policy = field_policies(preset.prompt_resources["refs"], form_limits(preset, "refs"))["references"]

    assert (dict(policy.limits), policy.total) == ({"image": 9, "video": 3, "audio": 3}, 12)


class TestResolveRouteLimits(RoutesBase):

    def test_a_bare_kit_is_cut_at_the_form_maximum_and_reports_the_rest(self):
        self.template = with_form(
            [{"field": "images", "kind": "image", "token": "<Picture @>"}], media_field("images", multi=True, max_items=3),
        )
        kit = self.kit("Mara")
        for number in range(10):
            self.call("POST", f"/{kit['id']}/items", json={"upload_id": self.upload(f"p{number}.png").id})

        data = self.call("POST", "/resolve", json={"cites": ["mara"], "preset_id": "h3", "mode": "refs"}).json()["data"]

        self.assertEqual((len(data["items"]), data["limits"]["total"]), (3, 3))
        self.assertEqual([entry["code"] for entry in data["skipped"]], ["limit"] * 7)
