"""output_codec: lossless encode/decode of any GenerationOutput.

`test_every_concrete_output_type_round_trips` is the guard the design exists
for: it walks every concrete `GenerationOutput` subclass reachable from
`src.pipelines.outputs` and proves each one survives an encode/decode round
trip - a new output type that can't cross the wire fails THIS test, not a
silent drop discovered later in a remote generation.
"""

from __future__ import annotations

import dataclasses
import hashlib
import typing
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest
from PIL import Image

import src.pipelines.outputs as outputs_module
from src.features.remote_execution.output_codec import (
    OutputDecodeError,
    OutputEncodeError,
    decode_output,
    encode_output,
)
from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    GalleryGenerationOutput,
    GenerationOutput,
    Icon,
    ImageGenerationOutput,
    ParamGenerationOutput,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
)
from src.platform.worker_protocol import ArtifactRefV1, ContentDigest


# -- shared materializer: mirrors what WorkerPipelineExecutor does, minus the
#    executor plumbing - write bytes under tmp_path, hand back a ref, and
#    record the mapping this test needs to build decode_output's
#    artifact_paths. --------------------------------------------------------

def _materializer(tmp_path: Path):
    registry: Dict[str, Path] = {}

    def materialize(value: Any, *, temporary: bool) -> ArtifactRefV1:
        artifact_id = uuid.uuid4().hex
        if isinstance(value, Path):
            dest = tmp_path / f"{artifact_id}{value.suffix}"
            dest.write_bytes(value.read_bytes())
        else:
            dest = tmp_path / f"{artifact_id}.png"
            value.save(dest, format="PNG")
        data = dest.read_bytes()
        registry[artifact_id] = dest
        return ArtifactRefV1(
            artifact_id=artifact_id,
            kind="image" if not isinstance(value, Path) else "file",
            media_type="application/octet-stream",
            size_bytes=len(data),
            digest=ContentDigest(algorithm="sha256", hex=hashlib.sha256(data).hexdigest()),
            uri=f"/v1/artifacts/{artifact_id}",
            filename=dest.name,
        )

    return materialize, registry


def _round_trip(output: GenerationOutput, tmp_path: Path, *, pipe_index=7, pipe_name="test/pipe"):
    materialize, registry = _materializer(tmp_path)
    payload, artifacts = encode_output(output, materialize)
    artifact_paths = {a.artifact_id: registry[a.artifact_id] for a in artifacts}
    decoded = decode_output(payload, artifact_paths, pipe_index=pipe_index, pipe_name=pipe_name)
    return decoded, payload, artifacts


# -- generic fixture builder: one instance of any dataclass, from its own
#    type hints -------------------------------------------------------------

_counter = 0


def _next() -> int:
    global _counter
    _counter += 1
    return _counter


def _build_dataclass(cls: type, *, tmp_path: Path) -> Any:
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        kwargs[f.name] = _build_value(hints.get(f.name), field_name=f.name, tmp_path=tmp_path)
    return cls(**kwargs)


def _build_value(hint: Any, *, field_name: str, tmp_path: Path) -> Any:
    hint = _unwrap_optional(hint)
    origin = typing.get_origin(hint)

    if hint is outputs_module.Image or hint is Image.Image:
        n = _next()
        return Image.new("RGB", (4, 4), color=(n % 255, (n * 3) % 255, (n * 7) % 255))
    if hint is Path:
        suffix = {"video_path": ".mp4", "audio_path": ".wav", "mesh_path": ".glb"}.get(field_name, ".bin")
        path = tmp_path / f"{field_name}-{_next()}{suffix}"
        path.write_bytes(f"fixture bytes {field_name}".encode())
        return path
    if origin is typing.Literal:
        return typing.get_args(hint)[0]
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return _build_dataclass(hint, tmp_path=tmp_path)
    if origin in (list,):
        args = typing.get_args(hint)
        elem_hint = args[0] if args else None
        return [_build_value(elem_hint, field_name=field_name, tmp_path=tmp_path)]
    if origin is tuple:
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return (_build_value(args[0], field_name=field_name, tmp_path=tmp_path),)
        return tuple(_build_value(a, field_name=field_name, tmp_path=tmp_path) for a in args)
    if origin is dict:
        args = typing.get_args(hint)
        value_hint = args[1] if len(args) == 2 else None
        return {"k": _build_value(value_hint, field_name=field_name, tmp_path=tmp_path)}
    if hint is bool:
        return True
    if hint is int:
        return _next()
    if hint is float:
        return _next() + 0.5
    if hint is str:
        return f"{field_name}-{_next()}"
    return f"generic-{_next()}"  # Any, or anything else unrecognized


