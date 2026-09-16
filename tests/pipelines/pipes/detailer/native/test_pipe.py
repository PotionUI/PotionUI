import types
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import CompareImagesGenerationOutput, ImageGenerationOutput
from src.pipelines.pipes.detailer.native.detection import FaceDetection
from src.pipelines.pipes.detailer.native.latent_blend import LatentInpaintFilter
from src.pipelines.pipes.detailer.native.main import DetailerNativePipe


class _FakeGen:
    spec = types.SimpleNamespace(family="fake", variant="fake")

    def __init__(self):
        self.encoded = []

    def snap_resolution(self, w, h):
        return (256, 256)

    def encode_image(self, image, **kwargs):
        self.encoded.append(np.asarray(image).shape)
        return torch.zeros(1, 16, 32, 32)


class _FakeDetector:
    def __init__(self, boxes, landmarks=None, confidence=None):
        self.boxes = list(boxes)
        self.landmarks = landmarks
        self.confidence = confidence
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return [FaceDetection(box=tuple(float(v) for v in box), landmarks=self.landmarks,
                              confidence=self.confidence)
                for box in self.boxes]


def _solid_denoise(color):
    def _denoise(gen, crop, conditioning, **kwargs):
        out = np.zeros_like(np.asarray(crop))
        out[...] = color
        return out[None]

    return _denoise


def _run(config, image, detector, denoise_fn=None, seeds=None, generator=None):
    pipe = DetailerNativePipe(config)
    generator = generator or _FakeGen()
    outputs = []
    cond = [types.SimpleNamespace(embeds={}, n_embeds=None)]
    pipe_input = PipeInput(input={
        "image": [image],
        "model": types.SimpleNamespace(
            dit=types.SimpleNamespace(estimated_vram_gb=1.0),
            te_encoder="TE", vae="VAE",
            spec=types.SimpleNamespace(family="fake"),
        ),
        "conditioning": cond,
        "seed": seeds if seeds is not None else [1000],
    })
    denoise_fn = denoise_fn or _solid_denoise((200, 200, 200))
    with patch("src.pipelines.pipes.detailer.native.main.build_native_generator", return_value=generator), \
         patch("src.pipelines.pipes.detailer.native.main.build_face_detector", return_value=detector) as mock_build, \
         patch("src.pipelines.pipes.detailer.native.main.img2img_denoise", side_effect=denoise_fn) as mock_denoise:
        result = pipe.process(pipe_input, outputs.append)
    return result, outputs, mock_denoise, mock_build


def _base_config(**overrides):
    config = DetailerNativePipe.get_default_config()
    config["device"] = "cpu"
    config["face_model"] = "models/mediapipe/face_landmarker.task"
    config.update(overrides)
    return config


def test_no_faces_detected_is_passthrough():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([])
    result, outputs, mock_denoise, _ = _run(_base_config(), img, detector)

    out_images = result.output["image"]
    assert len(out_images) == 1
    assert out_images[0].size == img.size
    assert np.array_equal(np.asarray(out_images[0]), np.asarray(img))
    mock_denoise.assert_not_called()
    assert not any(isinstance(o, CompareImagesGenerationOutput) for o in outputs)


def test_one_face_only_padded_region_changes():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    config = _base_config(colour_match=False)
    result, outputs, mock_denoise, _ = _run(config, img, detector)

    out_img = np.asarray(result.output["image"][0])
    orig = np.asarray(img)

    assert tuple(out_img[10, 10]) == tuple(orig[10, 10])
    assert tuple(out_img[200, 200]) == tuple(orig[200, 200])
    assert all(abs(int(v) - 200) <= 2 for v in out_img[125, 125])

    mock_denoise.assert_called_once()
    assert mock_denoise.call_args.kwargs["seed"] == 1000
    assert any(isinstance(o, CompareImagesGenerationOutput) for o in outputs)


def test_max_faces_respected():
    img = Image.new("RGB", (512, 512), (10, 10, 10))
    boxes = [
        (10 + i * 80, 10, 10 + i * 80 + 80, 90) for i in range(6)
    ]
    detector = _FakeDetector(boxes)
    config = _base_config(max_faces=4)
    result, outputs, mock_denoise, _ = _run(config, img, detector)

    assert mock_denoise.call_count == 4
    compares = [o for o in outputs if isinstance(o, CompareImagesGenerationOutput)]
    assert len(compares) == 4


