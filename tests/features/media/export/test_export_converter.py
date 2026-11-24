from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from src.features.media.export import (
    CRF_BY_CODEC,
    ConvertError,
    ExportCache,
    ExportConverter,
    FormatUnavailable,
    ImageSpec,
    VideoSpec,
    cache_key,
    parse_encoders,
    probe_image_support,
)
from src.features.media.export.image import convert_image
from src.features.media.export.jobs import CANCELLED, DONE, FAILED
from src.features.media.export.probe import FormatSupport, video_support
from src.features.media.export.specs import crf_for
from src.features.media.export.video import build_command
from src.platform.util.video_transcode import find_ffmpeg

CANNED = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC (codec h264)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 VFS... prores_ks            Apple ProRes (iCodec Pro) (codec prores)
 V....D gif                  GIF (Graphics Interchange Format)
 VF...D png                  PNG (Portable Network Graphics) image
 A....D aac                  AAC (Advanced Audio Coding)
"""


def _noise(size=(64, 64), mode="RGB"):
    return Image.effect_noise(size, 80).convert(mode)


@pytest.fixture
def rgba_png(tmp_path):
    path = tmp_path / "a.png"
    image = Image.new("RGBA", (40, 20), (255, 0, 0, 0))
    image.paste((0, 0, 255, 255), (0, 0, 20, 20))
    image.save(path)
    return path


def test_probe_parses_canned_and_reports_missing_av1():
    names = parse_encoders(CANNED)
    assert {"libx264", "libx265", "prores_ks", "gif", "png", "aac"} <= names
    support = video_support(names)
    assert support["mp4_h264"].available and support["mp4_h264"].encoder == "libx264"
    assert not support["mp4_av1"].available
    assert support["mp4_av1"].reason == "No AV1 encoder in this ffmpeg build"
    assert support["png_sequence_zip"].available


def test_probe_prefers_svtav1_over_aom():
    names = parse_encoders(" V..... libaom-av1  x\n V..... libsvtav1  y\n")
    assert video_support(names)["mp4_av1"].encoder == "libsvtav1"
    assert video_support(parse_encoders(" V..... libaom-av1  x\n"))["mp4_av1"].encoder == "libaom-av1"


def test_probe_without_ffmpeg_reports_reason():
    from src.features.media.export.probe import probe_video_support

    support = probe_video_support(None)
    assert all(not item.available and item.reason for item in support.values())


def test_crf_mapping_orders_levels_and_rejects_unknown():
    for codec, table in CRF_BY_CODEC.items():
        assert table["draft"] > table["standard"] > table["high"] > table["lossless"] == 0
        assert crf_for(codec, "high") == table["high"]
    with pytest.raises(ConvertError):
        crf_for("h264", "ultra")


def test_command_uses_mapped_crf_and_flags():
    support = video_support(parse_encoders(CANNED))
    cmd = build_command("ffmpeg", Path("in.mp4"), Path("out.part"), VideoSpec("mp4_h264", "high"), support["mp4_h264"])
    assert cmd[cmd.index("-crf") + 1] == str(CRF_BY_CODEC["h264"]["high"])
    assert "-shell" not in cmd and cmd[0] == "ffmpeg"
    cmd = build_command("ffmpeg", Path("in.mp4"), Path("o"), VideoSpec("mp4_h265", "lossless", include_audio=False), support["mp4_h265"])
    assert "lossless=1" in cmd[cmd.index("-x265-params") + 1] and "-an" in cmd
    cmd = build_command("ffmpeg", Path("in.mp4"), Path("o"), VideoSpec("mov_prores", "lossless", keep_alpha=True), support["mov_prores"])
    assert "yuva444p10le" in cmd
    with pytest.raises(FormatUnavailable):
        build_command("ffmpeg", Path("i"), Path("o"), VideoSpec("mp4_av1"), support["mp4_av1"])


def test_spec_validation():
    with pytest.raises(ConvertError):
        ImageSpec("bmp").validate()
    with pytest.raises(ConvertError):
        ImageSpec("png", fill_colour="red").validate()
    with pytest.raises(ConvertError):
        VideoSpec("gif", fps=0).validate()


def test_alpha_to_jpeg_uses_fill(tmp_path, rgba_png):
    out = convert_image(rgba_png, tmp_path / "o.jpg", ImageSpec("jpeg", fill_colour="#00ff00"))
    with Image.open(out) as image:
        assert image.mode == "RGB"
        r, g, b = image.getpixel((35, 10))
        assert g > 200 and r < 60 and b < 60
        r, g, b = image.getpixel((5, 10))
        assert b > 200


def test_alpha_kept_for_png_and_webp(tmp_path, rgba_png):
    for fmt in ("png", "webp"):
        out = convert_image(rgba_png, tmp_path / f"o.{fmt}", ImageSpec(fmt, quality=100))
        with Image.open(out) as image:
            assert image.mode == "RGBA"
            assert image.getpixel((35, 10))[3] == 0
    out = convert_image(rgba_png, tmp_path / "flat.png", ImageSpec("png", keep_alpha=False))
    with Image.open(out) as image:
        assert image.mode == "RGB"


def test_scale_and_quality(tmp_path):
    src = tmp_path / "n.png"
    _noise((128, 128)).save(src)
    low = convert_image(src, tmp_path / "l.jpg", ImageSpec("jpeg", quality=10))
    high = convert_image(src, tmp_path / "h.jpg", ImageSpec("jpeg", quality=95))
    assert low.stat().st_size < high.stat().st_size
    half = convert_image(src, tmp_path / "s.webp", ImageSpec("webp", scale=0.5))
    with Image.open(half) as image:
        assert image.size == (64, 64)


def test_avif_roundtrip_or_unavailable(tmp_path):
    src = tmp_path / "n.png"
    _noise().save(src)
    if probe_image_support()["avif"].available:
        out = convert_image(src, tmp_path / "o.avif", ImageSpec("avif", quality=60))
        assert out.stat().st_size > 0
    else:
        with pytest.raises(FormatUnavailable):
            convert_image(src, tmp_path / "o.avif", ImageSpec("avif"))


def test_cache_hit_miss_and_key_changes_with_source_and_options(tmp_path):
    src = tmp_path / "s.png"
    _noise().save(src)
    converter = ExportConverter(ExportCache(tmp_path / "cache"), ffmpeg_finder=lambda: None)
    first = converter.convert_image(src, ImageSpec("webp", quality=70))
    stamp = first.stat().st_mtime_ns
    again = converter.convert_image(src, ImageSpec("webp", quality=70))
    assert again == first and again.stat().st_mtime_ns >= stamp
    other = converter.convert_image(src, ImageSpec("webp", quality=71))
    assert other != first
    old_key = cache_key(src, ImageSpec("webp").options())
    _noise((70, 70)).save(src)
    assert cache_key(src, ImageSpec("webp").options()) != old_key
    assert not list((tmp_path / "cache").rglob("*.part"))


def test_cache_lru_eviction_keeps_newest(tmp_path):
    cache = ExportCache(tmp_path / "c", max_bytes=2500)
    paths = []
    for index in range(4):
        key = f"{index:02x}" + "a" * 62
        part = cache.part_path(key, "bin")
        part.write_bytes(b"x" * 1000)
        os.utime(part, (1000 + index, 1000 + index))
        paths.append(cache.commit(part, key, "bin"))
        os.utime(paths[-1], (1000 + index, 1000 + index))
    assert not paths[0].exists() and not paths[1].exists()
    assert paths[3].exists() and paths[2].exists()
    assert cache.total_bytes() <= 2500


def test_cache_get_refreshes_recency(tmp_path):
    cache = ExportCache(tmp_path / "c", max_bytes=2500)
    old_key, new_key = "aa" + "0" * 62, "bb" + "0" * 62
    for key, stamp in ((old_key, 1000), (new_key, 2000)):
        part = cache.part_path(key, "bin")
        part.write_bytes(b"x" * 1000)
        final = cache.commit(part, key, "bin")
        os.utime(final, (stamp, stamp))
    assert cache.get(old_key, "bin")
    third = "cc" + "0" * 62
    part = cache.part_path(third, "bin")
    part.write_bytes(b"x" * 1000)
    cache.commit(part, third, "bin")
    assert cache.final_path(old_key, "bin").exists()
    assert not cache.final_path(new_key, "bin").exists()


def test_stale_part_files_are_swept(tmp_path):
    cache = ExportCache(tmp_path / "c", max_bytes=10)
    part = cache.part_path("dd" + "0" * 62, "bin")
    part.write_bytes(b"x")
    os.utime(part, (1000, 1000))
    cache.evict()
    assert not part.exists()


FAKE_SLOW = (
    "import sys,time\n"
    "for i in range(1000):\n"
    "    print('out_time_us=%d' % (i*100000)); sys.stdout.flush(); time.sleep(0.05)\n"
)

FAKE_FAST = (
    "import sys,pathlib\n"
    "out=sys.argv[-1]; pathlib.Path(out).write_bytes(b'data')\n"
    "print('out_time_us=500000'); print('progress=end')\n"
)

FAKE_FAIL = "import sys\nsys.stderr.write('boom\\n'); sys.exit(3)\n"


def _fake_popen(script):
    def factory(command, **kwargs):
        return subprocess.Popen([sys.executable, "-c", script, *command[-1:]], **kwargs)

    return factory


def _converter(tmp_path, script):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    converter = ExportConverter(
        ExportCache(tmp_path / "cache"),
        ffmpeg_finder=lambda: "ffmpeg-fake",
        popen=_fake_popen(script),
    )
    return src, converter


@pytest.fixture
def all_supported(monkeypatch):
    names = parse_encoders(CANNED + " V..... libsvtav1  x\n")
    monkeypatch.setattr("src.features.media.export.service.probe_video_support", lambda ffmpeg: video_support(names))
    monkeypatch.setattr("src.features.media.export.video.probe_duration", lambda f, s: 10.0)


def test_job_runs_reports_progress_and_caches(tmp_path, all_supported):
    src, converter = _converter(tmp_path, FAKE_FAST)
    job = converter.convert_video(src, VideoSpec("mp4_h264"))
    done = converter.jobs.wait(job.id)
    assert done.status == DONE and done.progress == 1.0
    assert done.result.read_bytes() == b"data"
    assert not list((tmp_path / "cache").rglob("*.part*"))
    again = converter.jobs.wait(converter.convert_video(src, VideoSpec("mp4_h264")).id)
    assert again.result == done.result


def test_job_failure_reports_error_and_cleans_part(tmp_path, all_supported):
    src, converter = _converter(tmp_path, FAKE_FAIL)
    job = converter.jobs.wait(converter.convert_video(src, VideoSpec("mp4_h264")).id)
    assert job.status == FAILED and "boom" in job.error
    assert not list((tmp_path / "cache").rglob("*.part*"))


def test_cancel_kills_process_and_cleans_part(tmp_path, all_supported):
    src, converter = _converter(tmp_path, FAKE_SLOW)
    spawned = []
    inner = converter._popen

    def tracking(command, **kwargs):
        process = inner(command, **kwargs)
        spawned.append(process)
        return process

    converter._popen = tracking
    job = converter.convert_video(src, VideoSpec("mp4_h264"))
    deadline = time.monotonic() + 10
    while job.progress <= 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert job.progress > 0
    assert converter.cancel(job.id)
    finished = converter.jobs.wait(job.id, timeout=10)
    assert finished.status == CANCELLED
    assert spawned[0].poll() is not None
    assert not list((tmp_path / "cache").rglob("*.part*"))
    assert not converter.cancel(job.id)


def test_unavailable_format_rejected_before_job(tmp_path, monkeypatch):
    names = parse_encoders(CANNED)
    monkeypatch.setattr("src.features.media.export.service.probe_video_support", lambda ffmpeg: video_support(names))
    src, converter = _converter(tmp_path, FAKE_FAST)
    with pytest.raises(FormatUnavailable, match="No AV1"):
        converter.convert_video(src, VideoSpec("mp4_av1"))


ffmpeg_path = find_ffmpeg()


@pytest.mark.skipif(not ffmpeg_path, reason="ffmpeg not available")
def test_real_tiny_clip_conversions(tmp_path):
    clip = tmp_path / "clip.mp4"
    made = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
         "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True,
    )
    if made.returncode != 0:
        pytest.skip("ffmpeg cannot synthesize clip")
    converter = ExportConverter(ExportCache(tmp_path / "cache"))
    caps = converter.capabilities()["video"]
    results = {}
    for fmt in ("mp4_h264", "gif", "png_sequence_zip", "mov_prores", "mp4_h265"):
        if not caps[fmt]["available"]:
            continue
        job = converter.jobs.wait(converter.convert_video(clip, VideoSpec(fmt, "draft", include_audio=False)).id, timeout=60)
        assert job.status == DONE, (fmt, job.error)
        results[fmt] = job.result
    assert results
    if "png_sequence_zip" in results:
        with zipfile.ZipFile(results["png_sequence_zip"]) as archive:
            assert len(archive.namelist()) >= 9