def _unwrap_optional(hint: Any) -> Any:
    if hint is None:
        return None
    if typing.get_origin(hint) is typing.Union:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return hint


def _all_concrete_subclasses(cls: type) -> list:
    seen: set = set()
    stack = list(cls.__subclasses__())
    result = []
    while stack:
        sub = stack.pop()
        if sub in seen:
            continue
        seen.add(sub)
        stack.extend(sub.__subclasses__())
        result.append(sub)
    return result


_CONCRETE_OUTPUT_TYPES = _all_concrete_subclasses(GenerationOutput)
assert _CONCRETE_OUTPUT_TYPES, "no GenerationOutput subclasses found - src.pipelines.outputs failed to import?"


def _assert_value_round_trips(original: Any, decoded: Any) -> None:
    if isinstance(original, Path):
        assert isinstance(decoded, Path)
        assert original.read_bytes() == decoded.read_bytes()
        return
    if isinstance(original, Image.Image):
        # Image.__eq__ also requires the same concrete class (Image vs.
        # PngImageFile), which round-tripping through a PNG on disk never
        # preserves - compare pixel content instead.
        assert isinstance(decoded, Image.Image)
        assert original.mode == decoded.mode
        assert original.size == decoded.size
        assert original.tobytes() == decoded.tobytes()
        return
    if dataclasses.is_dataclass(original) and not isinstance(original, type):
        assert type(original) is type(decoded)
        for f in dataclasses.fields(original):
            _assert_value_round_trips(getattr(original, f.name), getattr(decoded, f.name))
        return
    if isinstance(original, (list, tuple)):
        assert type(original) is type(decoded), f"{type(original)} != {type(decoded)}"
        assert len(original) == len(decoded)
        for o, d in zip(original, decoded):
            _assert_value_round_trips(o, d)
        return
    if isinstance(original, dict):
        assert original.keys() == decoded.keys()
        for key in original:
            _assert_value_round_trips(original[key], decoded[key])
        return
    assert original == decoded, f"{original!r} != {decoded!r}"


@pytest.mark.parametrize("output_cls", _CONCRETE_OUTPUT_TYPES, ids=[c.__name__ for c in _CONCRETE_OUTPUT_TYPES])
def test_every_concrete_output_type_round_trips(output_cls, tmp_path):
    original = _build_dataclass(output_cls, tmp_path=tmp_path)
    # pipe_id/pipe_name are excluded from the top-level payload by design
    # (decode re-stamps them from the event's own pipe id/type) - pin them to
    # what this test passes into decode_output so the top-level comparison
    # below doesn't spuriously fail on a field the codec deliberately drops.
    original.pipe_id = 7
    original.pipe_name = "test/pipe"

    decoded, payload, artifacts = _round_trip(original, tmp_path)

    assert payload["$type"] == f"{output_cls.__module__}:{output_cls.__qualname__}"
    assert "pipe_id" not in payload and "pipe_name" not in payload
    assert decoded.pipe_id == 7
    assert decoded.pipe_name == "test/pipe"
    _assert_value_round_trips(original, decoded)


# -- specific cases -----------------------------------------------------------

def test_param_output_with_mixed_values_including_path_and_numpy_float(tmp_path):
    numpy = pytest.importorskip("numpy")
    stray_file = tmp_path / "not_media.txt"
    stray_file.write_text("a path inside a generic List[Any] field")

    output = ParamGenerationOutput(name="cfg", values=[numpy.float32(1.5), stray_file, 3, "text", True])
    materialize, _registry = _materializer(tmp_path)

    payload, artifacts = encode_output(output, materialize)

    # A Path outside a Path-typed field is stringified, not materialized -
    # ParamGenerationOutput.values is List[Any].
    assert artifacts == ()
    assert payload["values"] == [1.5, str(stray_file), 3, "text", True]
    assert isinstance(payload["values"][0], float)

    decoded = decode_output(payload, {}, pipe_index=0, pipe_name="p")
    assert decoded.values == [1.5, str(stray_file), 3, "text", True]