def test_min_face_ratio_filters_small_faces():
    img = Image.new("RGB", (256, 256), (80, 90, 100))
    detector = _FakeDetector([(0, 0, 10, 10)])
    result, outputs, mock_denoise, _ = _run(_base_config(), img, detector)

    assert np.array_equal(np.asarray(result.output["image"][0]), np.asarray(img))
    mock_denoise.assert_not_called()


def test_denoise_zero_is_passthrough_and_skips_detection():
    img = Image.new("RGB", (256, 256), (1, 2, 3))
    detector = _FakeDetector([(100, 100, 150, 150)])
    config = _base_config(denoise=0.0)
    result, outputs, mock_denoise, mock_build = _run(config, img, detector)

    assert np.array_equal(np.asarray(result.output["image"][0]), np.asarray(img))
    mock_denoise.assert_not_called()
    mock_build.assert_not_called()
    assert detector.calls == 0


def test_missing_selected_model_raises_instead_of_falling_back():
    img = Image.new("RGB", (256, 256), (1, 2, 3))
    detector = _FakeDetector([(100, 100, 150, 150)])
    config = _base_config(face_model=None)
    with pytest.raises(ValueError, match="mediapipe"):
        _run(config, img, detector)


def test_missing_yolo_model_raises_when_backend_is_yolo():
    img = Image.new("RGB", (256, 256), (1, 2, 3))
    detector = _FakeDetector([(100, 100, 150, 150)])
    config = _base_config(detection_backend="yolo", yolo_model=None)
    with pytest.raises(ValueError, match="yolo"):
        _run(config, img, detector)


def test_progress_emits_temporary_visualization_and_composite_before_compare():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    result, outputs, mock_denoise, _ = _run(_base_config(), img, detector)

    compare_index = next(i for i, o in enumerate(outputs) if isinstance(o, CompareImagesGenerationOutput))
    preceding = outputs[:compare_index]
    image_outputs = [o for o in preceding if isinstance(o, ImageGenerationOutput)]

    assert len(image_outputs) >= 2
    assert all(o.temporary for o in image_outputs)


def test_seed_per_face_is_deterministic_with_offset():
    img = Image.new("RGB", (256, 256), (5, 5, 5))
    boxes = [(10, 10, 100, 100), (150, 150, 240, 240)]
    detector = _FakeDetector(boxes)
    config = _base_config(seed_offset=5)
    result, outputs, mock_denoise, _ = _run(config, img, detector, seeds=[42])

    seeds_used = [call.kwargs["seed"] for call in mock_denoise.call_args_list]
    assert seeds_used == [42 + 0 + 5, 42 + 1 + 5]


def _ellipse_landmarks(cx, cy, radius, count=24):
    import math

    return [
        (cx + radius * math.cos(2 * math.pi * i / count), cy + radius * math.sin(2 * math.pi * i / count))
        for i in range(count)
    ]


def _hooks_of(mock_denoise):
    return mock_denoise.call_args.kwargs["hooks"]


def test_latent_mask_installs_the_inpaint_filter_and_reuses_the_encoded_latent():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    generator = _FakeGen()
    _, _, mock_denoise, _ = _run(_base_config(), img, detector, generator=generator)

    kwargs = mock_denoise.call_args.kwargs
    assert len(generator.encoded) == 1
    assert kwargs["init_latent"].shape == (1, 16, 32, 32)
    assert kwargs["noise"].shape == (1, 16, 32, 32)
    filters = [h for h in _hooks_of(mock_denoise) if isinstance(h, LatentInpaintFilter)]
    assert len(filters) == 1


def test_latent_mask_off_keeps_the_previous_encode_once_inside_img2img_path():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    generator = _FakeGen()
    config = _base_config(latent_mask=False)
    _, _, mock_denoise, _ = _run(config, img, detector, generator=generator)

    kwargs = mock_denoise.call_args.kwargs
    assert generator.encoded == []
    assert "init_latent" not in kwargs and "noise" not in kwargs
    assert not any(isinstance(h, LatentInpaintFilter) for h in _hooks_of(mock_denoise))


