import pytest

from src.features.elixirs.definitions import NodeRejected, import_session
from src.features.elixirs.sessions import (
    SessionModeMissing,
    apply_overrides,
    mode_payload,
)

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"


def snapshot(preset_id=KREA, modes=None, version=4, name="Product photo"):
    data = modes if modes is not None else {
        "txt2img": {
            "selectedVariant": "custom",
            "prompt": "a bottle",
            "formData": {"quantity": 2, "seed": 7},
            "leftPanelWidth": 380,
            "layoutMode": "split",
        },
        "enhance": {"prompt": "sharper", "formData": {}},
    }
    return {"session_id": "s1", "preset_id": preset_id, "name": name, "version": version, "data": data}


def definition(node):
    return {"schema_version": 1, "nodes": [node], "edges": []}


def preset_node(**fields):
    return {"id": "n", "kind": "preset", "title": "Photo", "overrides": {}, **fields}


def test_a_mode_payload_drops_layout_and_is_a_deep_copy():
    data = snapshot()["data"]

    payload = mode_payload(data, "txt2img")
    payload["formData"]["quantity"] = 99

    assert "leftPanelWidth" not in payload and "layoutMode" not in payload
    assert payload["prompt"] == "a bottle"
    assert data["txt2img"]["formData"]["quantity"] == 2
    with pytest.raises(SessionModeMissing):
        mode_payload(data, "video")


def test_overrides_merge_form_fields_and_replace_the_rest():
    saved = {"prompt": "a", "formData": {"quantity": 2, "seed": 7}}

    effective = apply_overrides(saved, {"prompt": "b", "formData": {"seed": 1}})

    assert effective == {"prompt": "b", "formData": {"quantity": 2, "seed": 1}}
    assert saved == {"prompt": "a", "formData": {"quantity": 2, "seed": 7}}


def test_importing_a_session_copies_one_mode_and_records_where_it_came_from():
    result = import_session(definition(preset_node()), "n", snapshot(), "txt2img", None)

    node = result["nodes"][0]
    assert node["preset_id"] == KREA and node["mode"] == "txt2img"
    assert node["form_name"] == "custom"
    assert node["session_snapshot"]["formData"] == {"quantity": 2, "seed": 7}
    assert "leftPanelWidth" not in node["session_snapshot"]
    source = node["session_source"]
    assert (source["id"], source["name"], source["version"], source["unversioned"]) == ("s1", "Product photo", 4, False)


def test_a_session_without_a_version_is_labelled_unversioned():
    result = import_session(definition(preset_node()), "n", snapshot(version=None), "txt2img", None)

    assert result["nodes"][0]["session_source"]["unversioned"] is True


def test_the_mode_comes_from_the_node_or_the_only_saved_mode():
    same_preset = import_session(definition(preset_node(preset_id=KREA, mode="enhance")), "n", snapshot(), None, None)
    single = import_session(definition(preset_node()), "n", snapshot(modes={"video": {"prompt": "go"}}, preset_id=H3), None, None)

    assert same_preset["nodes"][0]["mode"] == "enhance"
    assert single["nodes"][0]["mode"] == "video"
    with pytest.raises(NodeRejected) as rejected:
        import_session(definition(preset_node()), "n", snapshot(), None, None)
    assert rejected.value.code == "mode_required"


def test_a_mode_the_session_never_saved_is_refused():
    with pytest.raises(NodeRejected) as rejected:
        import_session(definition(preset_node()), "n", snapshot(), "video", None)

    assert rejected.value.code == "session_mode_missing"


def test_local_edits_are_never_dropped_silently():
    node = preset_node(preset_id=KREA, mode="txt2img", overrides={"prompt": "mine"})

    with pytest.raises(NodeRejected) as asked:
        import_session(definition(node), "n", snapshot(), None, None)
    kept = import_session(definition(node), "n", snapshot(), None, "keep")
    discarded = import_session(definition(node), "n", snapshot(), None, "discard")

    assert asked.value.code == "node_has_local_edits" and asked.value.status == 409
    assert kept["nodes"][0]["overrides"] == {"prompt": "mine"}
    assert discarded["nodes"][0]["overrides"] == {}


def test_local_edits_cannot_follow_a_different_preset():
    node = preset_node(preset_id=KREA, mode="txt2img", overrides={"prompt": "mine"})
    other = snapshot(preset_id=H3, modes={"video": {"prompt": "go"}})

    with pytest.raises(NodeRejected) as rejected:
        import_session(definition(node), "n", other, None, "keep")
    switched = import_session(definition(node), "n", other, None, "discard")

    assert rejected.value.code == "local_edits_do_not_apply"
    assert switched["nodes"][0]["preset_id"] == H3


def test_only_preset_nodes_take_a_session():
    media = {"id": "m", "kind": "media", "assets": []}

    with pytest.raises(NodeRejected) as rejected:
        import_session(definition(media), "m", snapshot(), "txt2img", None)
    with pytest.raises(NodeRejected) as missing:
        import_session(definition(media), "nope", snapshot(), "txt2img", None)

    assert rejected.value.code == "not_a_preset_node"
    assert missing.value.status == 404


def test_importing_leaves_the_given_definition_unchanged():
    original = definition(preset_node())

    import_session(original, "n", snapshot(), "txt2img", None)

    assert "session_snapshot" not in original["nodes"][0]
