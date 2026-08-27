import pytest
import sys
import os
import tempfile
import shutil
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, Response
from PIL import Image
import io

# Add the src directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.features.media.routes import MediaController
from src.features.media import MediaStore
from src.features.media.dto import MediaResult
from src.features.media import UnsupportedSizeError


class TestMediaController:
    """Tests for MediaController, focusing on preset file serving"""

    @pytest.fixture
    def mock_media_manager(self):
        """Mock media manager"""
        manager = Mock(spec=MediaStore)
        return manager

    @pytest.fixture
    def controller(self, mock_media_manager):
        """Create MediaController instance"""
        return MediaController(mock_media_manager)

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def sample_image(self):
        """Create a sample test image"""
        img = Image.new('RGB', (100, 100), color='blue')
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        return img_buffer.getvalue()

    def create_test_image_file(self, path: Path, image_data: bytes):
        """Helper to create test image file"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(image_data)

    # Test serve_preset_file method
    @pytest.mark.asyncio
    async def test_serve_preset_file_success(self, controller, mock_media_manager, temp_dir, sample_image):
        """Test serving preset file successfully"""
        preset_id = "test_preset_id"
        file_path = "files/carousel/test_image.png"

        # Create test file
        test_file = temp_dir / "test_image.png"
        self.create_test_image_file(test_file, sample_image)

        # Setup mock media manager
        mock_media_manager.get_preset_file.return_value = MediaResult(
            file_path=str(test_file),
            media_type='image/png',
            headers={
                "Cache-Control": "public, max-age=3600",
                "ETag": f'"{preset_id}-{file_path}"'
            },
            use_streaming=True
        )

        # Test serving the file
        result = await controller.serve_preset_file(preset_id, file_path)

        assert isinstance(result, StreamingResponse)
        assert result.media_type == 'image/png'
        mock_media_manager.get_preset_file.assert_called_once_with(preset_id, file_path, None)

    @pytest.mark.asyncio
    async def test_serve_preset_file_not_found_preset(self, controller, mock_media_manager):
        """Test serving file when preset doesn't exist"""
        preset_id = "nonexistent_preset"
        file_path = "public/test.png"

        mock_media_manager.get_preset_file.side_effect = ValueError("Preset not found: nonexistent_preset")

        with pytest.raises(HTTPException) as exc:
            await controller.serve_preset_file(preset_id, file_path)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_serve_preset_file_not_found_file(self, controller, mock_media_manager):
        """Test serving file when file doesn't exist"""
        preset_id = "test_preset_id"
        file_path = "public/nonexistent.png"

        mock_media_manager.get_preset_file.side_effect = ValueError("Preset file not found")

        with pytest.raises(HTTPException) as exc:
            await controller.serve_preset_file(preset_id, file_path)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_serve_preset_file_rejection_is_indistinguishable(
        self, controller, mock_media_manager
    ):
        """A disallowed type and an absent file must look identical to a caller."""
        mock_media_manager.get_preset_file.side_effect = ValueError("Preset file not found")

        with pytest.raises(HTTPException) as exc:
            await controller.serve_preset_file("test_preset_id", "preset.yml")

        assert exc.value.status_code == 404
        assert exc.value.detail == "Preset file not found"

    @pytest.mark.asyncio
    async def test_serve_preset_file_unknown_size_is_400(self, controller, mock_media_manager):
        """An unknown ?size= is a client error, distinct from a missing file."""
        mock_media_manager.get_preset_file.side_effect = UnsupportedSizeError("Unknown size 'huge'")

        with pytest.raises(HTTPException) as exc:
            await controller.serve_preset_file("test_preset_id", "public/a.png", size="huge")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_serve_preset_file_conditional_request_304(
        self, controller, mock_media_manager, temp_dir, sample_image
    ):
        """A matching If-None-Match skips the body."""
        test_file = temp_dir / "test_image.png"
        self.create_test_image_file(test_file, sample_image)
        etag = '"p-public/a.png-123"'
        mock_media_manager.get_preset_file.return_value = MediaResult(
            file_path=str(test_file),
            media_type='image/png',
            headers={"ETag": etag},
            use_streaming=True,
        )
        request = Mock()
        request.headers = {"if-none-match": etag}

        result = await controller.serve_preset_file("p", "public/a.png", None, request)
        assert result.status_code == 304

    @pytest.mark.asyncio
    async def test_serve_preset_file_path_traversal(self, controller, mock_media_manager):
        """Test security: prevent path traversal attacks"""
        preset_id = "test_preset_id"
        file_path = "../../../../../../etc/passwd"

        mock_media_manager.get_preset_file.side_effect = ValueError("Access denied - path traversal detected")

        with pytest.raises(HTTPException) as exc:
            await controller.serve_preset_file(preset_id, file_path)
        assert exc.value.status_code == 404
        # The traversal detail must not leak back to the caller.
        assert "traversal" not in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_serve_preset_file_thumbnail(self, controller, mock_media_manager, sample_image):
        """Test serving preset file with thumbnail generation"""
        preset_id = "test_preset_id"
        file_path = "files/carousel/test_image.png"

        # Setup mock media manager to return thumbnail content
        mock_media_manager.get_preset_file.return_value = MediaResult(
            content=sample_image,
            media_type='image/png',
            headers={
                "Cache-Control": "public, max-age=3600",
                "ETag": f'"{preset_id}-{file_path}-thumbnail"'
            },
            use_streaming=False
        )

        # Test serving with thumbnail
        result = await controller.serve_preset_file(preset_id, file_path, size='thumbnail')

        assert isinstance(result, Response)
        assert result.media_type == 'image/png'
        mock_media_manager.get_preset_file.assert_called_once_with(preset_id, file_path, 'thumbnail')

    @pytest.mark.asyncio
    async def test_serve_preset_file_error(self, controller, mock_media_manager):
        """Test error when preset loader has issue"""
        mock_media_manager.get_preset_file.side_effect = ValueError("Preset loader not configured")

        with pytest.raises(HTTPException) as exc:
            await controller.serve_preset_file("test_id", "public/test.png")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_serve_preset_file_media_types(self, controller, mock_media_manager, temp_dir, sample_image):
        """Test correct media type detection for different file types"""
        preset_id = "test_preset_id"

        # Test different file types
        test_cases = [
            ('test.jpg', 'image/jpeg'),
            ('test.jpeg', 'image/jpeg'),
            ('test.png', 'image/png'),
            ('test.gif', 'image/gif'),
            ('test.webp', 'image/webp'),
        ]

        for filename, expected_media_type in test_cases:
            file_path = f'files/{filename}'
            test_file = temp_dir / filename
            self.create_test_image_file(test_file, sample_image)

            mock_media_manager.get_preset_file.return_value = MediaResult(
                file_path=str(test_file),
                media_type=expected_media_type,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "ETag": f'"{preset_id}-{file_path}"'
                },
                use_streaming=True
            )

            result = await controller.serve_preset_file(preset_id, file_path)

            assert isinstance(result, StreamingResponse)
            assert result.media_type == expected_media_type

    # Test serve_generation_media method
    @pytest.mark.asyncio
    async def test_serve_generation_media_success(self, controller, mock_media_manager, temp_dir, sample_image):
        """Test serving generation media successfully"""
        generation_id = "gen123"
        filename = "output.png"

        test_file = temp_dir / filename
        self.create_test_image_file(test_file, sample_image)

        mock_media_manager.get_generation_media.return_value = MediaResult(
            file_path=str(test_file),
            media_type='image/png',
            headers={
                "Cache-Control": "public, max-age=3600",
                "ETag": f'"{generation_id}-{filename}"'
            },
            use_streaming=True
        )

        result = await controller.serve_generation_media(generation_id, filename)

        assert isinstance(result, StreamingResponse)
        assert result.media_type == 'image/png'

    @pytest.mark.asyncio
    async def test_serve_generation_media_not_found(self, controller, mock_media_manager):
        """Test serving generation media when not found"""
        mock_media_manager.get_generation_media.side_effect = ValueError("Generation not found")

        result = await controller.serve_generation_media("nonexistent", "file.png")

        # Should return APIResponse error
        assert result.success is False
        assert result.error == "not_found"

    @pytest.mark.asyncio
    async def test_serve_generation_media_conditional_request(self, controller, mock_media_manager, temp_dir, sample_image):
        """Test conditional request returns 304"""
        generation_id = "gen123"
        filename = "output.png"

        test_file = temp_dir / filename
        self.create_test_image_file(test_file, sample_image)

        etag = f'"{generation_id}-{filename}"'
        mock_media_manager.get_generation_media.return_value = MediaResult(
            file_path=str(test_file),
            media_type='image/png',
            headers={
                "ETag": etag
            },
            use_streaming=True
        )

        # Create mock request with If-None-Match header
        mock_request = Mock()
        mock_request.headers.get.return_value = etag

        result = await controller.serve_generation_media(
            generation_id, filename, request=mock_request
        )

        assert result.status_code == 304

    # Test upload_media method
    @pytest.mark.asyncio
    async def test_upload_media_success(self, controller, mock_media_manager, sample_image):
        """Test uploading media successfully"""
        from src.features.media.dto import UploadResult

        mock_file = Mock()
        mock_file.filename = "test.png"
        mock_file.content_type = "image/png"
        mock_file.read = AsyncMock(return_value=sample_image)

        mock_current_user = Mock()
        mock_current_user.id = "user123"

        mock_media_manager.upload_media = AsyncMock(return_value=UploadResult(
            path="/tmp/uploads/unique-id.png",
            relative_path="uploads/unique-id.png",
            filename="unique-id.png",
            size=len(sample_image),
            url="/api/media/uploads/unique-id.png"
        ))

        result = await controller.upload_media(mock_file, mock_current_user)

        assert result.success is True
        assert "path" in result.data

    # Test get_upload_info method
    @pytest.mark.asyncio
    async def test_get_upload_info_success(self, controller, mock_media_manager):
        """Test fetching metadata for an already-uploaded file (addendum)."""
        from src.features.media.dto import UploadInfoResult

        mock_current_user = Mock()
        mock_current_user.id = "user123"

        mock_media_manager.get_upload_info.return_value = UploadInfoResult(
            filename="unique-id.png",
            size=2048,
            width=1920,
            height=1080,
            duration_seconds=None,
            fps=None
        )

        result = await controller.get_upload_info("unique-id.png", mock_current_user)

        assert result.success is True
        assert result.data["width"] == 1920
        assert result.data["height"] == 1080
        mock_media_manager.get_upload_info.assert_called_once_with("unique-id.png", "user123")

    @pytest.mark.asyncio
    async def test_get_upload_info_not_found(self, controller, mock_media_manager):
        """Test fetching metadata for a missing file raises a not_found error."""
        mock_current_user = Mock()
        mock_current_user.id = "user123"
        mock_media_manager.get_upload_info.side_effect = ValueError("Uploaded file not found")

        with pytest.raises(HTTPException) as exc:
            await controller.get_upload_info("missing.png", mock_current_user)

        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "not_found"

    # Test list_uploads / delete_upload methods
    @pytest.mark.asyncio
    async def test_list_uploads_success(self, controller, mock_media_manager):
        """Listing uploads is scoped to the requesting user only."""
        from src.features.media.dto import UploadListResult, UploadFileInfo

        mock_current_user = Mock()
        mock_current_user.id = "user123"

        mock_media_manager.list_uploads.return_value = UploadListResult(
            uploads=[
                UploadFileInfo(
                    id="up_1",
                    filename="abc.png",
                    original_filename="cat.png",
                    media_type="image",
                    mime_type="image/png",
                    url="/api/media/uploads/abc.png",
                    width=800,
                    height=600,
                    size=2048,
                )
            ],
            total=1,
            limit=20,
            offset=0,
        )

        result = await controller.list_uploads(mock_current_user)

        assert result.success is True
        assert result.data["total"] == 1
        assert result.data["uploads"][0]["filename"] == "abc.png"
        mock_media_manager.list_uploads.assert_called_once_with(
            "user123", media_type=None, limit=20, offset=0
        )

    @pytest.mark.asyncio
    async def test_list_uploads_passes_media_type_filter(self, controller, mock_media_manager):
        """The media_type query param reaches the manager unchanged."""
        from src.features.media.dto import UploadListResult

        mock_current_user = Mock()
        mock_current_user.id = "user123"
        mock_media_manager.list_uploads.return_value = UploadListResult(
            uploads=[], total=0, limit=20, offset=0
        )

        await controller.list_uploads(mock_current_user, media_type="video", limit=10, offset=5)

        mock_media_manager.list_uploads.assert_called_once_with(
            "user123", media_type="video", limit=10, offset=5
        )

    @pytest.mark.asyncio
    async def test_delete_upload_success(self, controller, mock_media_manager):
        """Deleting an owned upload succeeds."""
        mock_current_user = Mock()
        mock_current_user.id = "user123"
        mock_media_manager.delete_upload.return_value = None

        result = await controller.delete_upload("abc.png", mock_current_user)

        assert result.success is True
        assert result.data["filename"] == "abc.png"
        assert result.data["deleted"] is True
        mock_media_manager.delete_upload.assert_called_once_with("abc.png", "user123")

    @pytest.mark.asyncio
    async def test_delete_upload_not_found_is_404(self, controller, mock_media_manager):
        """Deleting a filename that doesn't exist, or belongs to another
        user, is a uniform 404 - never a 403 (GenerationPolicy precedent)."""
        mock_current_user = Mock()
        mock_current_user.id = "user123"
        mock_media_manager.delete_upload.side_effect = ValueError("Upload not found")

        with pytest.raises(HTTPException) as exc:
            await controller.delete_upload("someone-elses.png", mock_current_user)

        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "not_found"

    # Test serve_file_by_id method
    @pytest.mark.asyncio
    async def test_serve_file_by_id_success(self, controller, mock_media_manager, sample_image):
        """Test serving file by ID successfully"""
        mock_media_manager.get_file_by_id.return_value = MediaResult(
            content=sample_image,
            media_type='image/png',
            headers={"Cache-Control": "public, max-age=3600"},
            use_streaming=False
        )

        result = await controller.serve_file_by_id("file123")

        assert isinstance(result, Response)
        assert result.media_type == 'image/png'

    @pytest.mark.asyncio
    async def test_serve_file_by_id_not_found(self, controller, mock_media_manager):
        """Test serving file by ID when not found"""
        mock_media_manager.get_file_by_id.side_effect = ValueError("File not found")

        result = await controller.serve_file_by_id("nonexistent")

        # Should return APIResponse error
        assert result.success is False
        assert result.error == "not_found"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


