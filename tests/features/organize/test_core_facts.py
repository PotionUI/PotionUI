import pytest

from src.features.organize.core_facts import aspect_of, prompt_texts
from src.features.organize.evaluator import evaluate, sql_filter


@pytest.fixture
def library(seed, manager):
    user = seed.user("u1")
    seed.user("u2")
    krea = seed.model("krea.safetensors", "checkpoint")
    flux = seed.model("flux.safetensors", "checkpoint")
    lora = seed.model("style.safetensors", "lora")
    ids = {
        "krea_wide": seed.generation("u1", models=[krea, lora], prompt="A Sunset over hills"),
        "krea_square": seed.generation("u1", models=[krea], files=((1024, 1024, "IMAGE", None),), prompt="cat"),
        "flux_tall": seed.generation("u1", preset_id="flux-dev", mode="img2img", linked_models=[flux],
                                     files=((832, 1216, "IMAGE", None),), prompt="portrait"),
        "video": seed.generation("u1", files=((1280, 720, "VIDEO", 8.0),), mode="txt2vid", prompt="waves"),
        "audio": seed.generation("u1", files=((None, None, "AUDIO", 30.0),), prompt="song"),
        "nested": seed.generation("u1", form_extra={"segments": [{"prompt": "a SUNSET dream"}]}, prompt="x"),
        "theirs": seed.generation("u2", models=[krea]),
    }
    seed.tag_generation("u1", ids["krea_square"], "Cat")
    return {"user": user, "krea": krea, "flux": flux, "lora": lora, "ids": ids}


def matches_python(manager, conditions, match="all"):
    candidates = manager.c.items.candidate_ids("generation", "u1", "", [])
    items = manager.load_items("generation", "u1", candidates, False, False)
    return {i.item_id for i in items if evaluate(manager.c.registry, match, conditions, i)}


def matches_sql(manager, conditions, match="all"):
    where, params, complete = sql_filter(manager.c.registry, "generation", match, conditions)
    assert complete
    return set(manager.c.items.candidate_ids("generation", "u1", where, params))


def names(library, ids):
    reverse = {v: k for k, v in library["ids"].items()}
    return sorted(reverse[i] for i in ids)


CASES = [
    ("model is krea", lambda l: [{"fact": "model", "operator": "is", "value": l["krea"]}], ["krea_square", "krea_wide"]),
    ("model linked only by the models table", lambda l: [{"fact": "model", "operator": "is", "value": l["flux"]}], ["flux_tall"]),
    ("lora used", lambda l: [{"fact": "lora", "operator": "is_any_of", "value": [l["lora"]]}], ["krea_wide"]),
    ("model is not krea", lambda l: [{"fact": "model", "operator": "is_not", "value": l["krea"]}],
     ["audio", "flux_tall", "nested", "video"]),
    ("preset", lambda l: [{"fact": "preset", "operator": "is", "value": "flux-dev"}], ["flux_tall"]),
    ("preset is not", lambda l: [{"fact": "preset", "operator": "is_not", "value": "krea-2"}], ["flux_tall"]),
    ("mode", lambda l: [{"fact": "mode", "operator": "is_any_of", "value": ["img2img", "TXT2VID"]}], ["flux_tall", "video"]),
    ("media kind video", lambda l: [{"fact": "media_kind", "operator": "is", "value": "video"}], ["video"]),
    ("media kind not image", lambda l: [{"fact": "media_kind", "operator": "is_not", "value": "image"}], ["audio", "video"]),
    ("resolution is", lambda l: [{"fact": "resolution", "operator": "is", "value": {"width": 1344, "height": 768}}],
     ["krea_wide", "nested"]),
    ("resolution at least", lambda l: [{"fact": "resolution", "operator": "at_least", "value": {"width": 1280, "height": 720}}],
     ["krea_wide", "nested", "video"]),
    ("aspect landscape", lambda l: [{"fact": "aspect", "operator": "is", "value": "landscape"}],
     ["krea_wide", "nested", "video"]),
    ("aspect portrait", lambda l: [{"fact": "aspect", "operator": "is", "value": "portrait"}], ["flux_tall"]),
    ("aspect square or landscape", lambda l: [{"fact": "aspect", "operator": "is_any_of", "value": ["square", "landscape"]}],
     ["krea_square", "krea_wide", "nested", "video"]),
    ("duration at least", lambda l: [{"fact": "duration", "operator": "at_least", "value": 10}], ["audio"]),
    ("duration at most", lambda l: [{"fact": "duration", "operator": "at_most", "value": 10}], ["video"]),
    ("prompt contains", lambda l: [{"fact": "prompt", "operator": "contains", "value": "sunset"}], ["krea_wide", "nested"]),
    ("prompt ignores the negative prompt", lambda l: [{"fact": "prompt", "operator": "contains", "value": "dog"}], []),
    ("prompt does not contain", lambda l: [{"fact": "prompt", "operator": "not_contains", "value": "sunset"}],
     ["audio", "flux_tall", "krea_square", "video"]),
    ("tags has", lambda l: [{"fact": "tags", "operator": "has", "value": ["cat"]}], ["krea_square"]),
    ("tags has not", lambda l: [{"fact": "tags", "operator": "has_not", "value": ["cat"]}],
     ["audio", "flux_tall", "krea_wide", "nested", "video"]),
]


@pytest.mark.parametrize("label,conditions,expected", CASES, ids=[c[0] for c in CASES])
def test_python_and_sql_agree_on_generation_facts(library, manager, label, conditions, expected):
    built = conditions(library)

    assert names(library, matches_python(manager, built)) == expected
    assert names(library, matches_sql(manager, built)) == expected


