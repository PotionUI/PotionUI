"""Working with images.

`convert_image_to_base64` encodes a PIL image for transport - to the frontend, or
to a vision model that takes images inline.

`BackgroundMattingModel` runs BiRefNet background matting (an RGB(A) image in,
RGBA out at the same size, alpha = the matted subject). Load it, use it,
release it - it is never resident:

    model = BackgroundMattingModel.from_checkpoint(checkpoint_path)  # raises
                                                                       # ValueError
                                                                       # if the
                                                                       # file is
                                                                       # missing
                                                                       # or the
                                                                       # wrong
                                                                       # shape
    try:
        model.to("cuda")
        matted = model(image)
    finally:
        model.cpu()
        del model

`checkpoint_path` is a `.safetensors` file the plugin/user supplies (e.g. from
the model depot's `detection_segm/` folder, the convention
`content/plugins/marketplace/trellis2` established) - nothing is ever downloaded.
"""

import asyncio
import contextvars
import hashlib
import io
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Sequence, Union

from src.features.media.validators import UPLOAD_PURPOSE_USER, UPLOAD_PURPOSES
from src.pipelines.pipes._shared.media.frame_extract import extract_frame as _extract_video_frame
from src.pipelines.pipes.interpolator.rife.cadence import (
    Slot,
    output_count,
    output_fps,
    slot,
    source_per_output,
)
from src.platform.filesystem import audio_formats, visual_formats
from src.platform.filesystem.mesh_formats import mesh_format_registry
from src.platform.filesystem.storage_driver import local_copy, validate_key
from src.platform.plugins.runtime_registries import get_container
from src.platform.util import face_boxes
from src.platform.util.imaging import convert_image_to_base64
from src.platform.util.video_stream import (
    FFmpegNotFoundError,
    StreamingMp4Writer,
    has_audio_stream,
    mux_audio_from_source,
)
from src.platform.util.video_transcode import (
    FfmpegUnavailableError,
    TranscodeSpec,
    VideoProbe,
    VideoTranscodeError,
    find_ffmpeg,
    probe_video,
    transcode_video,
)
from src.plugin_api.generation import (
    HistoryMediaRef,
    LibraryMediaRef,
    MediaRefError,
    ResolvedMedia,
    resolve_media_ref,
)

if TYPE_CHECKING:
    from PIL import Image

    from src.platform.runtime.native.matting import BackgroundMattingModel

__all__ = [
    "BackgroundMattingModel",
    "FFmpegNotFoundError",
    "FfmpegUnavailableError",
    "HistoryMediaRef",
    "LibraryMediaRef",
    "MediaRefError",
    "ResolvedMedia",
    "Slot",
    "StoredMedia",
    "StreamingMp4Writer",
    "TranscodeSpec",
    "VideoProbe",
    "VideoTranscodeError",
    "convert_image_to_base64",
    "copy_media_file",
    "detect_faces",
    "embed_images",
    "extract_frame",
    "face_detector",
    "find_ffmpeg",
    "has_audio_stream",
    "image_embedder",
    "media_local_path",
    "mux_audio_from_source",
    "output_count",
    "output_fps",
    "probe_video",
    "resolve_media_ref",
    "save_user_media",
    "slot",
    "source_per_output",
    "transcode_video",
    "upload_formats",
]


def __getattr__(name):
    if name == "BackgroundMattingModel":
        from src.platform.runtime.native.matting import BackgroundMattingModel
        return BackgroundMattingModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class StoredMedia(ResolvedMedia):
    ref: Optional[LibraryMediaRef]
    width: Optional[int]
    height: Optional[int]


def _owner_id(user) -> str:
    user_id = getattr(user, "id", None)
    if not user_id:
        raise PermissionError("A user is required to store media")
    return user_id


def _checked_purpose(purpose: str) -> str:
    if purpose not in UPLOAD_PURPOSES:
        raise MediaRefError("invalid_purpose", f"purpose must be one of {', '.join(UPLOAD_PURPOSES)}")
    return purpose


def _seconds(at: Any) -> Optional[float]:
    if isinstance(at, str) and at == "last":
        return None
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
        raise MediaRefError("invalid_ref", "at must be 'last' or a number of seconds from the start")
    return float(at)


def _stored(container, kind: str, value: str):
    store = container.file_service if kind == "history" else container.media_store
    return store.storage_driver, validate_key(value)


def _frame_index(path: Path, seconds: Optional[float]) -> int:
    if seconds is None:
        return -1
    probe = probe_video(path)
    if probe is None or not probe.fps or probe.fps <= 0:
        raise MediaRefError("extract_failed", "The frame rate of the video could not be read")
    index = int(math.floor(seconds * probe.fps + 1e-6))
    last = int(round(probe.duration * probe.fps)) - 1
    if 0 <= last < index and seconds <= probe.duration:
        return last
    return index


def _frame_png(container, kind: str, value: str, seconds: Optional[float]) -> bytes:
    driver, key = _stored(container, kind, value)
    with local_copy(driver, key, Path(key).suffix) as path:
        index = _frame_index(path, seconds)
        try:
            frame = _extract_video_frame(path, index)
        except ValueError as exc:
            raise MediaRefError("extract_failed", "The frame could not be extracted") from exc
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()


def _read_admitted(store, source: Path, user_id: str, purpose: str, content_type: str) -> bytes:
    try:
        store.check_upload_size(user_id, source.stat().st_size, purpose, content_type)
    except ValueError as exc:
        raise MediaRefError("upload_rejected", str(exc)) from exc
    return source.read_bytes()


