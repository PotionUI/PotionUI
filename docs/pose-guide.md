---
category: Presets / Models
category_order: 70
order: 35
---

# Pose Guide

The **Pose guide** turns a person's movement into a skeleton video that a control model can follow. It
is the `control_video` pipe (`src/pipelines/pipes/control_video/`) on top of a whole-body pose
estimator in the platform layer (`src/platform/runtime/pose/`).

## Models

| File | Role | Source | Licence |
|---|---|---|---|
| `yolox_l.onnx` | person detector | Hugging Face `yzd-v/DWPose` | Apache-2.0 |
| `dw-ll_ucoco_384.onnx` | whole-body pose (133 points: body, feet, hands, face) | Hugging Face `yzd-v/DWPose` | Apache-2.0 |

Both files are fetched on first use into `annotators/yzd-v-dwpose/` of the model depot, and checked
against the size and SHA-256 listed in `src/std/models/pose.yml`. A half-finished download is
reported by name instead of failing inside the runtime.

They run on ONNX Runtime. `requirements.txt` carries the CPU `onnxruntime` package. If the installed
build offers the CUDA execution provider (the `onnxruntime-gpu` package) the estimator uses it,
otherwise it falls back to the CPU. Only one of the two packages may be installed at a time; swap
them with `pip uninstall onnxruntime && pip install onnxruntime-gpu` when the GPU provider is wanted.

## The `control_video` pipe

Input: `video` (the driving clip). Output: `video`, one guide per driving clip with the **same frame count and
frame rate** as the source.

- The person is detected on keyframes (`keyframe_interval`) and followed between them from the
  previous skeleton, so one pose pass per frame is enough. Only one person is tracked: the largest
  on the first keyframe, then the best box overlap.
- Points under `min_confidence` are dropped, never drawn. A joint missing for up to `max_gap`
  frames is interpolated; a frame with no person is interpolated from its neighbours, or holds the
  last pose when the gap is longer.
- `smoothing` runs a One-Euro filter forwards and backwards over every coordinate, so a still pose
  stays still and jitter drops without lag.
- The guide is drawn in the ControlNet/OpenPose whole-body style on black: coloured body limbs and
  joints, hue-ramped hand edges, white face dots. Line widths scale with the guide's short side
  relative to `detect_resolution` (512), so a 544- or 704-pixel guide looks like the 512 one the
  control models were trained on.
- Size: `width` and `height` win; otherwise the clip's size is capped by
  `max_pixels` and snapped to `size_multiple` (32 for LTX and MiniMax-H3). `fit` decides between
  letterboxing and cropping when the shape differs.
- `type` is `pose` today; `depth` and `edges` are reserved for later.