def test_any_matching_combines_conditions(library, manager):
    built = [{"fact": "media_kind", "operator": "is", "value": "audio"},
             {"fact": "aspect", "operator": "is", "value": "portrait"}]

    assert names(library, matches_sql(manager, built, "any")) == ["audio", "flux_tall"]
    assert names(library, matches_python(manager, built, "any")) == ["audio", "flux_tall"]


def test_candidates_never_include_another_users_items(library, manager):
    assert library["ids"]["theirs"] not in manager.c.items.candidate_ids("generation", "u1", "", [])


def test_upload_facts(seed, manager):
    seed.user("u1")
    wide = seed.upload("u1", width=1920, height=1080, filename="Beach.PNG")
    clip = seed.upload("u1", media_type="video", width=720, height=1280, filename="clip.mp4", duration=12.0)
    seed.upload("u1", filename="mask.png", purpose="derived_artifact")
    cases = [
        ([{"fact": "media_kind", "operator": "is", "value": "video"}], {clip}),
        ([{"fact": "aspect", "operator": "is", "value": "landscape"}], {wide}),
        ([{"fact": "filename", "operator": "contains", "value": "beach"}], {wide}),
        ([{"fact": "duration", "operator": "at_least", "value": 10}], {clip}),
        ([{"fact": "resolution", "operator": "at_least", "value": {"width": 1920, "height": 1080}}], {wide}),
    ]
    for conditions, expected in cases:
        where, params, complete = sql_filter(manager.c.registry, "upload", "all", conditions)
        assert complete
        assert set(manager.c.items.candidate_ids("upload", "u1", where, params)) == expected
        all_ids = manager.c.items.candidate_ids("upload", "u1", "", [])
        loaded = manager.load_items("upload", "u1", all_ids, False, False)
        assert {i.item_id for i in loaded if evaluate(manager.c.registry, "all", conditions, i)} == expected


def test_model_facts(seed, manager):
    seed.user("admin", admin=True)
    sdxl = seed.model("a.safetensors", "checkpoint", family="SDXL")
    flux = seed.model("b.safetensors", "lora", family="flux")
    seed.tag_model(flux, "style")
    cases = [
        ([{"fact": "base_model", "operator": "is", "value": "sdxl"}], {sdxl}),
        ([{"fact": "model_type", "operator": "is", "value": "lora"}], {flux}),
        ([{"fact": "tags", "operator": "has", "value": ["Style"]}], {flux}),
        ([{"fact": "base_model", "operator": "is_not", "value": "sdxl"}], {flux}),
    ]
    for conditions, expected in cases:
        where, params, _ = sql_filter(manager.c.registry, "model", "all", conditions)
        assert set(manager.c.items.candidate_ids("model", "admin", where, params, is_admin=True)) == expected
        loaded = manager.load_items("model", "admin", [sdxl, flux], True, False)
        assert {i.item_id for i in loaded if evaluate(manager.c.registry, "all", conditions, i)} == expected


def test_aspect_of_treats_nearly_square_as_square():
    assert aspect_of(1024, 1024) == "square"
    assert aspect_of(1024, 1010) == "square"
    assert aspect_of(1344, 768) == "landscape"
    assert aspect_of(768, 1344) == "portrait"
    assert aspect_of(None, 10) is None


def test_prompt_texts_skip_negative_prompts():
    form = {"prompt": "a", "negative_prompt": "b", "nested": {"positive_prompt": "c"}, "seed": 1}
    assert prompt_texts(form) == ["a", "c"]


def test_catalog_lists_core_facts_and_hides_admin_actions_from_users(seed, manager, registry):
    from src.platform.plugins.organize import OrganizeActionDefinition

    registry.register_action(OrganizeActionDefinition(
        key="example.webhook", label="Send a webhook", subjects=("generation",), apply=lambda *a: [],
        requires_admin=True, source="example",
    ))
    user = seed.user("u1")
    admin = seed.user("admin", admin=True)

    catalog = manager.catalog(user, "generation")
    facts = {f["key"]: f for f in catalog["facts"]}
    assert {"model", "lora", "preset", "mode", "media_kind", "resolution", "aspect", "duration", "prompt", "tags"} <= set(facts)
    assert "model_type" not in facts
    assert facts["model"]["picker"]["model_types"] == ["checkpoint", "diffusion_model", "unet"]
    assert facts["preset"]["has_options_endpoint"] is True
    assert {a["key"] for a in catalog["actions"]} == {"add_to_collection", "add_tags"}
    assert "example.webhook" in {a["key"] for a in manager.catalog(admin, "generation")["actions"]}
    assert {a["key"] for a in manager.catalog(user, "model")["actions"]} == {"add_to_collection"}


def test_fact_options(seed, manager):
    user = seed.user("u1")
    seed.generation("u1", mode="weird-mode")
    seed.tag_generation("u1", seed.generation("u1"), "Sunset")

    assert manager.fact_options(user, "preset", "generation", "flux", 50) == [{"value": "flux-dev", "label": "Flux Dev"}]
    mode_options = manager.fact_options(user, "mode", "generation", "", 50)
    assert {"value": "weird-mode", "label": "Weird Mode"} in mode_options
    assert {"value": "txt2img", "label": "Text to Image"} in mode_options
    assert manager.fact_options(user, "mode", "generation", "image to image", 50) == [{"value": "img2img", "label": "Image to Image"}]
    assert manager.fact_options(user, "tags", "generation", "sun", 50) == [{"value": "Sunset", "label": "Sunset"}]
    assert manager.fact_options(user, "aspect", "generation", "port", 50) == [{"value": "portrait", "label": "Portrait"}]
