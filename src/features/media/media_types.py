"""
Media type resolver for PotionUI.

This module provides a single source of truth for media type detection
based on file extensions, plus `sniff_media_extension` for the cases where
there is no usable extension to go on.
"""

from typing import Optional, Set

from src.platform.filesystem import audio_formats
from src.platform.filesystem.mesh_formats import mesh_format_registry


# Longest signature first where two share a prefix (`RIFF`/`ftyp` containers
# are disambiguated by their own branch below).
_LEADING_SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'\xff\xd8\xff', '.jpg'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
    (b'BM', '.bmp'),
    (b'\x1a\x45\xdf\xa3', '.webm'),   # EBML: webm and mkv share it; webm is servable
    (b'glTF', '.glb'),
    (b'fLaC', '.flac'),
    (b'OggS', '.ogg'),
    (b'ID3', '.mp3'),
)

# `RIFF....<form>` containers, keyed by the 4-byte form type at offset 8.
_RIFF_FORMS = {
    b'WEBP': '.webp',
    b'WAVE': '.wav',
    b'AVI ': '.avi',
}

# ISO base media (`....ftyp<brand>`) major brands that are NOT mp4.
_ISO_BMFF_BRANDS = {
    b'qt  ': '.mov',
}


def sniff_media_extension(content: bytes) -> Optional[str]:
    """The extension `content` really is, from its leading bytes, or None.

    For files that arrive with no extension, or one no registry recognises:
    defaulting such a file to `.png`/IMAGE mistypes every video, audio track
    and mesh that comes through, and the wrong `file_type` follows the row
    forever (wrong player, wrong thumbnailer, wrong serve MIME).

    Signature-based and deliberately conservative - an unrecognised header
    returns None so the caller keeps its own fallback rather than being handed
    a guess.
    """
    if not content:
        return None

    if content[:4] == b'RIFF' and len(content) >= 12:
        form = _RIFF_FORMS.get(content[8:12])
        if form:
            return form

    if len(content) >= 12 and content[4:8] == b'ftyp':
        return _ISO_BMFF_BRANDS.get(content[8:12], '.mp4')

    for signature, extension in _LEADING_SIGNATURES:
        if content.startswith(signature):
            return extension

    # MPEG audio frame sync (an mp3 with no ID3 tag): 11 set bits.
    if len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0:
        return '.mp3'

    return None


class MediaTypeResolver:
    """Resolves file extensions to MIME types."""

    MEDIA_TYPE_MAP = {
        # Images
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp',
        '.svg': 'image/svg+xml',
        # Videos
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.avi': 'video/avi',
        '.mov': 'video/quicktime',
        '.mkv': 'video/x-matroska',
        # Audio and meshes come from their platform-side registries, not this
        # map - see get_media_type().
    }

    # Image extensions that can be processed by PIL
    RESIZABLE_EXTENSIONS: Set[str] = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}

    # All image extensions
    IMAGE_EXTENSIONS: Set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}

    # Video extensions
    VIDEO_EXTENSIONS: Set[str] = {'.mp4', '.webm', '.avi', '.mov', '.mkv'}

    # Canonical audio extensions live in `audio_formats` (platform layer,
    # shared with `FileStore`); mirrored here as a plain class attribute
    # (rather than a property, like MESH_EXTENSIONS below) so callers can
    # keep using `MediaTypeResolver.AUDIO_EXTENSIONS` at class scope, the way
    # `file_resolver.py`'s `PRESET_SERVABLE_EXTENSIONS` does.
    AUDIO_EXTENSIONS: Set[str] = set(audio_formats.AUDIO_EXTENSIONS)

    @property
    def MESH_EXTENSIONS(self) -> Set[str]:
        """Extensions accepted as self-contained 3D meshes.

        Derived from `mesh_format_registry` (see `src.platform.filesystem.
        mesh_formats` for the admission rule) rather than hardcoded here, so
        a new format is a registry entry, not a change to this class.
        """
        return set(mesh_format_registry.extensions())

    def get_media_type(self, suffix: str) -> str:
        """Get MIME type for a file extension.

        Args:
            suffix: File extension including dot (e.g., '.jpg')

        Returns:
            MIME type string, or 'application/octet-stream' if unknown
        """
        suffix = suffix.lower()
        if suffix in self.MEDIA_TYPE_MAP:
            return self.MEDIA_TYPE_MAP[suffix]
        if audio_formats.is_registered(suffix):
            return audio_formats.mime_type_for(suffix)
        mesh_format = mesh_format_registry.get(suffix)
        if mesh_format:
            return mesh_format.mime_type
        return 'application/octet-stream'

    def is_image(self, suffix: str) -> bool:
        """Check if file extension is an image type.

        Args:
            suffix: File extension including dot (e.g., '.jpg')

        Returns:
            True if the extension represents an image
        """
        return suffix.lower() in self.IMAGE_EXTENSIONS

    def is_resizable(self, suffix: str) -> bool:
        """Check if file extension can be resized by PIL.

        Args:
            suffix: File extension including dot (e.g., '.jpg')

        Returns:
            True if the extension can be resized
        """
        return suffix.lower() in self.RESIZABLE_EXTENSIONS

    def is_video(self, suffix: str) -> bool:
        """Check if file extension is a video type.

        Args:
            suffix: File extension including dot (e.g., '.mp4')

        Returns:
            True if the extension represents a video
        """
        return suffix.lower() in self.VIDEO_EXTENSIONS

    def is_audio(self, suffix: str) -> bool:
        """Check if file extension is an audio type.

        Args:
            suffix: File extension including dot (e.g., '.mp3')

        Returns:
            True if the extension represents audio
        """
        return suffix.lower() in self.AUDIO_EXTENSIONS

    def is_mesh(self, suffix: str) -> bool:
        """Check if file extension is a 3D mesh type.

        Args:
            suffix: File extension including dot (e.g., '.glb')

        Returns:
            True if the extension represents a mesh
        """
        return mesh_format_registry.is_registered(suffix.lower())

    def is_valid_media_type(self, content_type: str) -> bool:
        """Check if a content type is a valid media type for upload.

        Args:
            content_type: MIME type string (e.g., 'image/jpeg')

        Returns:
            True if the content type is allowed for upload
        """
        if not content_type:
            return False
        if (
            content_type.startswith('image/') or
            content_type.startswith('video/') or
            content_type.startswith('audio/')
        ):
            return True
        return content_type in mesh_format_registry.mime_types().values()
