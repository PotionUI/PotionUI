---
type: technique
title: Native Face Detailer
category_group: Quality
status: needs-gpu-validation
families: [krea2]
authors: []
paper: null
reference_impl: null
knobs:
  - key: face_detailer_enabled
    surface: preset
    default: false
    effect: "Detects faces in the generated image and refines each one with a low-denoise img2img pass"
  - key: detection_backend
    surface: preset
    default: mediapipe
    effect: "mediapipe (Apache-2.0, default) or yolo (AGPL-3.0, opt-in) face detector"
  - key: denoise
    surface: preset
    default: 0.3
    effect: "img2img denoise strength applied to each face crop"
  - key: steps
    surface: preset
    default: 12
    effect: "Denoising steps per face"
  - key: guidance
    surface: preset
    default: 1.0
    effect: "Tracks the run's own guidance (speed profile / cfg), so distilled profiles stay at 1.0"
  - key: crop_padding
    surface: preset
    default: 0.6
    effect: "Context around each face box, as a fraction of the box's own size"
  - key: crop_size
    surface: preset
    default: 1536
    effect: "Working resolution each face crop is refined at"
  - key: latent_mask
    surface: preset
    default: true
    effect: "Re-pins latents outside the face mask to the original at every sampling step"
  - key: mask_mode
    surface: preset
    default: oval
    effect: "oval follows the landmark face contour; box is a rounded rectangle"
  - key: mask_dilate
    surface: preset
    default: 0.12
    effect: "Grows the mask by this fraction of the face box's short side"
  - key: colour_match
    surface: preset
    default: true
    effect: "Transfers the original crop's masked LAB mean/std onto the refined one"
  - key: max_faces
    surface: preset
    default: 4
    effect: "Caps how many (largest-first) faces are refined per image"
  - key: min_face_ratio
    surface: preset
    default: 0.02
    effect: "Skips faces smaller than this fraction of the frame area"
  - key: max_face_ratio
    surface: preset
    default: 0.6
    effect: "Skips faces wider than this fraction of the frame's short side"
  - key: overlay
    surface: preset
    default: true
    effect: "Draws detection brackets, the mask contour and per-face labels on the live preview only"
  - key: loras_mode
    surface: preset
    default: keep
    effect: "keep / drop / select: what happens to the run's LoRAs during the face pass"
  - key: feather
    surface: preset
    default: 24
    effect: "Blend width (px) between the refined face crop and the rest of the image"
related: []
---

# Native Face Detailer

`detailer/native` (`src/pipelines/pipes/detailer/native/`) is a family-agnostic face detailer for
the native engine: after a generator produces an image, it detects faces, crops a padded square
around each one, refines the crop with a low-denoise img2img pass using the run's own model bundle
and conditioning, then feathers the refined crop back into the full image. There is no native
inpainting primitive, so this crop/refine/feather/paste sequence plays that role, the same pattern
the SDXL detailer (`detailer/sdxl`) and the LTX video detailer (`detailer/video_ltx`) already use for
their own families.

It reuses the same family-agnostic mechanism as the tiled hi-res refiner: `build_native_generator`
builds the correct generator subclass for the run's model bundle, and the shared `img2img_denoise`
helper does the encode → sample → decode work. No new dependency, no model reload — the crop is
refined with whatever DiT/VAE/text encoder the generation itself already loaded.

## When to use it

For a generated portrait or group shot where faces come out slightly soft or malformed relative to
the rest of the image — a common diffusion failure mode at typical output resolutions, where a face
occupies a small fraction of the frame. Refining just the face region at its own working resolution
recovers detail without re-rendering (and potentially drifting) the whole image.

## How it works

1. Detect faces in the full image (face-only for v1; hands/eyes/teeth are out of scope, unlike the
   SDXL detailer). The MediaPipe backend returns the dense landmark set alongside each box; the
   YOLO backend returns boxes only.
2. Drop faces smaller than `min_face_ratio` of the frame area and larger than `max_face_ratio` of
   its short side, then keep only the largest `max_faces`.
3. Pad each remaining box by `crop_padding` (a fraction of the box's own size) and square it, so the
   crop encodes cleanly at the model's own resolution granularity.
4. Build the face mask over that crop (see *Face-oval mask* below).
5. Resize the crop to `crop_size` (snapped to the family's pixel granularity via
   `NativeGenerator.snap_resolution`), then run `img2img_denoise` at `denoise` strength for `steps`
   steps — the run's own prompt conditioning, unchanged (a dedicated per-face prompt is out of
   scope) — with the mask-guided latent blend installed (see *Mask-guided latent inpainting*).