def test_gallery_with_temporary_preview_final_image_and_video(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    output = GalleryGenerationOutput(
        images=[
            ImageGenerationOutput(image=Image.new("RGB", (8, 8)), temporary=True, seed=1),
            ImageGenerationOutput(image=Image.new("RGB", (8, 8)), temporary=False, seed=2, derived=True),
        ],
        videos=[VideoGenerationOutput(video_path=video_path, temporary=False, seed=3)],
    )

    decoded, payload, artifacts = _round_trip(output, tmp_path)

    assert len(artifacts) == 3  # 2 images + 1 video, all materialized
    assert isinstance(decoded, GalleryGenerationOutput)
    assert [i.temporary for i in decoded.images] == [True, False]
    assert [i.seed for i in decoded.images] == [1, 2]
    assert decoded.images[1].derived is True
    assert len(decoded.videos) == 1
    assert decoded.videos[0].video_path.read_bytes() == b"fake mp4 bytes"
    assert decoded.videos[0].seed == 3


def test_compare_images_with_nested_pil_images_inside_tuples(tmp_path):
    output = CompareImagesGenerationOutput(
        index=0,
        compare=("before", Image.new("RGB", (5, 5), color=(1, 2, 3))),
        to=("after", Image.new("RGB", (6, 6), color=(4, 5, 6))),
    )

    decoded, _payload, artifacts = _round_trip(output, tmp_path)

    assert len(artifacts) == 2
    assert isinstance(decoded.compare, tuple) and decoded.compare[0] == "before"
    assert decoded.compare[1].size == (5, 5)
    assert isinstance(decoded.to, tuple) and decoded.to[0] == "after"
    assert decoded.to[1].size == (6, 6)


def test_progress_with_icon_and_progress_round_trips(tmp_path):
    output = ProgressGenerationOutput(
        state="denoising", icon=Icon(name="bolt", effect="pulse"), title="Denoising",
        progress=Progress(current=4, max=20),
    )

    decoded, _payload, artifacts = _round_trip(output, tmp_path)

    assert artifacts == ()
    assert decoded.state == "denoising"
    assert decoded.icon == Icon(name="bolt", effect="pulse")
    assert decoded.title == "Denoising"
    assert decoded.progress == Progress(current=4, max=20)


def test_an_unknown_type_not_in_sys_modules_raises_decode_error():
    with pytest.raises(OutputDecodeError):
        decode_output(
            {"$type": "not.a.real.module:Nope", "state": "x"}, {}, pipe_index=0, pipe_name="p",
        )


def test_a_type_resolving_outside_generationoutput_raises_decode_error():
    with pytest.raises(OutputDecodeError):
        decode_output(
            {"$type": "src.pipelines.outputs:Icon", "name": "bolt"}, {}, pipe_index=0, pipe_name="p",
        )


def test_a_nonexistent_media_path_raises_encode_error_naming_the_field(tmp_path):
    output = VideoGenerationOutput(video_path=tmp_path / "never-written.mp4", temporary=False)
    materialize, _registry = _materializer(tmp_path)

    with pytest.raises(OutputEncodeError) as exc_info:
        encode_output(output, materialize)
    assert "video_path" in str(exc_info.value)


def test_an_unencodable_field_value_raises_encode_error_naming_the_field(tmp_path):
    @dataclasses.dataclass
    class _NotOnTheWire(GenerationOutput):
        payload: object  # a plain object has no JSON-safe representation

    materialize, _registry = _materializer(tmp_path)
    output = _NotOnTheWire(payload=object())

    with pytest.raises(OutputEncodeError) as exc_info:
        encode_output(output, materialize)
    assert "_NotOnTheWire.payload" in str(exc_info.value)


def test_encoding_a_non_generation_output_raises():
    materialize, _registry = _materializer(Path("."))
    with pytest.raises(OutputEncodeError):
        encode_output(object(), materialize)  # noqa
