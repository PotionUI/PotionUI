"""A lossless wire codec for `GenerationOutput` (`src.pipelines.outputs`).

Remote Native must behave exactly like the local engine: whatever a pipe
emits through its `generation_outputs` callable has to reach core's `emit`
callback as the identical dataclass instance, whether the pipe ran in this
process or on a worker. Rather than a per-type whitelist (which silently
drops any output type nobody remembered to wire up - see git history for what
that cost), this module walks a `GenerationOutput` generically via
`dataclasses.fields()` + `typing.get_type_hints()` and encodes every field it
finds. A future output type - core's or a plugin's - needs no change here to
cross the wire; it only needs to be built from JSON-safe values, PIL images,
and `Path`s, which is already the vocabulary `src.pipelines.outputs` and
plugin outputs are written in.

`encode_output` (worker side) turns one output into a JSON-safe dict plus the
`ArtifactRefV1`s any media it contains materialized to; `decode_output` (core
side) turns that dict, plus a `{artifact_id: local_path}` map of already
-downloaded artifacts, back into the same dataclass instance.

`resolve_import_destination` (used by both artifact-import and asset-staging
callers) lives here too - it is the other half of "reconstruct what a worker
produced" this module is about.
"""

from __future__ import annotations

import dataclasses
import sys
import typing
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Tuple

from PIL import Image as PILImage

from src.pipelines.outputs import GenerationOutput
from src.platform.worker_protocol.artifact import ArtifactRefV1


class OutputEncodeError(Exception):
    """A `GenerationOutput` field could not be put on the wire.

    Raised loudly rather than silently dropping the field or the whole
    output - a future output type that can't cross the wire must fail the
    run, not degrade it.
    """


class OutputDecodeError(Exception):
    """A wire payload could not be turned back into a `GenerationOutput`."""


class ArtifactImportError(Exception):
    """The destination path for an artifact could not be resolved safely."""


Materializer = Callable[..., ArtifactRefV1]


# -- destination path resolution (shared by artifact download and, formerly,
#    asset staging) -----------------------------------------------------------

def resolve_import_destination(imports_dir: Path, artifact: ArtifactRefV1) -> Path:
    """The contained, collision-free path an artifact's bytes are written to.

    Named by `artifact_id` (already validated as a bare 32-hex-char id by the
    protocol layer - see `ArtifactRefV1`/the worker's own `_ARTIFACT_ID_RE`
    check) plus the suggested filename's suffix, so two artifacts can never
    collide and the destination can never escape ``imports_dir`` even if a
    future protocol version widens what a filename may contain.
    """
    imports_root = imports_dir.resolve()
    imports_root.mkdir(parents=True, exist_ok=True)

    suffix = Path(artifact.filename).suffix if artifact.filename else ""
    candidate = (imports_root / f"{artifact.artifact_id}{suffix}").resolve()
    try:
        candidate.relative_to(imports_root)
    except ValueError as exc:
        raise ArtifactImportError(
            f"artifact {artifact.artifact_id!r} destination escapes {imports_root}"
        ) from exc
    return candidate


# -- encode (worker side) -----------------------------------------------------

def encode_output(
    output: GenerationOutput, materialize: Materializer,
) -> Tuple[Dict[str, Any], Tuple[ArtifactRefV1, ...]]:
    """Encode one `GenerationOutput` (core's or a plugin's) to a JSON-safe
    dict plus every `ArtifactRefV1` its media materialized to.

    `materialize(value, *, temporary)` is called for every `PIL.Image.Image`
    found anywhere in the output, and for every `Path` value in a field whose
    *declared* type is `Path` (e.g. `video_path`/`audio_path`/`mesh_path`) -
    a `Path` value anywhere else (e.g. inside `ParamGenerationOutput.values`)
    is just stringified, not materialized. `temporary` is the owning
    dataclass's own `temporary` field when it has one, else `False` - the
    worker uses it to choose a full-fidelity artifact vs. a downscaled
    preview for an image; it is meaningless for a leaf file and callers may
    ignore it there.
    """
    if not isinstance(output, GenerationOutput):
        raise OutputEncodeError(
            f"cannot encode {type(output).__name__}: not a GenerationOutput"
        )
    artifacts: list[ArtifactRefV1] = []
    payload = _encode_dataclass(output, materialize, artifacts, top_level=True)
    return payload, tuple(artifacts)


