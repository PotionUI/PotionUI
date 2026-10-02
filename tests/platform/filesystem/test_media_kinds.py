from src.platform.filesystem.media_kinds import (
    items_of_kind,
    kind_from_declared,
    kind_from_filename,
    media_item_kind,
)


def test_kind_from_declared_takes_category_of_mime_or_bare_kind():
    assert kind_from_declared("image") == "image"
    assert kind_from_declared(" Video/mp4 ") == "video"
    assert kind_from_declared("audio/mpeg") == "audio"
    assert kind_from_declared("application/pdf") is None
    assert kind_from_declared(None) is None
    assert kind_from_declared(3) is None


def test_kind_from_filename_by_extension():
    assert kind_from_filename("a/b.PNG") == "image"
    assert kind_from_filename("clip.mov") == "video"
    assert kind_from_filename("voice.opus") == "audio"
    assert kind_from_filename("/api/media/x.mp4?v=2") == "video"
    assert kind_from_filename("noext") is None
    assert kind_from_filename("a.bin") is None
    assert kind_from_filename(None) is None


def test_media_item_kind_prefers_declared_type_over_extension():
    assert media_item_kind({"type": "audio", "path": "x.png"}) == "audio"
    assert media_item_kind({"file_type": "video/mp4", "path": "x.png"}) == "video"
    assert media_item_kind({"media_type": "image"}) == "image"


def test_media_item_kind_falls_back_to_name_keys_in_order():
    assert media_item_kind({"name": "a.mp4", "path": "b.png"}) == "video"
    assert media_item_kind({"relative_path": "u/a.mp3"}) == "audio"
    assert media_item_kind({"url": "/api/media/u/a.webp"}) == "image"
    assert media_item_kind({"type": "bogus", "path": "a.png"}) == "image"


def test_media_item_kind_bare_strings_and_unknowns():
    assert media_item_kind("u/a.mp4") == "video"
    assert media_item_kind("u/a.bin") is None
    assert media_item_kind({"path": "a.bin"}) is None
    assert media_item_kind(None) is None
    assert media_item_kind(5) is None


def test_items_of_kind_filters_and_preserves_order():
    items = [
        {"path": "1.png", "type": "image"},
        "2.mp4",
        {"path": "3.png"},
        {"path": "4.mp3", "type": "audio"},
    ]
    assert items_of_kind(items, "image") == [{"path": "1.png", "type": "image"}, {"path": "3.png"}]
    assert items_of_kind(tuple(items), "video") == ["2.mp4"]


def test_items_of_kind_empty_and_scalar_inputs():
    assert items_of_kind(None, "image") == []
    assert items_of_kind("", "image") == []
    assert items_of_kind([], "image") == []
    assert items_of_kind("a.png", "image") == ["a.png"]
    assert items_of_kind({"type": "video", "path": "a.mp4"}, "image") == []
