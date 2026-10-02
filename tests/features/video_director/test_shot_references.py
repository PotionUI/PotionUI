from src.features.video_director.shot_references import derive_shot_references, packed_reference_pool

RESOURCES = [
    {"field": "media_inputs", "kind": "image", "token": "<Picture @>"},
    {"field": "media_inputs", "kind": "video", "token": "<Video @>"},
    {"field": "media_inputs", "kind": "audio", "token": "<Audio @>"},
]


def _item(path, kind):
    return {"relative_path": path, "type": kind}


MIXED = {
    "media_inputs": [
        _item("a.png", "image"),
        _item("v1.mp4", "video"),
        _item("b.png", "image"),
        _item("t.wav", "audio"),
        _item("v2.mp4", "video"),
    ],
}


def _names(pool):
    return [item["relative_path"] for _, item in pool]


def test_pool_orders_a_mixed_field_by_kind_keeping_list_order_within_a_kind():
    pool = packed_reference_pool(["media_inputs"], MIXED)
    assert _names(pool) == ["a.png", "b.png", "v1.mp4", "v2.mp4", "t.wav"]


def test_pool_of_three_single_kind_fields_keeps_field_order():
    form = {
        "media_inputs": [_item("a.png", "image")],
        "clips": [_item("v.mp4", "video")],
        "tracks": [_item("t.wav", "audio")],
    }
    pool = packed_reference_pool(["media_inputs", "clips", "tracks"], form)
    assert [field for field, _ in pool] == ["media_inputs", "clips", "tracks"]


def test_pool_puts_items_of_unknown_kind_last_and_skips_keyless_items():
    form = {"media_inputs": ["mystery", {"name": "x"}, _item("a.png", "image")]}
    assert [item if isinstance(item, str) else item["relative_path"] for _, item in packed_reference_pool(["media_inputs"], form)] == [
        "a.png", "mystery",
    ]


def test_derived_indices_point_into_the_kind_ordered_pool_and_numbers_restart_per_kind():
    pool = packed_reference_pool(["media_inputs"], MIXED)
    derived = derive_shot_references(
        ["@[media_inputs:v2.mp4] @[media_inputs:b.png] @[media_inputs:v1.mp4] @[media_inputs:t.wav]"], pool, RESOURCES,
    )
    assert derived.problems == []
    assert derived.indices == [1, 2, 3, 4]
    assert derived.kinds == ["image", "video", "video", "audio"]
    assert derived.texts == ["<Video 2> <Picture 1> <Video 1> <Audio 1>"]


def test_a_shot_subset_renumbers_from_one_within_each_kind():
    pool = packed_reference_pool(["media_inputs"], MIXED)
    derived = derive_shot_references(["@[media_inputs:b.png] @[media_inputs:v2.mp4]"], pool, RESOURCES)
    assert derived.texts == ["<Picture 1> <Video 1>"]
    assert derived.kinds == ["image", "video"]


def test_an_item_of_a_kind_the_field_does_not_map_is_a_problem():
    pool = packed_reference_pool(["media_inputs"], MIXED)
    derived = derive_shot_references(["@[media_inputs:t.wav]"], pool, RESOURCES[:2])
    assert derived.indices == []
    assert "audio item" in derived.problems[0]
    assert derived.texts == ["@[media_inputs:t.wav]"]


def test_a_removed_item_is_a_problem():
    pool = packed_reference_pool(["media_inputs"], MIXED)
    derived = derive_shot_references(["@[media_inputs:gone.png]"], pool, RESOURCES)
    assert "removed from this field (gone.png)" in derived.problems[0]
