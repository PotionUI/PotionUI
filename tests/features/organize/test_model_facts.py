import json
from types import SimpleNamespace

import pytest

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.organize import errors
from src.features.organize.evaluator import compare, evaluate, sql_filter
from src.features.organize.worker import HOOK_EVENTS
from src.platform.util.ids import generate_ulid
from tests.features.organize.conftest import collection_action, rule_body

MB = 1024 * 1024


def define(key, label, field_type, model_types=(), config=None, default=None, per_user=False, admin_only=False):
    return AttributeDefinitionRepository().create(ModelAttributeDefinition(
        key=key, label=label, field_type=field_type, model_types=list(model_types), config=config or {},
        default_value=default, per_user=per_user, admin_only=admin_only,
    ))


def add_model(seed, filename, model_type, size=None, description=None, metadata=None):
    model_id = seed.model(filename, model_type)
    seed._exec(
        "UPDATE models SET file_size = ?, description = ?, model_metadata = ? WHERE id = ?",
        (size, description, json.dumps(metadata) if metadata is not None else None, model_id),
    )
    return model_id


def add_provider(seed, model_id, provider, name=None, description=None, nsfw=0):
    seed._exec(
        "INSERT INTO providers (id, model_id, provider, name, description, nsfw) VALUES (?, ?, ?, ?, ?, ?)",
        (generate_ulid(), model_id, provider, name, description, nsfw),
    )


@pytest.fixture
def lib(seed, manager):
    admin = seed.user("admin", admin=True)
    user = seed.user("u1")
    define("triggers", "Trigger words", "tags")
    define("strength", "Recommended strength", "range", ["lora"], {"min": -2, "max": 2, "step": 0.05})
    define("weight", "Weight", "number", default=1.0, per_user=True)
    define("style", "Style", "select", ["lora"],
           {"options": [{"value": "anime", "label": "Anime"}, {"value": "photo", "label": "Photo"}]})
    define("safe", "Safe for work", "checkbox", ["lora"])
    define("notes", "Notes", "text")
    define("secret", "Secret", "text", admin_only=True)
    ids = {
        "pony": add_model(seed, "ponyRealism_v21.safetensors", "checkpoint", size=6500 * MB,
                          description="Great for Portraits"),
        "anime": add_model(seed, "AnimeStyle.safetensors", "lora", size=150 * MB, metadata={
            "triggers": ["anime girl", "cel shading"], "strength": [0.6, 1.0], "style": "anime", "safe": True,
            "notes": "Use low CFG",
        }),
        "detail": add_model(seed, "detail-tweaker.safetensors", "lora", size=40 * MB,
                            metadata={"strength": [0.2, 0.4], "style": "photo", "secret": "x"}),
        "cloud": add_model(seed, "openrouter/flux-pro", "cloud"),
    }
    add_provider(seed, ids["pony"], "civitai", name="Pony Realism", description="<p>Photo models</p>")
    add_provider(seed, ids["anime"], "huggingface", name="Anime Lines")
    add_provider(seed, ids["cloud"], "openrouter", name="Flux Pro")
    for model_id in ids.values():
        seed.assign("u1", model_id)
    seed._exec("INSERT INTO user_model_meta (user_id, model_id, custom_name) VALUES ('u1', ?, 'My Detail Slider')",
               (ids["detail"],))
    seed._exec("INSERT INTO user_model_attributes (user_id, model_id, key, value) VALUES ('u1', ?, 'weight', '2.5')",
               (ids["anime"],))
    return SimpleNamespace(admin=admin, user=user, ids=ids)


def matched(manager, lib, conditions, user_id="u1", is_admin=False, match="all"):
    names = {v: k for k, v in lib.ids.items()}
    where, params, complete = sql_filter(manager.c.registry, "model", match, conditions)
    assert complete
    by_sql = manager.c.items.candidate_ids("model", user_id, where, params, is_admin=is_admin)
    every = manager.c.items.candidate_ids("model", user_id, "", [], is_admin=is_admin)
    loaded = manager.load_items("model", user_id, every, is_admin, False)
    by_python = [i.item_id for i in loaded if evaluate(manager.c.registry, match, conditions, i)]
    assert sorted(by_sql) == sorted(by_python)
    return sorted(names[i] for i in by_sql)


def attr(key, value_type, operator, value):
    return [{"fact": "attribute", "operator": operator, "value": {"key": key, "type": value_type, "value": value}}]


