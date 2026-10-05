import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.collections.repository import CollectionRepository
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.library_items_tool import ListLibraryItemsTool
from src.features.library.repository import LibraryRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.tags.repository import TagRepository
from tests.conftest import TestDatabase


class _Safety:
    def __init__(self, restricted, states):
        self.restricted = restricted
        self.ledger = SimpleNamespace(states=lambda paths: {p: states.get(p, "unrated") for p in paths})

    def is_restricted(self, user_id):
        return self.restricted


@pytest.fixture
def library():
    test_database = TestDatabase.from_template()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        with test_database.get_cursor() as cursor:
            for user_id in ("user-1", "user-2"):
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
                    (user_id, user_id, f"{user_id}@x.com", "x"),
                )
        uploads = UploadRepository()
        fox = uploads.create(Upload(
            user_id="user-1", filename="a1.png", original_filename="fox.png", media_type="image", file_size=10,
        ))
        cat = uploads.create(Upload(user_id="user-1", filename="a2.png", original_filename="cat.png", media_type="image"))
        clip = uploads.create(Upload(user_id="user-1", filename="a3.mp4", original_filename="clip.mp4", media_type="video"))
        theirs = uploads.create(Upload(user_id="user-2", filename="b1.png", original_filename="theirs.png", media_type="image"))
        collections = CollectionRepository()
        album = collections.create("Refs", "user-1", "library")
        collections.add_upload_members(album.id, [fox.id], "user-1", "library")
        yield SimpleNamespace(
            collaborators=SimpleNamespace(repository=LibraryRepository(), tag_repository=TagRepository()),
            collections=collections, album=album, fox=fox, cat=cat, clip=clip, theirs=theirs,
        )
    test_database.close()


def _ctx(library, user_id="user-1", safety=None):
    return ToolContext(
        user_id=user_id, library_collaborators=library.collaborators,
        collection_repository=library.collections, content_safety=safety, chat_session=False,
    )


async def _items(library, **kwargs):
    context = kwargs.pop("context", None) or _ctx(library)
    result = await ListLibraryItemsTool().execute(context, **kwargs)
    assert result.success, result.error
    return json.loads(result.data)


@pytest.mark.asyncio
async def test_lists_the_owners_uploads_with_ids_paths_and_collections(library):
    payload = await _items(library)

    by_id = {item["id"]: item for item in payload["items"]}
    assert set(by_id) == {library.fox.id, library.cat.id, library.clip.id}
    assert by_id[library.fox.id]["file_name"] == "fox.png"
    assert by_id[library.fox.id]["path"] == "uploads/a1.png"
    assert by_id[library.fox.id]["size"] == 10
    assert by_id[library.fox.id]["collections"] == [{"id": library.album.id, "name": "Refs"}]
    assert by_id[library.cat.id]["collections"] == []


@pytest.mark.asyncio
async def test_another_user_never_sees_these_uploads(library):
    payload = await _items(library, context=_ctx(library, user_id="user-2"))

    assert [item["id"] for item in payload["items"]] == [library.theirs.id]


@pytest.mark.asyncio
async def test_filters_and_paging(library):
    videos = await _items(library, media_type="video")
    searched = await _items(library, search="fox")
    in_album = await _items(library, collection_id=library.album.id)
    first = await _items(library, limit=2)

    assert [i["id"] for i in videos["items"]] == [library.clip.id]
    assert [i["id"] for i in searched["items"]] == [library.fox.id]
    assert [i["id"] for i in in_album["items"]] == [library.fox.id]
    assert len(first["items"]) == 2 and first["has_more"] is True


@pytest.mark.asyncio
async def test_a_restricted_viewer_gets_safe_items_in_full_unrated_ones_without_details_and_no_flagged_ones(library):
    safety = _Safety(True, {"uploads/a1.png": "safe", "uploads/a2.png": "flagged"})

    payload = await _items(library, context=_ctx(library, safety=safety))

    by_id = {item["id"]: item for item in payload["items"]}
    assert set(by_id) == {library.fox.id, library.clip.id}
    assert by_id[library.fox.id]["file_name"] == "fox.png"
    assert by_id[library.clip.id] == {"id": library.clip.id, "media_type": "video", "content_state": "unrated"}
    assert "cat.png" not in json.dumps(payload)
    assert "1 item(s) are hidden" in payload["note"]


@pytest.mark.asyncio
async def test_an_unrestricted_viewer_sees_flagged_items(library):
    safety = _Safety(False, {"uploads/a2.png": "flagged"})

    payload = await _items(library, context=_ctx(library, safety=safety))

    assert library.cat.id in {item["id"] for item in payload["items"]}
    assert "note" not in payload


@pytest.mark.asyncio
async def test_an_unknown_media_type_is_a_plain_error(library):
    result = await ListLibraryItemsTool().execute(_ctx(library), media_type="mesh")

    assert result.success is False
    assert "image, video, audio" in result.error