class TestMediaRouteAuthentication:
    """Which media routes a browser can reach without an Authorization header.

    Auth is `OAuth2PasswordBearer` — the token only ever travels in the
    Authorization header. A browser rendering `<img src=...>` or `<video src=...>`
    sends no such header, so any file-serving route carrying an auth dependency
    401s every thumbnail it is asked to draw. These tests pin which routes are
    reachable that way; the file-serving routes must all agree.
    """

    @pytest.fixture
    def router(self):
        from src.features.media.routes import build_router

        container = Mock()
        container.media_controller = Mock(spec=MediaController)
        return build_router(container)

    @staticmethod
    def _route(router, path: str, method: str = "GET"):
        for route in router.routes:
            if route.path == path and method in route.methods:
                return route
        raise AssertionError(f"no {method} route registered for {path}")

    @staticmethod
    def _security_requirements(route):
        """Every security scheme FastAPI would enforce on this route.

        The scheme is not on the route's own dependant: `get_current_active_user`
        depends on `get_current_user`, which is what actually depends on the
        OAuth2 scheme. Only a recursive walk sees it — reading
        `route.dependant.security_requirements` directly returns [] for every
        route, authenticated or not, and quietly asserts nothing.
        """
        found = []
        stack = [route.dependant]
        while stack:
            dependant = stack.pop()
            found.extend(dependant.security_requirements)
            stack.extend(dependant.dependencies)
        return found

    def test_serving_an_upload_needs_no_authorization_header(self, router):
        """The regression: an <img> pointed at an upload got a 401, not a file."""
        route = self._route(router, "/api/media/uploads/{filename}")

        assert self._security_requirements(route) == [], (
            "Serving an uploaded file must not require an Authorization header — "
            "a browser <img>/<video> tag cannot send one, so every Library "
            "thumbnail would 401."
        )

    @pytest.mark.parametrize(
        "path",
        [
            "/api/media/uploads/{filename}",
            "/api/media/tmp/{filename}",
            "/api/media/generations/{generation_id}/{filename}",
        ],
    )
    def test_every_file_serving_route_is_browser_reachable(self, router, path):
        """Uploads, temp files and generation media are served the same way."""
        assert self._security_requirements(self._route(router, path)) == [], (
            f"{path} serves bytes straight into an <img>/<video> tag and so must "
            f"not carry an auth dependency."
        )

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/api/media/uploads", "GET"),        # list my uploads
            ("/api/media/uploads/{filename}", "DELETE"),
            ("/api/media/uploads/{filename}/info", "GET"),
            ("/api/media/upload", "POST"),
        ],
    )
    def test_managing_uploads_still_requires_authentication(self, router, path, method):
        """Only *serving* opens up. Listing, deleting and uploading go through
        the API client, which does send the bearer token, and stay per-user."""
        route = self._route(router, path, method)

        assert self._security_requirements(route), (
            f"{method} {path} is reached through the authenticated API client and "
            f"must stay scoped to the requesting user."
        )
