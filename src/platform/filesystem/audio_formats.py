"""
Canonical set of audio container formats accepted for storage.

Lives in `platform`, not `features` - same reason as `mesh_formats.py`:
`FileStore` (`src/platform/filesystem/file_store.py`) needs to classify a
file's extension, and the layering rule in CLAUDE.md runs one way only -
`platform` may not import `src.features`.

Unlike a mesh, an audio container needs no bespoke structural validation:
every format registered here is already parsed generically by `soundfile`
(the duration probe in `src.features.generation.media_probe`), so there is
no per-format probe callback to carry the way `mesh_formats.MeshFormat`
does - just the extension -> mime type mapping that used to be duplicated
between `src.features.media.media_types` and the audio generation handler.
Add a new extension here once; both features-side call sites pick it up
without being touched.
"""

from __future__ import annotations

from typing import Dict, FrozenSet

AUDIO_MIME_TYPES: Dict[str, str] = {
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
}

AUDIO_EXTENSIONS: FrozenSet[str] = frozenset(AUDIO_MIME_TYPES.keys())

DEFAULT_MIME_TYPE = 'audio/wav'


def is_registered(extension: str) -> bool:
    """Whether `extension` (e.g. '.wav', with or without leading dot) is a
    recognized audio container."""
    ext = extension if extension.startswith('.') else f'.{extension}'
    return ext.lower() in AUDIO_MIME_TYPES


def mime_type_for(extension: str, default: str = DEFAULT_MIME_TYPE) -> str:
    """MIME type for `extension` (e.g. '.wav' or 'wav'), or `default` if not
    registered."""
    ext = extension if extension.startswith('.') else f'.{extension}'
    return AUDIO_MIME_TYPES.get(ext.lower(), default)