def text(fact, operator, value):
    return [{"fact": fact, "operator": operator, "value": value}]


CASES = [
    ("name from marketplace", text("name", "contains", "PONY"), ["pony"]),
    ("name from my rename", text("name", "contains", "my detail"), ["detail"]),
    ("name starts with", text("name", "starts_with", "anime"), ["anime"]),
    ("name ends with", text("name", "ends_with", "SLIDER"), ["detail"]),
    ("name is", text("name", "is", "flux pro"), ["cloud"]),
    ("name is not", text("name", "is_not", "pony realism"), ["anime", "cloud", "detail"]),
    ("name falls back to the file stem", text("name", "is", "detail-tweaker"), ["detail"]),
    ("filename ends with", text("filename", "ends_with", ".SAFETENSORS"), ["anime", "detail", "pony"]),
    ("filename ends with only at the end", text("filename", "ends_with", "style"), []),
    ("name starts with only at the start", text("name", "starts_with", "lines"), []),
    ("filename does not contain", text("filename", "not_contains", "tweaker"), ["anime", "cloud", "pony"]),
    ("description", text("description", "contains", "portraits"), ["pony"]),
    ("description from the marketplace", text("description", "contains", "PHOTO"), ["pony"]),
    ("description does not contain", text("description", "not_contains", "portrait"), ["anime", "cloud", "detail"]),
    ("trigger words contain", text("trigger_words", "contains", "CEL"), ["anime"]),
    ("trigger word is", text("trigger_words", "is", "anime girl"), ["anime"]),
    ("trigger word is a whole word", text("trigger_words", "is", "anime"), []),
    ("trigger words do not contain", text("trigger_words", "not_contains", "anime"), ["cloud", "detail", "pony"]),
    ("source civitai", text("source", "is", "civitai"), ["pony"]),
    ("source local", text("source", "is", "local"), ["detail"]),
    ("source cloud", text("source", "is", "cloud"), ["cloud"]),
    ("source any of", text("source", "is_any_of", ["HuggingFace", "openrouter"]), ["anime", "cloud"]),
    ("source is not local", text("source", "is_not", "local"), ["anime", "cloud", "pony"]),
    ("file size at least", text("file_size", "at_least", 1000), ["pony"]),
    ("file size at most", text("file_size", "at_most", 200), ["anime", "detail"]),
    ("range at least", attr("strength", "number", "at_least", 0.8), ["anime"]),
    ("range at most", attr("strength", "number", "at_most", 0.3), ["detail"]),
    ("range is inside", attr("strength", "number", "is", 0.7), ["anime"]),
    ("range is in a gap", attr("strength", "number", "is", 0.5), []),
    ("per-user overlay wins", attr("weight", "number", "at_least", 2), ["anime"]),
    ("default applies when unset", attr("weight", "number", "is", 1), ["cloud", "detail", "pony"]),
    ("enum is", attr("style", "enum", "is", "ANIME"), ["anime"]),
    ("enum any of", attr("style", "enum", "is_any_of", ["photo"]), ["detail"]),
    ("enum is not", attr("style", "enum", "is_not", "anime"), ["cloud", "detail", "pony"]),
    ("bool true", attr("safe", "bool", "is", True), ["anime"]),
    ("bool false", attr("safe", "bool", "is", False), ["cloud", "detail", "pony"]),
    ("text attribute", attr("notes", "text", "contains", "cfg"), ["anime"]),
    ("text attribute starts with", attr("notes", "text", "starts_with", "USE"), ["anime"]),
    ("text attribute is not", attr("notes", "text", "is_not", "use low cfg"), ["cloud", "detail", "pony"]),
    ("tags attribute", attr("triggers", "text", "contains", "shading"), ["anime"]),
]


@pytest.mark.parametrize("label,conditions,expected", CASES, ids=[c[0] for c in CASES])
def test_model_facts_match_the_same_in_python_and_sql(lib, manager, label, conditions, expected):
    assert matched(manager, lib, conditions) == expected


def test_per_user_values_belong_to_the_viewer(lib, manager):
    assert matched(manager, lib, attr("weight", "number", "at_least", 2), "admin", True) == []
    assert matched(manager, lib, text("name", "contains", "my detail"), "admin", True) == []


def test_any_match_mixes_model_facts(lib, manager):
    conditions = text("source", "is", "civitai") + attr("safe", "bool", "is", True)
    assert matched(manager, lib, conditions, match="any") == ["anime", "pony"]


