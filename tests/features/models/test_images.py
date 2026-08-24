import pytest
from unittest.mock import patch

from src.features.models.images import generate_missing_thumbnails_from_videos


@pytest.mark.asyncio
async def test_generate_missing_thumbnails_from_videos_imports_its_collaborator():
    """Regression: the function used to import a non-existent
    `video_thumbnail_service` module and the ModuleNotFoundError was
    swallowed by the caller's bare except, silently no-opping thumbnail
    backfill."""
    with patch("src.features.models.repository.model_repo.get_all", return_value=[]):
        result = await generate_missing_thumbnails_from_videos()

    assert result["processed"] == 0