def _encode_dataclass(
    obj: Any, materialize: Materializer, artifacts: list, *, top_level: bool = False,
) -> Dict[str, Any]:
    cls = type(obj)
    hints = typing.get_type_hints(cls)
    temporary = bool(getattr(obj, "temporary", False))
    result: Dict[str, Any] = {"$type": f"{cls.__module__}:{cls.__qualname__}"}
    for f in dataclasses.fields(cls):
        # pipe_id/pipe_name are re-stamped by core from the event's own pipe
        # id/type on the way back in (see decode_output) - the worker's
        # pipe_id is this pipeline's string id, core's is a pipe *index*, so
        # carrying the worker's value across would be actively wrong.
        if top_level and f.name in ("pipe_id", "pipe_name"):
            continue
        value = getattr(obj, f.name)
        result[f.name] = _encode_value(
            value, hints.get(f.name), materialize, artifacts, temporary,
            context=f"{cls.__qualname__}.{f.name}",
        )
    return result


def _encode_value(
    value: Any, hint: Any, materialize: Materializer, artifacts: list, temporary: bool, *, context: str,
) -> Any:
    if value is None:
        return None
    if isinstance(value, PILImage.Image):
        ref = materialize(value, temporary=temporary)
        artifacts.append(ref)
        return {"$artifact": ref.artifact_id}
    if isinstance(value, Path):
        if not _hint_is_path(hint):
            return str(value)
        if not value.exists():
            raise OutputEncodeError(f"{context}: media file does not exist: {value}")
        ref = materialize(value, temporary=temporary)
        artifacts.append(ref)
        return {"$artifact": ref.artifact_id}
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _encode_dataclass(value, materialize, artifacts)
    if isinstance(value, (list, tuple)):
        args = _origin_args(hint)
        return [
            _encode_value(
                item, _element_hint(hint, args, idx), materialize, artifacts, temporary,
                context=f"{context}[{idx}]",
            )
            for idx, item in enumerate(value)
        ]
    if isinstance(value, dict):
        args = _origin_args(hint)
        value_hint = args[1] if len(args) == 2 else None
        return {
            str(key): _encode_value(
                item, value_hint, materialize, artifacts, temporary, context=f"{context}[{key!r}]",
            )
            for key, item in value.items()
        }
    if type(value).__module__.split(".")[0] == "numpy" and hasattr(value, "item"):
        return value.item()
    raise OutputEncodeError(
        f"{context}: cannot encode a value of type {type(value).__name__} onto the wire"
    )


# -- decode (core side) -------------------------------------------------------

def decode_output(
    payload: Mapping[str, Any], artifact_paths: Mapping[str, Path], *, pipe_index: Any, pipe_name: Any,
) -> GenerationOutput:
    """The inverse of `encode_output`. `artifact_paths` maps every
    `artifact_id` this payload references to the local path its bytes were
    already downloaded to (core downloads via `event.artifacts` before
    calling this - see `RemoteNativeBackend._handle_event`)."""
    if not isinstance(payload, dict) or "$type" not in payload:
        raise OutputDecodeError("output payload carries no '$type'")

    cls = _resolve_class(payload["$type"])
    if not issubclass(cls, GenerationOutput):
        raise OutputDecodeError(f"{payload['$type']!r} does not resolve to a GenerationOutput subclass")

    output = _decode_dataclass(payload, cls, artifact_paths)
    output.pipe_id = pipe_index
    output.pipe_name = pipe_name
    return output