def test_attribute_definitions_narrowed_from_the_type_do_not_apply(lib, manager, seed):
    seed._exec("UPDATE models SET model_metadata = ? WHERE id = ?", (json.dumps({"style": "anime"}), lib.ids["pony"]))
    assert matched(manager, lib, attr("style", "enum", "is", "anime")) == ["anime"]


def test_restricted_users_never_match_hidden_models(lib, manager, seed, visibility):
    hidden = add_model(seed, "spicy.safetensors", "lora", metadata={"style": "anime"})
    add_provider(seed, hidden, "civitai", name="Spicy Anime", nsfw=1)
    seed.assign("u1", hidden)
    visibility.restricted.add("u1")
    preview = manager.preview(lib.user, {"subject": "model", "conditions": text("name", "contains", "anime")})
    assert {s["item_id"] for s in preview["sample"]} == {lib.ids["anime"]}


def test_compare_attribute_shapes():
    actual = {"strength": [0.5, 0.9], "safe": True, "style": "anime"}
    assert compare("attribute", "is", actual, {"key": "strength", "type": "number", "value": 0.9})
    assert not compare("attribute", "at_least", actual, {"key": "strength", "type": "number", "value": 1.0})
    assert compare("attribute", "is", actual, {"key": "safe", "type": "bool", "value": True})
    assert compare("attribute", "is", {}, {"key": "safe", "type": "bool", "value": False})
    assert compare("attribute", "is_not", actual, {"key": "style", "type": "enum", "value": "photo"})
    assert not compare("attribute", "is", actual, {"key": "style", "type": "mystery", "value": "anime"})
    assert not compare("attribute", "is", actual, "strength")
    assert compare("text", "ends_with", ["Sunset.PNG"], "png")
    assert compare("text", "is_not", ["a"], "b")


def test_catalog_serves_the_new_model_facts(lib, manager):
    facts = {f["key"]: f for f in manager.catalog(lib.user, "model")["facts"]}
    assert facts["attribute"]["kind"] == "attribute"
    assert facts["attribute"]["has_options_endpoint"] is True
    assert facts["attribute"]["picker"]["operators_by_type"]["number"] == ["is", "at_least", "at_most"]
    assert facts["name"]["operators"] == ["contains", "not_contains", "starts_with", "ends_with", "is", "is_not"]
    assert facts["file_size"]["picker"]["unit"] == "MB"
    assert {o["value"] for o in facts["source"]["options"]} == {"local", "cloud"}
    prompt = {f["key"]: f for f in manager.catalog(lib.user, "generation")["facts"]}["prompt"]
    assert prompt["operators"] == ["contains", "not_contains"]


def test_attribute_options_carry_types_and_hide_admin_only(lib, manager):
    options = {o["value"]: o for o in manager.fact_options(lib.user, "attribute", "model", "", 200)}
    assert "secret" not in options
    assert options["strength"]["meta"] == {
        "type": "number", "type_label": "Number range", "field_type": "range", "model_types": ["lora"],
        "min": -2, "max": 2, "step": 0.05,
    }
    assert options["style"]["meta"]["choices"] == [{"value": "anime", "label": "Anime"}, {"value": "photo", "label": "Photo"}]
    assert options["safe"]["meta"]["type"] == "bool"
    assert options["triggers"]["meta"]["type_label"] == "Tags"
    assert "secret" in {o["value"] for o in manager.fact_options(lib.admin, "attribute", "model", "", 200)}
    assert [o["value"] for o in manager.fact_options(lib.user, "attribute", "model", "STRENGTH", 200)] == ["strength"]


def test_source_options_list_real_providers(lib, manager):
    options = manager.fact_options(lib.user, "source", "model", "", 50)
    assert options == [
        {"value": "local", "label": "Local files"},
        {"value": "cloud", "label": "Cloud catalog"},
        {"value": "civitai", "label": "CivitAI"},
        {"value": "huggingface", "label": "Hugging Face"},
        {"value": "openrouter", "label": "OpenRouter"},
    ]


def problems_of(manager, user, conditions):
    with pytest.raises(errors.OrganizeError) as raised:
        manager.preview(user, {"subject": "model", "conditions": conditions})
    return [(p["path"], p["code"]) for p in raised.value.extra["problems"]]


