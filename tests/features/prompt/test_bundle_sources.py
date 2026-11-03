import pytest

from src.features.forms.binding import FormBindingError
from src.features.prompt.reference_pool import BundlePoolMember, build_reference_pool
from src.features.prompt.resource_sources import member_kinds_resolver, with_source_specs
from src.features.prompt.resources import describe_prompt_resources, resolve_prompt_pairs, resolve_prompt_resources
from src.features.video_director.shot_references import derive_shot_references
from src.platform.plugins.prompt_resource_sources import PromptResourceCaller
from tests.fixtures.prompt_source_plugin import FAKE_SOURCE, fake_prompt_source

RESOURCES = [
    {"field": "references", "kind": "image", "token": "<Picture @>"},
    {"field": "references", "kind": "audio", "token": "<Audio @>"},
    {"field": "bundles", "source": FAKE_SOURCE, "kind": "image", "token": "<Picture @>"},
    {"field": "bundles", "source": FAKE_SOURCE, "kind": "audio", "token": "<Audio @>"},
]


def _upload(path, kind="image"):
    return {"relative_path": path, "type": kind}


def _form(*rows):
    return {"references": [_upload("a.png"), _upload("b.png"), _upload("t.wav", "audio")], "bundles": list(rows)}


def _row(key, strength=1.0, copies=1):
    return {"model": key, "strength": strength, "copies": copies}


@pytest.fixture
def members(fake_prompt_source):
    return member_kinds_resolver(PromptResourceCaller(user_id="user-1", is_admin=False, model_file=None))


def _labels(pool):
    return [f"{item.source}:{item.key}:{item.kind}" if isinstance(item, BundlePoolMember) else item["relative_path"] for _, item in pool]


def test_a_plugin_source_packs_each_member_after_the_natives_of_its_kind(members):
    pool, problems = build_reference_pool(
        ["references", "bundles"], _form(_row("model:pair", copies=2), _row("model:off", strength=0)), RESOURCES, members,
    )
    assert problems == []
    assert _labels(pool) == [
        "a.png", "b.png", f"{FAKE_SOURCE}:model:pair:image", f"{FAKE_SOURCE}:model:pair:image",
        "t.wav", f"{FAKE_SOURCE}:model:pair:audio", f"{FAKE_SOURCE}:model:pair:audio",
    ]


def test_the_handler_is_asked_as_the_caller(fake_prompt_source, members):
    _source, calls = fake_prompt_source
    build_reference_pool(["bundles"], _form(_row("model:face")), RESOURCES, members)
    assert [(key, caller.user_id) for key, caller in calls] == [("model:face", "user-1")]


def test_a_handler_value_error_is_a_named_pool_problem(members):
    _pool, problems = build_reference_pool(["bundles"], _form(_row("model:gone")), RESOURCES, members)
    assert problems == ["bundles: the Bundle model:gone is gone"]


def test_a_handler_crash_is_a_clean_problem_under_the_source_label(members):
    _pool, problems = build_reference_pool(["bundles"], _form(_row("model:boom")), RESOURCES, members)
    assert problems == ["bundles: the Bundle model:boom cannot be read"]


def test_without_a_resolver_the_source_label_names_the_problem(fake_prompt_source):
    _pool, problems = build_reference_pool(["bundles"], _form(_row("model:face")), RESOURCES, None)
    assert problems == ["bundles: the Bundle model:face cannot be read here"]


def test_copies_outside_the_range_name_the_source(members):
    _pool, problems = build_reference_pool(["bundles"], _form(_row("model:face", copies=13)), RESOURCES, members)
    assert problems == ["bundles: Bundle 'model:face': copies must be a whole number from 1 to 12, got 13"]


def test_a_plain_prompt_citation_names_every_member_of_a_plugin_item(members):
    text, problems = resolve_prompt_resources(
        "@[bundles:model:pair] beside @[references:b.png]", RESOURCES, _form(_row("model:pair")), members,
    )
    assert problems == {}
    assert text == "<Picture 3> <Audio 2> beside <Picture 2>"


def test_a_member_kind_the_field_does_not_map_is_named_with_the_source_label(members):
    resources = RESOURCES[:3]
    _text, problems = resolve_prompt_resources("@[bundles:model:pair]", resources, _form(_row("model:pair")), members)
    assert problems["bundles"] == [
        "the prompt references a Bundle with an audio member (model:pair), which this field's prompt cannot reference"
    ]


def test_a_removed_plugin_item_fails_the_submission(members):
    from src.features.generation.dto import PromptPair

    pair = PromptPair(positive="with @[bundles:model:clip]", negative="")
    with pytest.raises(FormBindingError, match="removed from this field \\(model:clip\\)"):
        resolve_prompt_pairs([pair], RESOURCES, _form(_row("model:face")), members)


def test_describe_leaves_plugin_source_fields_out(fake_prompt_source):
    lines = describe_prompt_resources(RESOURCES, _form(_row("model:pair")))
    assert lines and not any("bundles" in line for line in lines)


def test_served_entries_carry_the_source_label_and_badge(fake_prompt_source):
    served = with_source_specs(RESOURCES)
    assert served[0] == RESOURCES[0]
    assert served[2] == {**RESOURCES[2], "source_label": "Bundle", "source_badge": "Kit"}


def test_a_shot_citing_a_plugin_item_selects_its_members(members):
    pool, _ = build_reference_pool(["references", "bundles"], _form(_row("model:pair")), RESOURCES, members)
    derived = derive_shot_references(["@[references:b.png] and @[bundles:model:pair]"], pool, RESOURCES)
    assert derived.problems == []
    assert derived.indices == [1, 2, 4]
    assert derived.texts == ["<Picture 1> and <Picture 2> <Audio 1>"]


def test_a_shot_citing_an_unmapped_member_kind_names_the_source(members):
    resources = RESOURCES[:3]
    pool, _ = build_reference_pool(["references", "bundles"], _form(_row("model:pair")), resources, members)
    derived = derive_shot_references(["@[bundles:model:pair]"], pool, resources)
    assert "a Bundle with an audio member (model:pair)" in derived.problems[0]
