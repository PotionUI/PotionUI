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


REFMOD_RESOURCES = RESOURCES + [
    {"field": "reference_mods", "source": "refmod", "kind": "image", "token": "<Picture @>"},
    {"field": "reference_mods", "source": "refmod", "kind": "video", "token": "<Video @>"},
    {"field": "reference_mods", "source": "refmod", "kind": "audio", "token": "<Audio @>"},
]

BUNDLES = {
    "model:face": ["image"],
    "model:pair": ["image", "audio"],
    "model:clip": ["video"],
}


def _members(key):
    if key not in BUNDLES:
        raise ValueError(f"the RefMod {key} is not available: no present location")
    return BUNDLES[key]


def _mod_form(*rows):
    return {**MIXED, "reference_mods": list(rows)}


def _row(model, strength=1.0, copies=1):
    return {"model": model, "strength": strength, "copies": copies}


def _refmod_pool(form):
    return packed_reference_pool(["media_inputs", "reference_mods"], form, REFMOD_RESOURCES, _members)


def _labels(pool):
    return [
        f"mod:{item.key}:{item.kind}" if field == "reference_mods" else item["relative_path"]
        for field, item in pool
    ]


def test_refmod_members_follow_the_natives_of_their_kind_with_copies_expanded():
    pool = _refmod_pool(_mod_form(_row("model:pair", copies=2), _row("model:clip")))
    assert _labels(pool) == [
        "a.png", "b.png", "mod:model:pair:image", "mod:model:pair:image",
        "v1.mp4", "v2.mp4", "mod:model:clip:video",
        "t.wav", "mod:model:pair:audio", "mod:model:pair:audio",
    ]


def test_the_refmod_pool_order_is_the_order_pack_references_gives_the_generator():
    from src.pipelines.pipes._shared.generation.reference_order import pack_references

    class _Mod:
        def __init__(self, key, kind):
            self.key, self.kind = key, kind

    rows = [_row("model:pair", copies=2), _row("model:face"), _row("model:clip")]
    mods = [_Mod(row["model"], kind) for row in rows for _ in range(row["copies"]) for kind in BUNDLES[row["model"]]]
    natives = {kind: [item for item in MIXED["media_inputs"] if item["type"] == kind] for kind in ("image", "video", "audio")}
    packed = pack_references(natives["image"], natives["video"], natives["audio"], mods=mods)
    expected = [
        f"mod:{media.key}:{media.kind}" if isinstance(media, _Mod) else media["relative_path"]
        for _kind, media in packed
    ]
    assert _labels(_refmod_pool(_mod_form(*rows))) == expected


def test_a_refmod_at_zero_strength_is_not_in_the_pool():
    pool = _refmod_pool(_mod_form(_row("model:face", strength=0)))
    assert not any(field == "reference_mods" for field, _ in pool)


def test_citing_a_refmod_selects_every_member_and_copy_numbered_after_the_cited_natives():
    pool = _refmod_pool(_mod_form(_row("model:pair", copies=2)))
    derived = derive_shot_references(
        ["@[media_inputs:b.png] with @[reference_mods:model:pair] over @[media_inputs:t.wav]"], pool, REFMOD_RESOURCES,
    )
    assert derived.problems == []
    assert derived.indices == [1, 2, 3, 6, 7, 8]
    assert derived.kinds == ["image", "image", "image", "audio", "audio", "audio"]
    assert derived.texts == ["<Picture 1> with <Picture 2> <Picture 3> <Audio 2> <Audio 3> over <Audio 1>"]


def test_a_refmod_the_shot_does_not_cite_stays_out_of_its_subset():
    pool = _refmod_pool(_mod_form(_row("model:face")))
    derived = derive_shot_references(["@[media_inputs:a.png] walks in"], pool, REFMOD_RESOURCES)
    assert derived.indices == [0]
    assert derived.texts == ["<Picture 1> walks in"]


def test_a_shot_citing_nothing_still_gets_no_references():
    pool = _refmod_pool(_mod_form(_row("model:face")))
    derived = derive_shot_references(["a quiet street"], pool, REFMOD_RESOURCES)
    assert derived.indices == []
    assert derived.problems == []


def test_a_refmod_member_kind_the_field_does_not_map_is_a_problem():
    resources = RESOURCES + [REFMOD_RESOURCES[3]]
    pool = packed_reference_pool(["media_inputs", "reference_mods"], _mod_form(_row("model:pair")), resources, _members)
    derived = derive_shot_references(["@[reference_mods:model:pair]"], pool, resources)
    assert derived.indices == []
    assert "RefMod with an audio member (model:pair)" in derived.problems[0]


def test_an_unreadable_refmod_is_a_pool_problem_and_cannot_be_cited():
    from src.features.prompt.reference_pool import build_reference_pool

    pool, problems = build_reference_pool(
        ["media_inputs", "reference_mods"], _mod_form(_row("model:gone")), REFMOD_RESOURCES, _members,
    )
    assert problems == ["reference_mods: the RefMod model:gone is not available: no present location"]
    derived = derive_shot_references(["@[reference_mods:model:gone]"], pool, REFMOD_RESOURCES)
    assert "removed from this field (model:gone)" in derived.problems[0]


def test_without_a_resolver_a_refmod_is_a_problem_not_a_silent_drop():
    from src.features.prompt.reference_pool import build_reference_pool

    _pool, problems = build_reference_pool(
        ["media_inputs", "reference_mods"], _mod_form(_row("model:face")), REFMOD_RESOURCES, None,
    )
    assert problems == ["reference_mods: the RefMod model:face cannot be read here"]
