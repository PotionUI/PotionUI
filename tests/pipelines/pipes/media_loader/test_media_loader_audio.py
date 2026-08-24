"""Tests for the audio passthrough extension of MediaLoaderPipe: `type: "audio"`
media items -> an `audio` output list (IOType.AUDIO), same passthrough
convention as video (path in, path out; no decode).
"""

from __future__ import annotations

from pathlib import Path

from src.pipelines.outputs import AudioGenerationOutput
from src.pipelines.contracts import IOType
from src.pipelines.pipes.media_loader.main import MediaLoaderPipe


def _pipe(**config_over):
    cfg = MediaLoaderPipe.get_default_config()
    cfg.update(config_over)
    return MediaLoaderPipe(config=cfg)


def _touch(tmp_path, name="clip.mp3") -> str:
    path = tmp_path / name
    path.write_bytes(b"fake audio bytes")
    return str(path)


def test_audio_output_spec_declared():
    outputs = {o.name: o for o in MediaLoaderPipe.outputs()}
    assert "audio" in outputs
    assert outputs["audio"].io_type == IOType.AUDIO
    assert outputs["audio"].is_array is True


def test_explicit_audio_type_loads_and_emits(tmp_path):
    path = _touch(tmp_path)
    pipe = _pipe(media=[{"type": "audio", "path": path}], validate_files=True)
    emitted = []
    result = pipe.process(None, emitted.append)

    assert result.output["audio"] == [path]
    assert result.output["image"] == []
    assert result.output["video"] == []
    audio_outputs = [o for o in emitted if isinstance(o, AudioGenerationOutput)]
    assert len(audio_outputs) == 1
    assert audio_outputs[0].audio_path == path


def test_auto_detect_audio_extension(tmp_path):
    path = _touch(tmp_path, name="voice.wav")
    pipe = _pipe(media=[{"path": path}], auto_detect_type=True)  # no explicit type
    result = pipe.process(None, lambda o: None)
    assert result.output["audio"] == [path]


def test_metadata_records_audio_type(tmp_path):
    path = _touch(tmp_path)
    pipe = _pipe(media=[{"type": "audio", "path": path}])
    result = pipe.process(None, lambda o: None)
    meta = result.output["media_metadata"]
    assert len(meta) == 1
    assert meta[0]["type"] == "audio"
    assert meta[0]["path"] == path


def test_no_media_configured_returns_empty_audio_list():
    pipe = _pipe(media=[])
    result = pipe.process(None, lambda o: None)
    assert result.output == {"image": [], "video": [], "audio": [], "media_metadata": []}


def test_mixed_media_list_routes_each_to_its_own_bucket(tmp_path):
    img_path = tmp_path / "pic.png"
    img_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    audio_path = _touch(tmp_path, name="track.flac")
    pipe = _pipe(media=[{"type": "image", "path": str(img_path)}, {"type": "audio", "path": audio_path}])
    result = pipe.process(None, lambda o: None)
    assert len(result.output["image"]) == 1
    assert result.output["audio"] == [audio_path]