def _resolve_class(type_string: str) -> type:
    """Resolves a `"module:QualName"` string through `sys.modules` only -
    never `importlib` - so a wire string can never trigger an import as a
    side effect of decoding untrusted-ish worker output."""
    if ":" not in type_string:
        raise OutputDecodeError(f"malformed '$type' {type_string!r} (expected 'module:QualName')")
    module_name, qualname = type_string.split(":", 1)
    module = sys.modules.get(module_name)
    if module is None:
        raise OutputDecodeError(
            f"cannot resolve '$type' {type_string!r}: module {module_name!r} is not imported"
        )
    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            raise OutputDecodeError(f"cannot resolve '$type' {type_string!r}: no attribute {part!r}")
    if not isinstance(obj, type) or not dataclasses.is_dataclass(obj):
        raise OutputDecodeError(f"'$type' {type_string!r} does not resolve to a dataclass")
    return obj


def _decode_dataclass(payload: Mapping[str, Any], cls: type, artifact_paths: Mapping[str, Path]) -> Any:
    hints = typing.get_type_hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in payload:
            continue  # absent -> the dataclass's own default applies
        kwargs[f.name] = _decode_value(
            payload[f.name], hints.get(f.name), artifact_paths, context=f"{cls.__qualname__}.{f.name}",
        )
    return cls(**kwargs)


def _decode_value(value: Any, hint: Any, artifact_paths: Mapping[str, Path], *, context: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict) and "$artifact" in value:
        artifact_id = value["$artifact"]
        path = artifact_paths.get(artifact_id)
        if path is None:
            raise OutputDecodeError(f"{context}: no local path was downloaded for artifact {artifact_id!r}")
        if _hint_is_image(hint):
            image = PILImage.open(path)
            image.load()  # see outputs.py's _force_eager_decode - same cross-thread race
            return image
        return path
    if isinstance(value, dict) and "$type" in value:
        nested_cls = _resolve_class(value["$type"])
        return _decode_dataclass(value, nested_cls, artifact_paths)
    if isinstance(value, dict):
        args = _origin_args(hint)
        value_hint = args[1] if len(args) == 2 else None
        return {
            key: _decode_value(item, value_hint, artifact_paths, context=f"{context}[{key!r}]")
            for key, item in value.items()
        }
    if isinstance(value, list):
        args = _origin_args(hint)
        items = [
            _decode_value(item, _element_hint(hint, args, idx), artifact_paths, context=f"{context}[{idx}]")
            for idx, item in enumerate(value)
        ]
        return tuple(items) if _hint_is_tuple(hint) else items
    return value


# -- type-hint helpers (shared by encode and decode) --------------------------

def _unwrap_optional(hint: Any) -> Any:
    if hint is None:
        return None
    if typing.get_origin(hint) is typing.Union:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return hint


def _hint_is_path(hint: Any) -> bool:
    return _unwrap_optional(hint) is Path


def _hint_is_image(hint: Any) -> bool:
    """`ImageGenerationOutput.image` (and every field shaped like it, e.g.
    `CompareImagesGenerationOutput.compare`'s second element) is annotated
    ``Image`` from ``from PIL import Image`` - that name is the *module*
    `PIL.Image`, not the class `PIL.Image.Image`, so both spellings are
    accepted here."""
    hint = _unwrap_optional(hint)
    return hint is PILImage or hint is PILImage.Image


def _hint_is_tuple(hint: Any) -> bool:
    return typing.get_origin(_unwrap_optional(hint)) is tuple


def _origin_args(hint: Any) -> tuple:
    hint = _unwrap_optional(hint)
    if hint is None:
        return ()
    return typing.get_args(hint)


def _element_hint(hint: Any, args: tuple, index: int) -> Any:
    if not args:
        return None
    unwrapped = _unwrap_optional(hint)
    if typing.get_origin(unwrapped) is tuple:
        if len(args) == 2 and args[1] is Ellipsis:  # Tuple[X, ...]
            return args[0]
        return args[index] if index < len(args) else None
    return args[0]  # List[X] / a bare container - every element shares X