async def _store(container, data: bytes, filename: str, content_type: str, user_id: str, purpose: str) -> StoredMedia:
    try:
        stored = await container.media_store.upload_media(data, filename, content_type, user_id, purpose)
    except ValueError as exc:
        raise MediaRefError("upload_rejected", str(exc)) from exc
    return {
        "value": stored.relative_path,
        "media_type": content_type.split("/", 1)[0],
        "original_filename": filename,
        "origin": None,
        "ref": None if stored.id is None else {"kind": "library", "item_id": stored.id},
        "width": stored.width,
        "height": stored.height,
    }


async def extract_frame(
    user,
    ref: Union[HistoryMediaRef, LibraryMediaRef],
    at: Union[Literal["last"], float] = "last",
    purpose: str = UPLOAD_PURPOSE_USER,
) -> StoredMedia:
    user_id = _owner_id(user)
    seconds = _seconds(at)
    purpose = _checked_purpose(purpose)
    resolved = resolve_media_ref(user, ref)
    if resolved["media_type"] != "video":
        raise MediaRefError("not_a_video", "The referenced media is not a video")
    container = get_container()
    data = await asyncio.to_thread(_frame_png, container, ref["kind"], resolved["value"], seconds)
    stem = Path(resolved["original_filename"] or "video").stem or "video"
    label = "last" if seconds is None else f"{seconds:g}s"
    return await _store(container, data, f"{stem}-frame-{label}.png", "image/png", user_id, purpose)


async def save_user_media(user, path: Union[str, Path], purpose: str = UPLOAD_PURPOSE_USER) -> StoredMedia:
    user_id = _owner_id(user)
    purpose = _checked_purpose(purpose)
    source = Path(path)
    if not source.is_absolute():
        raise MediaRefError("invalid_ref", "The file path must be absolute")
    if not source.is_file():
        raise MediaRefError("not_found", "The file does not exist")
    container = get_container()
    store = container.media_store
    content_type = store.media_types.get_media_type(source.suffix)
    if not store.media_types.is_valid_media_type(content_type):
        raise MediaRefError("unsupported_media", "Only image, video, audio and 3D model files can be stored")
    data = await asyncio.to_thread(_read_admitted, store, source, user_id, purpose, content_type)
    return await _store(container, data, source.name, content_type, user_id, purpose)


_COPY_CHUNK = 1 << 20


def media_local_path(user, ref: Union[HistoryMediaRef, LibraryMediaRef]) -> Optional[Path]:
    resolved = resolve_media_ref(user, ref)
    driver, key = _stored(get_container(), ref["kind"], resolved["value"])
    return driver.local_path(key)


def copy_media_file(user, ref: Union[HistoryMediaRef, LibraryMediaRef], target: Union[str, Path]) -> None:
    resolved = resolve_media_ref(user, ref)
    driver, key = _stored(get_container(), ref["kind"], resolved["value"])
    destination = Path(target)
    try:
        with destination.open("wb") as handle:
            for chunk in driver.get_stream(key, _COPY_CHUNK):
                handle.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def upload_formats() -> Dict[str, List[str]]:
    return {
        "image": sorted(visual_formats.IMAGE_EXTENSIONS),
        "video": sorted(visual_formats.VIDEO_EXTENSIONS),
        "audio": sorted(audio_formats.AUDIO_EXTENSIONS),
        "model": sorted(mesh_format_registry.extensions()),
    }


ImageInput = Union[str, Path, "Image.Image"]


def _embedder():
    return get_container().media_indexer.vision_embedder


def _embedder_weights_here(embedder) -> bool:
    return bool(type(embedder).resolve_status(embedder.model_name, embedder.resolver)["present"])


def image_embedder() -> Dict[str, Any]:
    embedder = _embedder()
    return {
        "model": embedder.model_name,
        "slug": embedder.embedder_slug,
        "device": str(embedder.device),
        "available": _embedder_weights_here(embedder),
        "loaded": bool(embedder.is_loaded()),
    }


def embed_images(images: Sequence[ImageInput]) -> List[List[float]]:
    embedder = _embedder()
    if not _embedder_weights_here(embedder):
        raise FileNotFoundError(f"The weights of {embedder.model_name} are not on this server")
    return contextvars.copy_context().run(embedder.embed_images, list(images))


FACE_MODEL = "face_detection_yunet_2023mar.onnx"
FACE_MODEL_TYPE = "detection_bbox"
MODEL_FILES = {
    FACE_MODEL: (232589, "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
}
_verified_files: set = set()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_model_file(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if stamp in _verified_files:
        return True
    size, digest = MODEL_FILES[path.name]
    if not path.is_file() or stat.st_size != size or _sha256(path) != digest:
        return False
    _verified_files.add(stamp)
    return True


def _face_model() -> Optional[Path]:
    for entry in get_container().model_roots.type_dirs(FACE_MODEL_TYPE):
        candidate = Path(entry.path) / FACE_MODEL
        if _is_model_file(candidate):
            return candidate
    return None


def face_detector() -> Dict[str, Any]:
    return {"model": FACE_MODEL, "available": _face_model() is not None}


def detect_faces(images: Sequence[ImageInput], min_score: float = 0.5) -> List[List[Dict[str, Any]]]:
    model = _face_model()
    if model is None:
        raise FileNotFoundError(f"{FACE_MODEL} is not in a {FACE_MODEL_TYPE} folder of the model roots")
    return face_boxes.detect(model, list(images), min_score)