def test_latent_noise_is_reproducible_for_a_seed():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    _, _, first, _ = _run(_base_config(), img, detector, seeds=[77])
    _, _, second, _ = _run(_base_config(), img, detector, seeds=[77])
    _, _, other, _ = _run(_base_config(), img, detector, seeds=[78])

    assert torch.equal(first.call_args.kwargs["noise"], second.call_args.kwargs["noise"])
    assert not torch.equal(first.call_args.kwargs["noise"], other.call_args.kwargs["noise"])


def test_max_face_ratio_skips_a_face_that_already_fills_the_frame():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(20, 20, 236, 236), (10, 10, 50, 50)])
    _, _, mock_denoise, _ = _run(_base_config(), img, detector)

    assert mock_denoise.call_count == 1


def test_max_face_ratio_of_zero_keeps_every_face():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(20, 20, 236, 236), (10, 10, 50, 50)])
    _, _, mock_denoise, _ = _run(_base_config(max_face_ratio=0.0), img, detector)

    assert mock_denoise.call_count == 2


def test_oval_mask_leaves_the_crop_corners_alone_where_box_mode_paints_them():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    landmarks = _ellipse_landmarks(125, 125, 25)
    detector = _FakeDetector([(100, 100, 150, 150)], landmarks=landmarks)
    shared = dict(colour_match=False, feather=0, mask_dilate=0.0)

    with patch("src.pipelines.pipes.detailer.native.main.mediapipe_oval_ring",
               return_value=tuple(range(len(landmarks)))):
        oval, _, _, _ = _run(_base_config(mask_mode="oval", **shared), img, detector)
        box, _, _, _ = _run(_base_config(mask_mode="box", **shared), img, detector)

    oval_img = np.asarray(oval.output["image"][0])
    box_img = np.asarray(box.output["image"][0])

    assert tuple(oval_img[145, 145]) == (50, 60, 70)
    assert tuple(box_img[145, 145]) == (200, 200, 200)
    assert tuple(oval_img[125, 125]) == (200, 200, 200)


def test_yolo_backend_never_asks_for_landmarks():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    config = _base_config(detection_backend="yolo", yolo_model="models/face_yolov8n.pt")

    with patch("src.pipelines.pipes.detailer.native.main.mediapipe_oval_ring") as ring:
        _run(config, img, detector)

    ring.assert_called_once()
    assert all(face.landmarks is None for face in detector.detect(img))


def test_colour_match_pulls_the_refined_face_back_to_the_original_tone():
    img = Image.new("RGB", (256, 256), (40, 90, 160))
    detector = _FakeDetector([(100, 100, 150, 150)])

    matched, _, _, _ = _run(_base_config(feather=0), img, detector)
    plain, _, _, _ = _run(_base_config(feather=0, colour_match=False), img, detector)

    matched_centre = np.asarray(matched.output["image"][0])[125, 125].astype(int)
    plain_centre = np.asarray(plain.output["image"][0])[125, 125].astype(int)

    assert tuple(plain_centre) == (200, 200, 200)
    assert np.abs(matched_centre - np.array([40, 90, 160])).max() <= 2


def _preview_capture():
    captured = {}

    def fake_hook(spec, emit, **kwargs):
        captured["emit"] = emit
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(marker="preview")

    return captured, fake_hook


def _denoise_firing_previews(captured, count, colour=(10, 240, 10)):
    def _denoise(gen, crop, conditioning, **kwargs):
        for _ in range(count):
            captured["emit"](Image.new("RGB", (64, 64), colour))
        out = np.zeros_like(np.asarray(crop))
        out[...] = 200
        return out[None]

    return _denoise


def _image_outputs_before_compare(outputs):
    compare_index = next(i for i, o in enumerate(outputs) if isinstance(o, CompareImagesGenerationOutput))
    return [o for o in outputs[:compare_index] if isinstance(o, ImageGenerationOutput)]


def test_live_preview_pastes_the_face_into_the_full_composite():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    captured, fake_hook = _preview_capture()

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", side_effect=fake_hook):
        _, outputs, mock_denoise, _ = _run(
            _base_config(colour_match=False, overlay=False), img, detector,
            denoise_fn=_denoise_firing_previews(captured, 3),
        )

    emitted = _image_outputs_before_compare(outputs)
    assert all(o.temporary for o in emitted)
    previews = [o for o in emitted if np.asarray(o.image)[125, 125][1] > 200]
    assert len(previews) == 3

    for output in previews:
        frame = np.asarray(output.image)
        assert output.image.size == img.size
        assert tuple(frame[10, 10]) == (50, 60, 70)
        assert tuple(frame[200, 200]) == (50, 60, 70)

    assert any(isinstance(h, types.SimpleNamespace) for h in _hooks_of(mock_denoise))