6. Resize the refined crop back to its original crop size, colour-match it to the original crop
   (see *Colour match*), then paste it back through the same feathered mask so the blend has no
   hard seam.

A face seed is derived deterministically from the image's own seed (`seed + face_index +
seed_offset`), so re-running the same generation refines the same faces the same way. No faces
detected, or `denoise <= 0`, is a passthrough: the image is emitted unchanged so the gallery wiring
stays stable either way.

## Mask-guided latent inpainting

A plain crop-and-refine moves every latent in the crop, and the feathered paste then hides the
difference at the edge. That is what makes a detailed face read as pasted on: inside the feather
band the background, hair and neck have quietly become a *different* image that happens to be
blended in.

This pass removes the drift instead of hiding it. The flow-matching families define the noisy state
at a given sigma as `x_t = (1 - sigma) * x0 + sigma * eps`. The pipe already has `x0` — the
VAE-encoded original crop that `img2img_denoise` starts from — and it now supplies `eps` itself, the
same seeded initial noise the sampler uses, so that identity reconstructs the untouched crop's own
trajectory at any sigma on the schedule. After every sampler step the running latent becomes

```
x = mask * x + (1 - mask) * ((1 - sigma) * x0 + sigma * eps)
```

so outside the mask the latent is pinned back on the original's trajectory exactly, and the model
is only ever allowed to move what the mask admits. The mask is the soft pixel mask area-averaged
down to the encoded latent's own spatial size, so a latent at mask 0.5 travels half as far from the
original as one at 1.0 — differential diffusion, which is what keeps the mask edge from becoming a
new seam of its own.

The seam this hangs off is `LatentFilter` in
`src/platform/runtime/native/sampling/hooks.py`: an optional `filter_latent(step, total, x, sigma)`
that a step hook may define to replace the latent, dispatched by `apply_latent_filters` from every
sampler right after its own step update. A hook that only observes does not define the method, so
every existing run is byte-identical. The filter itself is
`src/pipelines/pipes/detailer/native/latent_blend.py`.

`latent_mask: false` restores the plain whole-crop refine, which is the A/B control.

## Face-oval mask

The MediaPipe backend's landmarks carry a face-oval contour (`FACEMESH_FACE_OVAL`), read from the
installed package at runtime and walked from its unordered connection pairs into a closed ring. That
ring is filled as a polygon, grown by `mask_dilate` (a fraction of the face box's short side, so it
scales with the face rather than the output resolution), and Gaussian-feathered over a band about
`feather` px wide. The result drives both the latent blend and the final composite, so hair,
background and the neck inside a padded crop are never candidates for redrawing.

`mask_mode: box` — and the YOLO backend, which has no landmarks to offer — produces a rounded
rectangle over the detected box with the same dilate and feather, which is the pre-existing
behaviour with softened corners.

## Colour match

A low-denoise refine still shifts a face's exposure and saturation slightly, and on a face that is
otherwise identical to its surroundings that shift is exactly what the eye picks up. Before
compositing, the refined crop's per-channel mean and standard deviation in CIE L\*a\*b\* are
matched to the original crop's, measured over the masked region only so a bright background inside
the crop cannot drag the face's own exposure. The correction is applied through the same mask
weights, so a pixel the mask excludes comes back bit-identical. The conversion runs in float
(`src/pipelines/pipes/detailer/native/colour.py`) rather than through an 8-bit colour-space
convert, which would put a visible step into a low-contrast skin gradient.

## Detection overlay

The workbench preview is annotated so the pass is legible while it runs
(`src/pipelines/pipes/detailer/native/overlay.py`, pure PIL, no new dependency):

It has two modes, because one full marker per face is unreadable as soon as there are two.

**Light** is the detection frame, emitted once after detection and before any refining, and never
again. Every
detected face gets thin 2px corner brackets in the signal-blue token value (`rgb(91 157 255)`, from
`frontend/src/lib/styles/tokens.css`) at 75% alpha, plus a tiny index tag (`1`, `2`, …) inside the
box's top-left corner. No dim, no contour, no pill. Faces dropped by `min_face_ratio` or
`max_face_ratio` get the same brackets and tag in grey, reading `skip`.

**Focus** is every frame emitted while a face is being refined: one opening frame as the face
starts, one per sampling step, and the composite handing over to the next face. Exactly one face is
marked, the active one, and no other face is annotated at all: 3px brackets over a darker outline,
its mask contour as a dashed line at 60% alpha, and one label pill reading
`FACE 1 · 0.93 · 412x412 · OVAL · step 4/12`, that is index, detector confidence, padded-crop size,
the mask shape actually used and the live step. The background is darkened 25%, but every other
detected face stays lit through its own mask, so a face that is not being worked on is pixel-identical
to the untouched image. The opening frame is what replaces the detection frame the moment the first
face starts, and the detection frame is never re-emitted.

The pill is placed by `place_label`, a pure helper that tries above, below, right and left in turn,
rejecting any position that leaves the frame or overlaps another face's box. When none of the four
fits, the label is wrapped onto two lines and the four sides are tried again.

Confidence is per-backend. The YOLO detector reports a real score, read through
`DetailerHelper.detect_objects_scored`. MediaPipe's FaceLandmarker result exposes landmarks only
and carries no per-face score, so its labels show `-`.

The label font is `PIL.ImageFont.load_default(size=...)`, sized at 2.2% of the frame's short edge
with a 12px floor. The repository ships IBM Plex Mono as WOFF2 only, which PIL cannot read, so the
labels render in Pillow's bundled proportional face rather than a mono one, and the label text is
restricted to glyphs that face actually covers.

`overlay: false` turns all of it off. The overlay only ever touches emitted preview copies: the
image the pipe returns, the composite emitted after the last face, the saved output and the
before/after compare artifact never carry it.

## LoRAs on the face pass

A character LoRA active on the run applies to every face the detailer refines, so a crowd scene
comes back with one person's face on everybody. `loras_mode` decides what the face pass runs under:

- `keep` (default) leaves the run's stack exactly as the loader baked it.
- `drop` clears it before the first face and restores it after the last.
- `select` swaps in the stack from `face_detailer_lora_picker` for the faces, then restores the
  run's.

The swap goes through `sync_loras`
(`src/pipelines/pipes/_shared/generation/loader_lifecycle.py`), the same in-place reconciliation the
model loader uses for a cache-hit DiT whose LoRA request changed. That buys the safety properties
for free: the applied-stack stamp is cleared before the first mutation and only rewritten once the
requested stack is fully applied, the weight revision moves before every mutation so a cache keyed
on the old weights cannot answer, a failure rolls back to the base checkpoint weights, and a failed
rollback marks the wrapper unusable rather than sampling weights nobody asked for. Any failure
surfaces as a plain error naming which half failed.

The scope opens lazily, on the first face actually refined, so an image with no detected face never
pays for a swap. Restoring re-applies the stack rather than reloading the checkpoint, which is the
same cost as the original apply and nowhere near a disk read; it happens once per generation.

Step-windowed LoRAs are excluded from the restore, because the loader never baked them into the
module in the first place: the sampler toggles them per step, and the face pass is its own sample
call. A window on a face-pass LoRA is refused rather than silently baked, matching `active_loras`'s
own contract.

## How to enable it

Wired into the Krea-2 `txt2img` mode's "Face Detailer" tab
(`content/presets/marketplace/Krea2/modes/txt2img/tabs/face-detailer.yml`), gated by
`face_detailer_enabled` (default off). In `modes/txt2img/pipeline.yml` it runs after the optional
Enhance stage and before `gallery`, taking whichever of `enhancer`/`generator` actually produced the
image, and emits its own `compare_faces` before/after artifact the same way `compare_enhance` does
for the Enhance pass.

## Tradeoffs and limitations

- **Face-only for v1** — no hand/eye/teeth detailing (see `detailer/sdxl` for that shape on SDXL).
- **Detector licensing**: the default `mediapipe` backend is Apache-2.0; the opt-in `yolo` backend
  reuses the shared AGPL-3.0 YOLO detector and must stay explicit, never the default.
- **No dedicated per-face prompt** — every face is refined against the same whole-image
  conditioning the base generation used.
- Adds one extra img2img pass per detected face, so generation time scales with face count.
- **Oval masks need MediaPipe** — the YOLO backend has no landmarks, so it always falls back to the
  rounded-rectangle mask regardless of `mask_mode`.
- The latent blend assumes the flow-matching noise convention every native family shares; a family
  on a different parameterisation would need its own re-noise identity.
- Needs GPU validation — implemented and wired, but not yet run against a real model on hardware.
