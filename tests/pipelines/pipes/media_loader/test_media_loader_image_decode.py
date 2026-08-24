"""Tests for image decode hardening in MediaLoaderPipe._load_image / _decode_image:
force a full pixel decode at load time (instead of PIL's lazy decode blowing up
downstream), tolerate truncated images with a loud warning, and raise a clear,
self-diagnosing error for genuinely undecodable files. Configured items that
cannot be validated or decoded must be fatal, not silently skipped.
"""

from __future__ import annotations

import io
import threading

import numpy as np
import pytest
from PIL import Image, ImageFile

from src.pipelines.pipes.media_loader.main import MediaLoaderPipe


def _pipe(**config_over):
    cfg = MediaLoaderPipe.get_default_config()
    cfg.update(config_over)
    return MediaLoaderPipe(config=cfg)


def _write_png(path, size=(16, 16), color=(200, 50, 50)):
    Image.new("RGB", size, color).save(path, format="PNG")


def _write_jpeg_bytes(size=(64, 64), color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _write_noisy_jpeg_bytes(size=(256, 256)) -> bytes:
    # High-entropy, multi-MCU content so a 30% tail truncation lands mid-scan
    # rather than wiping out the whole (tiny, single-block) image - that is
    # what makes LOAD_TRUNCATED_IMAGES actually able to recover partial data.
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_valid_png_loads_and_is_fully_decoded(tmp_path):
    path = tmp_path / "valid.png"
    _write_png(path)

    pipe = _pipe(media=[{"type": "image", "path": str(path)}])
    emitted = []
    result = pipe.process(None, emitted.append)

    assert len(result.output["image"]) == 1
    image = result.output["image"][0]
    # Fully decoded: no lazy-decode exception should be possible here.
    arr = np.asarray(image)
    assert arr.shape[0] == 16 and arr.shape[1] == 16
    assert image.tobytes()


def test_valid_rgb_jpeg_loads_and_is_fully_decoded(tmp_path):
    path = tmp_path / "valid.jpg"
    path.write_bytes(_write_jpeg_bytes())

    pipe = _pipe(media=[{"type": "image", "path": str(path)}])
    result = pipe.process(None, lambda o: None)

    assert len(result.output["image"]) == 1
    image = result.output["image"][0]
    assert image.mode == "RGB"
    arr = np.asarray(image)
    assert arr.size > 0
    assert image.tobytes()


def test_truncated_jpeg_is_tolerated_with_warning(tmp_path, caplog):
    full_bytes = _write_noisy_jpeg_bytes()
    truncated_bytes = full_bytes[: int(len(full_bytes) * 0.7)]
    path = tmp_path / "truncated.jpg"
    path.write_bytes(truncated_bytes)

    original_flag = ImageFile.LOAD_TRUNCATED_IMAGES
    assert original_flag is False  # sanity: default PIL state

    pipe = _pipe(media=[{"type": "image", "path": str(path)}])
    with caplog.at_level("WARNING"):
        result = pipe.process(None, lambda o: None)

    assert len(result.output["image"]) == 1
    image = result.output["image"][0]
    assert image is not None

    assert any(
        "truncated" in rec.message.lower() and str(path) in rec.message
        for rec in caplog.records
    )

    # Global PIL state must be restored after the tolerant retry.
    assert ImageFile.LOAD_TRUNCATED_IMAGES == original_flag


def test_loaded_image_is_fully_decoded_and_owns_no_lazy_file_state(tmp_path):
    # Primary, deterministic guard for the real root cause (a thread race, not
    # truncation): a lazily-opened PIL Image keeps its file handle open
    # (`image.fp`) with no decoded pixel buffer (`image.im`) until first use.
    # This pipe runs on a worker thread (InProcessBackend drives pipes via
    # asyncio.to_thread) while the very same image object is concurrently
    # base64-encoded for a preview on the event-loop thread
    # (OutputBridge -> image_handler.create_base64_image). If the image were
    # still lazy when handed off, both threads would drive PIL's decoder over
    # one open file object at once and corrupt the decode - which is exactly
    # what produced the user's "unrecognized data stream contents" error on a
    # perfectly valid PNG. Once `image.load()` has run, PIL guarantees `im` is
    # populated and `fp` is cleared (see PIL.Image.Image.load), so the image is
    # self-contained and safe to hand to another thread. This must hold even
    # for a plain, already-RGB PNG - the exact case the old code skipped.
    #
    # Calls `_load_image` directly (not `process()`): `process()` wraps the
    # image in `ImageGenerationOutput`, which independently forces its own
    # eager decode in `__post_init__` (src/pipelines/outputs.py) - that
    # second safety net would mask a regression in media_loader's own decode.
    path = tmp_path / "plain_rgb.png"
    _write_png(path, size=(64, 64))

    pipe = _pipe()
    image = pipe._load_image(str(path), lambda o: None)

    assert image.im is not None
    assert image.fp is None


def test_concurrent_threaded_access_does_not_corrupt_decode(tmp_path):
    # Mirrors the actual race that produced the bug report: one thread doing
    # img.save(BytesIO(), format="PNG") (what create_base64_image does for the
    # preview, on the event-loop thread) concurrently with another thread doing
    # np.asarray(img.convert("RGB")) (what _prep_start_frame does, on the pipe's
    # worker thread) - both driving PIL's decoder over the SAME Image object. A
    # lazily-opened image races and corrupts; a fully-decoded image (image.im
    # populated, image.fp cleared) is safe because both operations then only
    # read the already-decoded pixel buffer. Uses a large, high-entropy image
    # so the decode window is wide, and re-loads a FRESH image per iteration
    # (not one image reused 20x) since a single race can leave an already
    # lazily-opened image in a state where later races don't re-trigger it.
    #
    # Calls `_load_image` directly, for the same reason as the test above: the
    # `ImageGenerationOutput.__post_init__` safety net in outputs.py would
    # otherwise force the decode itself and hide a regression here.
    rng = np.random.default_rng(2)
    arr = rng.integers(0, 256, size=(1072, 1920, 3), dtype=np.uint8)
    path = tmp_path / "large_rgb.png"
    Image.fromarray(arr, mode="RGB").save(path, format="PNG")

    errors = []

    for _ in range(20):
        pipe = _pipe()
        image = pipe._load_image(str(path), lambda o: None)

        def _encode_preview(img=image):
            try:
                img.save(io.BytesIO(), format="PNG")
            except Exception as e:  # noqa: BLE001 - collecting for the assertion below
                errors.append(e)

        def _prep_start_frame(img=image):
            try:
                np.asarray(img.convert("RGB"))
            except Exception as e:  # noqa: BLE001 - collecting for the assertion below
                errors.append(e)

        threads = [
            threading.Thread(target=_encode_preview),
            threading.Thread(target=_prep_start_frame),
            threading.Thread(target=_encode_preview),
            threading.Thread(target=_prep_start_frame),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == [], f"cross-thread decode corrupted the image: {errors}"


def test_truncated_rgb_png_is_tolerated_with_warning(tmp_path, caplog):
    # Regression for the real-world bug report: a truncated PNG whose mode is
    # already RGB (so the old code's "convert to RGB if necessary" branch never
    # ran .convert()/.load(), and the file "loaded" successfully while pixel
    # data was never actually decoded - the OSError only surfaced much later in
    # a downstream generator's convert("RGB") call). The forced image.load() in
    # _decode_image must catch this BEFORE any mode-based branching, i.e. on
    # every image regardless of its (already-RGB) mode.
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 256, size=(256, 256, 3), dtype=np.uint8)
    full_bytes_buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(full_bytes_buf, format="PNG")
    full_bytes = full_bytes_buf.getvalue()
    truncated_bytes = full_bytes[: int(len(full_bytes) * 0.7)]

    path = tmp_path / "truncated_rgb.png"
    path.write_bytes(truncated_bytes)

    # Sanity: PIL reports RGB mode on open, before any decode is forced.
    with Image.open(path) as probe:
        assert probe.mode == "RGB"

    original_flag = ImageFile.LOAD_TRUNCATED_IMAGES
    pipe = _pipe(media=[{"type": "image", "path": str(path)}])
    with caplog.at_level("WARNING"):
        result = pipe.process(None, lambda o: None)

    assert len(result.output["image"]) == 1
    image = result.output["image"][0]
    assert image.mode == "RGB"
    assert image.tobytes()

    assert any(
        "partially" in rec.message.lower() and str(path) in rec.message
        for rec in caplog.records
    )
    assert ImageFile.LOAD_TRUNCATED_IMAGES == original_flag


def test_garbage_bytes_named_png_raises_self_diagnosing_error(tmp_path):
    path = tmp_path / "garbage.png"
    path.write_bytes(b"not a real png file, just garbage bytes padding out")

    pipe = _pipe(media=[{"type": "image", "path": str(path)}])
    with pytest.raises(OSError) as exc_info:
        pipe.process(None, lambda o: None)

    message = str(exc_info.value)
    assert str(path.resolve()) in message
    # First 12 bytes as hex should be present for self-diagnosis.
    expected_hex = path.read_bytes()[:12].hex()
    assert expected_hex in message


def test_missing_configured_file_raises_with_path(tmp_path):
    missing_path = tmp_path / "does_not_exist.png"

    pipe = _pipe(media=[{"type": "image", "path": str(missing_path)}], validate_files=True)
    with pytest.raises(OSError) as exc_info:
        pipe.process(None, lambda o: None)

    assert str(missing_path.resolve()) in str(exc_info.value)