def test_live_preview_uses_the_shared_cadence():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    captured, fake_hook = _preview_capture()

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", side_effect=fake_hook) as hook:
        _run(_base_config(), img, detector, denoise_fn=_denoise_firing_previews(captured, 1))

    hook.assert_called_once()
    assert captured["kwargs"] == {}


def test_no_preview_hook_leaves_the_existing_emits_untouched():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", return_value=None):
        _, outputs, mock_denoise, _ = _run(_base_config(), img, detector)

    assert len(_image_outputs_before_compare(outputs)) == 3
    assert any(isinstance(h, LatentInpaintFilter) for h in _hooks_of(mock_denoise))


def _capture_overlays():
    seen = []

    def fake_render(frame, faces, **kwargs):
        seen.append(list(faces))
        return frame.copy()

    return seen, fake_render


def _denoise_stepping_previews(captured, count, total=12):
    from src.platform.runtime.native.sampling import ProgressHook

    def _denoise(gen, crop, conditioning, **kwargs):
        progress_hooks = [h for h in kwargs["hooks"] if isinstance(h, ProgressHook)]
        for i in range(count):
            for hook in progress_hooks:
                hook.on_step(i, total, None, 0.0, None)
            captured["emit"](Image.new("RGB", (32, 32), (10, 240, 10)))
        out = np.zeros_like(np.asarray(crop))
        out[...] = 200
        return out[None]

    return _denoise


def test_overlay_off_emits_the_untouched_detection_frame():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])

    _, outputs, _, _ = _run(_base_config(overlay=False), img, detector)

    detection_frame = _image_outputs_before_compare(outputs)[0]
    assert np.array_equal(np.asarray(detection_frame.image), np.asarray(img))


def _two_face_detector():
    return _FakeDetector([(40, 40, 140, 140), (320, 320, 420, 420)], confidence=0.87)


def test_detection_frame_is_all_light_tags_and_no_pill():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    seen, fake_render = _capture_overlays()

    with patch("src.pipelines.pipes.detailer.native.main.render_overlay", side_effect=fake_render):
        _run(_base_config(), img, _two_face_detector())

    detection = seen[0]
    assert sorted(f.tag for f in detection) == ["1", "2"]
    assert all(f.label == "" for f in detection)
    assert not any(f.focus for f in detection)


def test_skipped_faces_get_a_grey_skip_tag():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(20, 20, 236, 236), (100, 100, 150, 150), (0, 0, 8, 8)])
    seen, fake_render = _capture_overlays()

    with patch("src.pipelines.pipes.detailer.native.main.render_overlay", side_effect=fake_render):
        _run(_base_config(), img, detector)

    skipped = [f for f in seen[0] if f.skipped]
    assert [f.tag for f in skipped] == ["skip", "skip"]
    assert all(f.mask is None for f in skipped)


def test_focus_frame_carries_exactly_one_pill_on_the_active_face():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    captured, fake_hook = _preview_capture()
    seen, fake_render = _capture_overlays()

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", side_effect=fake_hook), \
         patch("src.pipelines.pipes.detailer.native.main.render_overlay", side_effect=fake_render):
        _run(_base_config(), img, _two_face_detector(),
             denoise_fn=_denoise_stepping_previews(captured, 1))

    for faces in seen[1:]:
        assert sum(1 for f in faces if f.focus) == 1
        assert sum(1 for f in faces if f.label) == 1
        assert all(not f.tag and not f.done for f in faces if not f.focus)

    assert seen[1][0].label.startswith("FACE 1 · 0.87")


def test_second_face_focus_never_marks_the_first_face():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    captured, fake_hook = _preview_capture()
    seen, fake_render = _capture_overlays()

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", side_effect=fake_hook), \
         patch("src.pipelines.pipes.detailer.native.main.render_overlay", side_effect=fake_render):
        _run(_base_config(), img, _two_face_detector(),
             denoise_fn=_denoise_stepping_previews(captured, 1))

    second = next(faces for faces in seen[1:] if faces[0].label.startswith("FACE 2"))
    assert second[0].box == (260, 260, 480, 480)
    assert [f.box for f in second[1:]] == [(0, 0, 200, 200)]
    assert all(not f.focus and not f.tag and not f.label and not f.done for f in second[1:])


