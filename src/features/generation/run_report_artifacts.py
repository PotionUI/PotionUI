"""Report-owned storage for the binary payloads a `pipe_artifact` message
carries.

The live WebSocket message keeps its inline base64 - the drawer renders it
straight from the socket. The durable run report keeps a reference instead:
the bytes go into managed storage under the generation's own key prefix and
the report records where they landed, so a saved report is metadata-sized
regardless of how many comparison images the run emitted.

A payload that is already a persisted output (a `/api/media/...` URL or a
`generations/...` storage key) is never copied - the string it already is
*is* the reference.
"""

import base64
import binascii
import io
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.platform.filesystem.storage_driver import generations_key
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

# Marker key that tells a reader (backend or renderer) "this value is a
# reference to bytes held outside the report", rather than the payload.
MEDIA_REF_MARKER = "$media"
RUN_REPORT_ARTIFACT_REF = "run_report_artifact"

_FILENAME_PREFIX = "runreport"

_MAGIC_EXTENSIONS: Tuple[Tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"RIFF", "webp", "image/webp"),
)

# Enough base64 to cover the longest magic above once decoded.
_MAGIC_SNIFF_CHARS = 24


def looks_like_stored_reference(value: str) -> bool:
    """Whether `value` already points at bytes someone else persisted."""
    return value.startswith(("/api/", "http://", "https://", "generations/"))


def sniff_image_payload(value: str) -> Optional[Tuple[str, str]]:
    """`(extension, mime)` when `value` is base64 (or a data URL) of an image
    format the browser can render, else `None`."""
    payload = value.partition(",")[2] if value.startswith("data:") else value
    if not payload:
        return None

    head = payload[:_MAGIC_SNIFF_CHARS]
    padded = head + "=" * (-len(head) % 4)
    try:
        magic = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None

    for prefix, extension, mime in _MAGIC_EXTENSIONS:
        if magic.startswith(prefix):
            return extension, mime
    return None


def decode_payload(value: str) -> Optional[bytes]:
    payload = value.partition(",")[2] if value.startswith("data:") else value
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def iter_ref_paths(report: Optional[Dict[str, Any]]) -> List[str]:
    """Every storage key the report owns, in recording order."""
    if not isinstance(report, dict):
        return []

    paths: List[str] = []
    for artifact in report.get("artifacts") or []:
        for ref in _refs_in(artifact.get("artifact_data")):
            path = ref.get("path")
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
    return paths


def find_ref(report: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """The recorded reference named `name`, or `None`.

    Serving resolves through this rather than through a client-supplied
    path: only bytes the report itself recorded are reachable, so the route
    has no path to traverse out of.
    """
    if not isinstance(report, dict):
        return None
    for artifact in report.get("artifacts") or []:
        for ref in _refs_in(artifact.get("artifact_data")):
            if ref.get("name") == name:
                return ref
    return None


def _refs_in(artifact_data: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(artifact_data, dict):
        return ()
    return [
        value for value in artifact_data.values()
        if isinstance(value, dict) and value.get(MEDIA_REF_MARKER) == RUN_REPORT_ARTIFACT_REF
    ]


class RunReportArtifactStore:
    """Writes, reads and removes the bytes a durable run report references."""

    def __init__(self, file_service):
        self._file_service = file_service

    def save(self, generation_id: str, value: str) -> Optional[Dict[str, Any]]:
        """Offload one base64 image payload, returning the reference to
        record in place of it. `None` when the payload is not an image, is
        undecodable, or storage refused the write - the caller records an
        omission marker instead of silently keeping the payload."""
        sniffed = sniff_image_payload(value)
        if sniffed is None:
            return None
        extension, mime = sniffed

        data = decode_payload(value)
        if not data:
            return None

        name = f"{_FILENAME_PREFIX}_{generate_ulid()}"
        _, metadata = self._file_service.save_file(
            generation_id, data, extension=extension, prefix=name,
        )
        if not metadata:
            return None

        ref = {
            MEDIA_REF_MARKER: RUN_REPORT_ARTIFACT_REF,
            "name": f"{name}.{extension}",
            "url": artifact_url(generation_id, f"{name}.{extension}"),
            "path": metadata["file_path"],
            "bytes": len(data),
            "mime": mime,
        }
        width, height = _dimensions(data)
        if width is not None:
            ref["width"] = width
            ref["height"] = height
        return ref

    def read(self, path: str) -> bytes:
        return self._file_service.storage_driver.get_bytes(generations_key(path))

    def delete_paths(self, paths: Iterable[str]) -> int:
        deleted = 0
        for path in paths:
            try:
                if self._file_service.delete_generation_output(path):
                    deleted += 1
            except Exception:
                logger.warning("[RUN_REPORT] Failed to delete artifact %s", path, exc_info=True)
        return deleted

    def delete_for_report(self, report: Optional[Dict[str, Any]]) -> int:
        return self.delete_paths(iter_ref_paths(report))


def artifact_url(generation_id: str, name: str) -> str:
    return f"/api/generations/{generation_id}/run-report/artifacts/{name}"


def _dimensions(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return image.width, image.height
    except Exception:
        return None, None
