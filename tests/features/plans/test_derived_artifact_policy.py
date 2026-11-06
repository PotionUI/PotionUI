from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.features.media import validators
from src.features.media.routes import MediaController
from src.features.plans.errors import LimitExceeded
from tests.features.plans.test_enforcement import limit_storage, media_store


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(validators, "DERIVED_ARTIFACT_MAX_BYTES", 10)


def test_a_helper_file_over_the_cap_is_refused_by_its_announced_size(seed, plans, tmp_path, small_cap):
    seed.user("u1")
    store, _ = media_store(tmp_path, plans.guard)
    store.check_upload_size("u1", 10, "derived_artifact", "image/png")
    with pytest.raises(ValueError):
        store.check_upload_size("u1", 11, "derived_artifact", "image/png")
    store.check_upload_size("u1", 11, "user_upload", "image/png")


@pytest.mark.asyncio
async def test_a_helper_file_over_the_cap_is_refused_by_its_bytes_before_any_write(seed, plans, tmp_path, small_cap):
    seed.user("u1")
    store, driver = media_store(tmp_path, plans.guard)
    with pytest.raises(ValueError):
        await store.upload_media(b"x" * 11, "big.png", "image/png", user_id="u1", purpose="derived_artifact")
    driver.put_bytes.assert_not_called()


@pytest.mark.parametrize("content_type", ["text/plain", "application/octet-stream", "model/gltf-binary", ""])
@pytest.mark.asyncio
async def test_a_helper_file_must_be_an_image_audio_or_video(seed, plans, tmp_path, content_type):
    seed.user("u1")
    store, driver = media_store(tmp_path, plans.guard)
    with pytest.raises(ValueError):
        await store.upload_media(b"abc", "a.bin", content_type, user_id="u1", purpose="derived_artifact")
    driver.put_bytes.assert_not_called()


@pytest.mark.asyncio
async def test_a_helper_file_counts_toward_the_storage_quota(seed, plans, tmp_path):
    seed.user("u1")
    limit_storage(plans, 10)
    seed.upload("u1", 8, purpose="derived_artifact")
    store, driver = media_store(tmp_path, plans.guard)
    with pytest.raises(LimitExceeded):
        await store.upload_media(b"abc", "a.png", "image/png", user_id="u1", purpose="derived_artifact")
    driver.put_bytes.assert_not_called()


@pytest.mark.asyncio
async def test_the_media_upload_route_refuses_a_helper_file_that_is_not_media_before_reading_it(seed, plans, tmp_path):
    seed.user("u1")
    store, driver = media_store(tmp_path, plans.guard)
    file = Mock(filename="notes.png", content_type="text/plain", size=3, read=AsyncMock(return_value=b"abc"))

    with pytest.raises(HTTPException) as refused:
        await MediaController(store, Mock()).upload_media(file, Mock(id="u1"), "derived_artifact")

    assert refused.value.status_code == 400
    file.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_media_upload_route_refuses_an_oversize_helper_file_before_reading_it(seed, plans, tmp_path, small_cap):
    seed.user("u1")
    store, driver = media_store(tmp_path, plans.guard)
    file = Mock(filename="mask.png", content_type="image/png", size=11, read=AsyncMock(return_value=b"x" * 11))

    with pytest.raises(HTTPException) as refused:
        await MediaController(store, Mock()).upload_media(file, Mock(id="u1"), "derived_artifact")

    assert refused.value.status_code == 400
    file.read.assert_not_awaited()
    driver.put_bytes.assert_not_called()