def test_focus_frame_leaves_the_other_face_pixel_identical():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    captured, fake_hook = _preview_capture()
    frames = []

    def record(image, **meta):
        frames.append(image)

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", side_effect=fake_hook):
        result, outputs, _, _ = _run(
            _base_config(colour_match=False), img, _two_face_detector(),
            denoise_fn=_denoise_stepping_previews(captured, 2),
        )

    final = np.asarray(result.output["image"][0])
    face_two_frames = [
        np.asarray(o.image) for o in outputs
        if isinstance(o, ImageGenerationOutput) and np.asarray(o.image)[370, 370][1] > 180
    ]
    assert face_two_frames
    for frame in face_two_frames:
        assert np.array_equal(frame[70:110, 70:110], final[70:110, 70:110])


def test_preview_label_step_suffix_increments():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    detector = _FakeDetector([(100, 100, 150, 150)])
    captured, fake_hook = _preview_capture()
    seen, fake_render = _capture_overlays()

    with patch("src.pipelines.pipes.detailer.native.main.make_preview_hook", side_effect=fake_hook), \
         patch("src.pipelines.pipes.detailer.native.main.render_overlay", side_effect=fake_render):
        _run(_base_config(), img, detector, denoise_fn=_denoise_stepping_previews(captured, 3))

    stepped = [faces[0].label for faces in seen if faces and " · step " in faces[0].label]
    assert [label.rsplit(" · ", 1)[-1] for label in stepped] == ["step 1/12", "step 2/12", "step 3/12"]


def test_final_composite_after_the_last_face_carries_no_overlay():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    seen, fake_render = _capture_overlays()

    with patch("src.pipelines.pipes.detailer.native.main.render_overlay", side_effect=fake_render):
        result, outputs, _, _ = _run(_base_config(colour_match=False), img, _two_face_detector())

    assert len(seen) == 4
    assert all(sum(1 for f in faces if f.focus) == 1 for faces in seen[1:])
    last_image = [o for o in outputs if isinstance(o, ImageGenerationOutput)][-1]
    assert np.array_equal(np.asarray(last_image.image), np.asarray(result.output["image"][0]))


def _sync_recorder():
    calls = []

    def _sync(dit_model, loras, lora_fp, apply, **kwargs):
        calls.append([lora["file_path"] for lora in loras])

    return calls, _sync


RUN_STACK = [{"file_path": "run.safetensors", "weight": 0.8}]


def test_drop_mode_clears_before_the_first_face_and_restores_after_the_last():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    calls, sync = _sync_recorder()
    config = _base_config(loras_mode="drop", run_loras=RUN_STACK)

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=sync):
        _run(config, img, _two_face_detector())

    assert calls == [[], ["run.safetensors"]]


def test_select_mode_applies_the_face_stack_then_restores_the_run_stack():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    calls, sync = _sync_recorder()
    config = _base_config(
        loras_mode="select",
        loras=[{"file_path": "skin.safetensors", "weight": 0.5}],
        run_loras=RUN_STACK,
    )

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=sync):
        _run(config, img, _two_face_detector())

    assert calls == [["skin.safetensors"], ["run.safetensors"]]


def test_keep_mode_never_switches_loras():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    calls, sync = _sync_recorder()

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=sync):
        _run(_base_config(run_loras=RUN_STACK), img, _two_face_detector())

    assert calls == []


def test_no_detected_face_never_switches_loras():
    img = Image.new("RGB", (256, 256), (50, 60, 70))
    calls, sync = _sync_recorder()
    config = _base_config(loras_mode="drop", run_loras=RUN_STACK)

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=sync):
        _run(config, img, _FakeDetector([]))

    assert calls == []


def test_a_failing_lora_switch_raises_out_of_the_pipe():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(loras_mode="drop", run_loras=RUN_STACK)

    def _sync(dit_model, loras, lora_fp, apply, **kwargs):
        raise RuntimeError("apply blew up")

    with patch("src.pipelines.pipes.detailer.native.loras.sync_loras", side_effect=_sync):
        with pytest.raises(RuntimeError, match="could not switch LoRAs"):
            _run(config, img, _two_face_detector())
