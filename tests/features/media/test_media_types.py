"""Tests for MediaTypeResolver class."""

import pytest
from src.features.media.media_types import MediaTypeResolver


class TestMediaTypeResolver:
    """Tests for MediaTypeResolver."""

    @pytest.fixture
    def resolver(self):
        """Create MediaTypeResolver instance."""
        return MediaTypeResolver()

    # Test get_media_type method
    def test_get_media_type_jpeg(self, resolver):
        """Test JPEG media type detection."""
        assert resolver.get_media_type('.jpg') == 'image/jpeg'
        assert resolver.get_media_type('.jpeg') == 'image/jpeg'
        assert resolver.get_media_type('.JPG') == 'image/jpeg'
        assert resolver.get_media_type('.JPEG') == 'image/jpeg'

    def test_get_media_type_png(self, resolver):
        """Test PNG media type detection."""
        assert resolver.get_media_type('.png') == 'image/png'
        assert resolver.get_media_type('.PNG') == 'image/png'

    def test_get_media_type_gif(self, resolver):
        """Test GIF media type detection."""
        assert resolver.get_media_type('.gif') == 'image/gif'

    def test_get_media_type_webp(self, resolver):
        """Test WebP media type detection."""
        assert resolver.get_media_type('.webp') == 'image/webp'

    def test_get_media_type_bmp(self, resolver):
        """Test BMP media type detection."""
        assert resolver.get_media_type('.bmp') == 'image/bmp'

    def test_get_media_type_svg(self, resolver):
        """Test SVG media type detection."""
        assert resolver.get_media_type('.svg') == 'image/svg+xml'

    def test_get_media_type_video(self, resolver):
        """Test video media type detection."""
        assert resolver.get_media_type('.mp4') == 'video/mp4'
        assert resolver.get_media_type('.webm') == 'video/webm'
        assert resolver.get_media_type('.avi') == 'video/avi'
        assert resolver.get_media_type('.mov') == 'video/quicktime'
        assert resolver.get_media_type('.mkv') == 'video/x-matroska'

    def test_get_media_type_audio(self, resolver):
        """Test audio media type detection."""
        assert resolver.get_media_type('.wav') == 'audio/wav'
        assert resolver.get_media_type('.mp3') == 'audio/mpeg'
        assert resolver.get_media_type('.ogg') == 'audio/ogg'
        assert resolver.get_media_type('.flac') == 'audio/flac'
        assert resolver.get_media_type('.m4a') == 'audio/mp4'
        assert resolver.get_media_type('.aac') == 'audio/aac'

    def test_get_media_type_unknown(self, resolver):
        """Test unknown extension returns octet-stream."""
        assert resolver.get_media_type('.xyz') == 'application/octet-stream'
        assert resolver.get_media_type('.unknown') == 'application/octet-stream'

    # Test is_image method
    def test_is_image_true(self, resolver):
        """Test is_image returns True for image extensions."""
        assert resolver.is_image('.jpg') is True
        assert resolver.is_image('.jpeg') is True
        assert resolver.is_image('.png') is True
        assert resolver.is_image('.gif') is True
        assert resolver.is_image('.webp') is True
        assert resolver.is_image('.bmp') is True
        assert resolver.is_image('.svg') is True

    def test_is_image_false(self, resolver):
        """Test is_image returns False for non-image extensions."""
        assert resolver.is_image('.mp4') is False
        assert resolver.is_image('.mp3') is False
        assert resolver.is_image('.txt') is False

    def test_is_image_case_insensitive(self, resolver):
        """Test is_image is case insensitive."""
        assert resolver.is_image('.JPG') is True
        assert resolver.is_image('.PNG') is True
        assert resolver.is_image('.WEBP') is True

    # Test is_resizable method
    def test_is_resizable_true(self, resolver):
        """Test is_resizable returns True for resizable extensions."""
        assert resolver.is_resizable('.jpg') is True
        assert resolver.is_resizable('.jpeg') is True
        assert resolver.is_resizable('.png') is True
        assert resolver.is_resizable('.webp') is True
        assert resolver.is_resizable('.bmp') is True

    def test_is_resizable_false(self, resolver):
        """Test is_resizable returns False for non-resizable extensions."""
        assert resolver.is_resizable('.gif') is False  # Animated, not easily resizable
        assert resolver.is_resizable('.svg') is False  # Vector, different handling
        assert resolver.is_resizable('.mp4') is False
        assert resolver.is_resizable('.mp3') is False

    # Test is_video method
    def test_is_video_true(self, resolver):
        """Test is_video returns True for video extensions."""
        assert resolver.is_video('.mp4') is True
        assert resolver.is_video('.webm') is True
        assert resolver.is_video('.avi') is True
        assert resolver.is_video('.mov') is True
        assert resolver.is_video('.mkv') is True

    def test_is_video_false(self, resolver):
        """Test is_video returns False for non-video extensions."""
        assert resolver.is_video('.jpg') is False
        assert resolver.is_video('.mp3') is False
        assert resolver.is_video('.txt') is False

    # Test is_audio method
    def test_is_audio_true(self, resolver):
        """Test is_audio returns True for audio extensions."""
        assert resolver.is_audio('.wav') is True
        assert resolver.is_audio('.mp3') is True
        assert resolver.is_audio('.ogg') is True
        assert resolver.is_audio('.flac') is True
        assert resolver.is_audio('.m4a') is True
        assert resolver.is_audio('.aac') is True

    def test_is_audio_false(self, resolver):
        """Test is_audio returns False for non-audio extensions."""
        assert resolver.is_audio('.jpg') is False
        assert resolver.is_audio('.mp4') is False
        assert resolver.is_audio('.txt') is False

    # Test is_valid_media_type method
    def test_is_valid_media_type_images(self, resolver):
        """Test is_valid_media_type for image content types."""
        assert resolver.is_valid_media_type('image/jpeg') is True
        assert resolver.is_valid_media_type('image/png') is True
        assert resolver.is_valid_media_type('image/gif') is True

    def test_is_valid_media_type_videos(self, resolver):
        """Test is_valid_media_type for video content types."""
        assert resolver.is_valid_media_type('video/mp4') is True
        assert resolver.is_valid_media_type('video/webm') is True

    def test_is_valid_media_type_audio(self, resolver):
        """Test is_valid_media_type for audio content types."""
        assert resolver.is_valid_media_type('audio/mpeg') is True
        assert resolver.is_valid_media_type('audio/wav') is True

    def test_is_valid_media_type_invalid(self, resolver):
        """Test is_valid_media_type for invalid content types."""
        assert resolver.is_valid_media_type('text/plain') is False
        assert resolver.is_valid_media_type('application/json') is False
        assert resolver.is_valid_media_type('') is False
        assert resolver.is_valid_media_type(None) is False

    def test_is_valid_media_type_mesh(self, resolver):
        """Test is_valid_media_type accepts glb, rejects other model/* types."""
        assert resolver.is_valid_media_type('model/gltf-binary') is True
        assert resolver.is_valid_media_type('model/obj') is False

    # Test is_mesh method
    def test_is_mesh_true(self, resolver):
        """Test is_mesh returns True for glb extension."""
        assert resolver.is_mesh('.glb') is True
        assert resolver.is_mesh('.GLB') is True

    def test_is_mesh_false(self, resolver):
        """Test is_mesh returns False for non-mesh extensions."""
        assert resolver.is_mesh('.jpg') is False
        assert resolver.is_mesh('.obj') is False

    def test_get_media_type_mesh(self, resolver):
        """Test glb media type detection."""
        assert resolver.get_media_type('.glb') == 'model/gltf-binary'


