---
category: Presets / Models
category_order: 70
order: 35
---

# Pose Guide

The **Pose guide** turns a person's movement into a skeleton video that a control model can follow. It
is the `control_video` pipe (`src/pipelines/pipes/control_video/`) on top of a whole-body pose
estimator in the platform layer (`src/platform/runtime/pose/`). The same estimator renders the
`openpose` type of `controlnet_preprocessor`, so the core no longer loads the CMU-derived OpenPose
checkpoint.

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
otherwise it falls back to the CPU. The chosen providers are logged when each model loads. Only one of
the two packages may be installed at a time. For CUDA pose, uninstall `onnxruntime` and install
`onnxruntime-gpu` (`pip uninstall onnxruntime && pip install onnxruntime-gpu`); `requirements.txt`
keeps the CPU package.

## The `control_video` pipe

Inputs: `video` (the driving clip) and, optionally, `image` (a picture of the person who should
perform the motion). Output: `video`, one guide per driving clip with the **same frame count and
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
- Size: `width` and `height` win; otherwise the picture's (or the clip's) size is capped by
  `max_pixels` and snapped to `size_multiple` (32 for LTX and MiniMax-H3). `fit` decides between
  letterboxing and cropping when the shape differs.
- `type` is `pose`, `depth` or `edges`; the last two are described under [Depth and edges](#depth-and-edges).

### Matching a picture

With an `image` input and `match_picture` on (the default), the driver's skeleton is retargeted onto
the person in the picture:

1. The picture is run through the same estimator to get the subject's skeleton.
2. Every driver bone keeps its angle and its foreshortening but takes the subject's bone length. The
   per-bone scale is the subject-over-driver ratio of a high percentile of the driver's bone length
   over the clip, so a tall driver and a short subject end up with the subject's proportions.
   A subject limb that looks short in the picture (pointing at the camera) borrows the length of
   its mirror limb.
3. The neck is the root. It starts on the subject's neck in the picture and then follows the driver's
   neck path scaled by the subject's size.
4. Hands follow the wrist and the face follows the nose, scaled like the arm and the head.
5. Joints the picture does not show (a half-length portrait) are still drawn from the driver, scaled
   by the visible part of the subject.

`ease_in` blends the first seconds from the picture's own pose into the motion, so the first frame
of the guide agrees with a picture used as the first frame of the video.

## Core `openpose` preprocessor

`controlnet_preprocessor` with `type: openpose` renders the same skeleton for a single image
(`include_body`, `include_hand`, `include_face` choose the parts), so the SDXL and Qwen-Image
Control modes keep their option and get the Apache-2.0 estimator.

## Depth and edges

`type: depth` and `type: edges` turn the driving video into a grey depth map or a canny outline video instead of a skeleton. Neither draws a person, so neither reads or retargets a picture: an `image` input is ignored, and the guide keeps the clip's own frame (size and fit rules as above, with `max_pixels` and `size_multiple` snapping).

- `edges` runs OpenCV Canny on each frame at the guide size. `canny_low` and `canny_high` (100 and 200 by default) are its two thresholds. White outlines on black, no model download.
- `depth` runs Video Depth Anything Small (Apache-2.0, vendored under `vendor/VideoDepthAnything`; the Base and Large sizes are non-commercial and are never used). The 116 MB checkpoint `video_depth_anything_vits.pth` is fetched on first use through the asset service into `annotators/depth-anything-video-depth-anything-small/` and verified by size and SHA-256. A `weights_dir` pointing at a folder that already holds it skips the fetch.
- The network sees 32-frame windows with a 10-frame overlap. Each later window is shifted and scaled onto the previous one through the shared keyframes, and the overlap is cross-faded, so depth does not pump between windows. The depth values are stretched between the 2nd and 98th percentile of the whole clip and written as bright for near, dark for far.
- The depth model is cached through the model lifecycle (`MODELS`, about 1.5 GB of VRAM budgeted), so it is evictable like any other model. It runs on the GPU when there is one and is moved back to the CPU when the pipe finishes, so it never competes with the generator for VRAM. On CPU it works but takes much longer.
- Frames are read and depth is produced one 32-frame window at a time and kept as float16 at the guide size; the guide is then streamed to the encoder. A depth guide is capped at 1001 frames when `max_frames` is 0.
- Frame count and rate follow the clip, like the pose guide. `max_frames` and `max_seconds` apply to every type.