def test_attribute_conditions_are_validated(lib, manager):
    assert problems_of(manager, lib.user, attr("nope", "number", "is", 1)) == [("conditions.0.value", "bad_value")]
    assert problems_of(manager, lib.user, attr("strength", "number", "contains", "x")) == [
        ("conditions.0.operator", "unknown_operator")
    ]
    assert problems_of(manager, lib.user, attr("style", "enum", "is", "watercolor")) == [("conditions.0.value", "bad_value")]
    assert problems_of(manager, lib.user, attr("safe", "bool", "is", "yes")) == [("conditions.0.value", "bad_value")]
    assert problems_of(manager, lib.user, attr("secret", "text", "contains", "x")) == [("conditions.0.value", "bad_value")]
    assert problems_of(manager, lib.user, [{"fact": "attribute", "operator": "is", "value": "strength"}]) == [
        ("conditions.0.value", "bad_value")
    ]


def test_prompt_keeps_only_contains_operators(lib, manager):
    with pytest.raises(errors.OrganizeError) as raised:
        manager.preview(lib.user, {"subject": "generation", "conditions": text("prompt", "starts_with", "a")})
    assert raised.value.extra["problems"][0]["code"] == "unknown_operator"


def test_saved_attribute_conditions_take_type_and_label_from_the_definition(lib, manager, seed):
    loras = seed.model_collection("u1", "Strong LoRAs")
    rule = manager.create_rule(lib.user, rule_body(
        subject="model", conditions=attr("strength", "text", "at_least", 0.8), actions=[collection_action(loras)],
    ))
    assert rule["conditions"][0]["value"] == {
        "key": "strength", "type": "number", "label": "Recommended strength", "value": 0.8,
    }
    admin_rule = manager.create_rule(lib.admin, rule_body(
        subject="model", conditions=attr("secret", "text", "is", "x"), actions=[collection_action(
            seed.model_collection("admin", "Secret"))],
    ))
    assert admin_rule["conditions"][0]["value"]["type"] == "text"


def test_preview_counts_and_backfill_use_model_facts(lib, manager, seed):
    conditions = [{"fact": "model_type", "operator": "is", "value": "lora"}] + attr("strength", "number", "at_least", 0.8)
    preview = manager.preview(lib.user, {"subject": "model", "conditions": conditions})
    assert (preview["matched"], preview["approximate"]) == (1, False)

    strong = seed.model_collection("u1", "Strong")
    rule = manager.create_rule(lib.user, rule_body(
        subject="model", conditions=conditions + text("trigger_words", "contains", "anime"),
        actions=[collection_action(strong)],
    ))
    job = manager.start_backfill(lib.user, rule["id"])
    assert manager.get_job(lib.user, job["id"])["status"] == "completed"
    members = {r["model_id"] for r in seed.rows(
        "SELECT model_id FROM model_collection_members WHERE collection_id = ?", (strong,)
    )}
    assert members == {lib.ids["anime"]}


def test_metadata_edits_re_run_rules_that_read_metadata(lib, manager, seed):
    neon = seed.model_collection("u1", "Neon")
    manager.create_rule(lib.user, rule_body(
        subject="model", conditions=text("trigger_words", "contains", "neon"), actions=[collection_action(neon)],
    ))
    seed._exec("UPDATE models SET model_metadata = ? WHERE id = ?",
               (json.dumps({"triggers": ["neon glow"]}), lib.ids["detail"]))

    manager.handle_event("model_metadata_changed", {"model_id": lib.ids["detail"], "user_id": None})

    members = {r["model_id"] for r in seed.rows(
        "SELECT model_id FROM model_collection_members WHERE collection_id = ?", (neon,)
    )}
    assert members == {lib.ids["detail"]}


def test_metadata_edits_skip_rules_that_never_read_metadata(lib, manager, seed):
    loras = seed.model_collection("u1", "LoRAs")
    manager.create_rule(lib.user, rule_body(
        subject="model", conditions=text("model_type", "is", "lora"), actions=[collection_action(loras)],
    ))
    manager.handle_event("model_metadata_changed", {"model_id": lib.ids["detail"], "user_id": "u1"})
    assert seed.rows("SELECT model_id FROM model_collection_members WHERE collection_id = ?", (loras,)) == []


def test_worker_listens_to_metadata_edits():
    assert HOOK_EVENTS["model_index.after_update_metadata"] == ("model_metadata_changed", ("model_id", "user_id"))