class TestMeshFormatRegistryDrivesResolver:
    """MediaTypeResolver derives mesh behavior from mesh_format_registry, not a literal."""

    @pytest.fixture
    def resolver(self):
        return MediaTypeResolver()

    @pytest.fixture
    def fake_ply_format(self):
        from src.platform.filesystem.mesh_formats import MeshFormat, mesh_format_registry

        mesh_format_registry.register(
            MeshFormat(extension='.ply', mime_type='application/x-ply', probe=lambda path: (None, None))
        )
        try:
            yield
        finally:
            del mesh_format_registry._by_extension['.ply']

    def test_is_valid_media_type_rejects_unregistered_model_mime(self, resolver):
        """`model/obj` has no registered format, so it stays rejected."""
        assert resolver.is_valid_media_type('model/obj') is False

    def test_is_valid_media_type_accepts_a_newly_registered_mesh_mime(self, resolver, fake_ply_format):
        assert resolver.is_valid_media_type('application/x-ply') is True

    def test_is_mesh_tracks_registry_membership(self, resolver, fake_ply_format):
        assert resolver.is_mesh('.ply') is True
        assert resolver.is_mesh('.PLY') is True

    def test_is_mesh_false_before_registration(self, resolver):
        assert resolver.is_mesh('.ply') is False

    def test_get_media_type_tracks_registry(self, resolver, fake_ply_format):
        assert resolver.get_media_type('.ply') == 'application/x-ply'

    def test_mesh_extensions_property_tracks_registry(self, resolver, fake_ply_format):
        assert '.ply' in resolver.MESH_EXTENSIONS
        assert '.glb' in resolver.MESH_EXTENSIONS


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
