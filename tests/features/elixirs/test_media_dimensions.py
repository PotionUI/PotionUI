import pytest
from pydantic import ValidationError

from src.features.elixirs.dto import MAX_STORED_ASSETS, AssetEntry, MediaNode
from src.features.elixirs.limits import Limits
from src.features.elixirs.media import resolve_assets


def library(item_id):
    return {"kind": "library", "item_id": item_id}


def test_a_library_file_keeps_the_dimensions_its_upload_reports():
    def resolver(user, ref):
        return {"value": "uploads/a.png", "media_type": "image", "original_filename": "a.png", "width": 640, "height": 480}

    [entry] = resolve_assets(None, [library("u1")], {}, resolver)

    assert (entry["width"], entry["height"]) == (640, 480)


def test_dimensions_sent_by_the_client_are_not_trusted():
    def resolver(user, ref):
        return {"value": "uploads/a.png", "media_type": "image", "original_filename": "a.png"}

    [entry] = resolve_assets(None, [{**library("u1"), "width": 9, "height": 9}], {}, resolver)

    assert "width" not in entry and "height" not in entry


def test_a_known_file_keeps_its_stored_dimensions_without_a_lookup():
    def resolver(user, ref):
        raise AssertionError("known files are not resolved again")

    known = {"library:u1": {"media_type": "image", "original_filename": "a.png", "preview_url": None, "width": 10, "height": 20}}

    [entry] = resolve_assets(None, [library("u1")], known, resolver)

    assert (entry["width"], entry["height"]) == (10, 20)


def test_a_source_can_hold_a_thousand_files_and_the_default_limit_allows_them():
    assets = [AssetEntry(kind="library", item_id=f"u{index}") for index in range(1000)]

    node = MediaNode(id="m", kind="media", assets=assets)

    assert len(node.assets) == 1000
    assert Limits().max_media_per_source == 1000 <= MAX_STORED_ASSETS


def test_dimensions_must_be_positive():
    with pytest.raises(ValidationError):
        AssetEntry(kind="library", item_id="u1", width=0, height=5)
