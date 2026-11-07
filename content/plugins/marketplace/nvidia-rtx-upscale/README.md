# NVIDIA RTX Upscale

Runs two NVIDIA RTX video effects from `nvidia-vfx` (`import nvvfx`) as native-engine pipes.
Both are per-frame RTX Tensor Core effects, not diffusion models — no checkpoint, no prompt, no
seed.

- **`upscaler/rtx_vsr`** runs RTX Video Super Resolution. It exposes four intents (`upscale`,
  `upscale_highbitrate` for a high-bitrate or lossless source, `denoise`, `deblur`) at four
  quality tiers each, for both images and video. Denoise and deblur are same-resolution
  enhancement; only the two upscale intents change output size.
- **`interpolator/rtx_vfg`** runs RTX Video Frame Generation on a video: it multiplies the frame
  rate by 2 or 4, or converts it to a given frame rate, and keeps the duration and the audio.

The plugin ships one preset, **RTX Upscale**, with three modes: `upscale` (image),
`video_upscale` and `interpolate` (video).

## Requirements

- An NVIDIA RTX GPU: Turing generation or newer for Video Super Resolution; **Ada or Blackwell**
  (Hopper too on Linux) for Video Frame Generation, as the VFX SDK 1.3 documentation lists.
- An NVIDIA driver `>= 570.190` on Linux (`580.82+` and `590.44+` branches too), `>= 570.65` on
  Windows.
- The `nvidia-vfx` Python package (import name `nvvfx`). Video Super Resolution runs on 0.1 or
  0.2; Video Frame Generation came in **0.2**. The pipes are written against 0.2.0.0, which
  bundles VFX SDK 1.3.0.0.

## Install

```bash
pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com
```

- **The extra index is needed.** pypi.org only carries source stubs of a few kB (3.7 kB for
  0.2.0.0); the wheels (about 443 MB for Linux x86_64, 436 MB for Windows x64) are on
  `pypi.nvidia.com`.
- **The package is proprietary** (`LicenseRef-NvidiaProprietary`, with NVIDIA's license
  agreements inside the wheel): installing it is the administrator's decision under NVIDIA's
  terms. Nothing from NVIDIA is vendored here, and PotionUI never installs it by itself.
- **Docker.** The container must request the `ngx` driver capability:
  `NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx` (or `all`). The PotionUI image inherits
  `compute,utility` from its CUDA base image, and the NVIDIA container runtime only passes the
  driver libraries of the requested capabilities. Video Super Resolution needs the driver's
  `libnvidia-ngx.so.1`: with only `compute,utility`, `import nvvfx` works but `load()` fails with
  "The effect has not been properly initialized (code -12)". Video Frame Generation loads without
  it. Checked on Docker Desktop under WSL2 (driver 617.42, RTX 4070 Ti). `LD_LIBRARY_PATH` does
  not help in a container, where `/usr/lib/wsl/lib` does not exist.
- **WSL2 outside Docker.** NVIDIA documents no WSL2 support. Users report that the library loads
  only when `LD_LIBRARY_PATH` starts with `/usr/lib/wsl/lib` before Python starts
  (`export LD_LIBRARY_PATH="/usr/lib/wsl/lib:$LD_LIBRARY_PATH"`); without it, `load()` can exit
  the process silently.

The PyPI distribution name (`nvidia-vfx`) differs from its import name (`nvvfx`) — the plugin's
manifest deliberately declares no `dependencies:` block so the generic installer never runs
`pip install nvvfx` against the wrong name. Until `nvidia-vfx` is installed, the `upscaler/rtx_vsr`
and `interpolator/rtx_vfg` pipes stay visible in the catalog marked not-installed, and the RTX
Upscale preset's Requirements tab reports the package missing with the command above.

## Upscale

| Field | Values | What it does |
|---|---|---|
| `intent` | `upscale`, `upscale_highbitrate`, `denoise`, `deblur` | The effect run on each frame. NVIDIA's standard upscale removes compression artifacts; its high-bitrate upscale skips that step to keep the detail of a clean source. |
| `quality` | `low`, `medium`, `high`, `ultra` | Its tier. |
| `scale` | 1 to 4 | Output size per side for the two upscale intents. |
| `target_short_side` | 0, or pixels | When set, the scale that brings the short side to this size, held between 1 and 4, replaces `scale`. 1080 gives 1872×1080 from 832×480. |
| `strength` | 0 to 1 (default 1) | The SDK's effect strength. 1 is NVIDIA's default and is not sent; a `nvvfx` whose `VideoSuperRes` takes no strength runs at full strength, with a warning in the log. |
| `keep_audio` | yes or no | Video only: the source audio is muxed back. |

- Outputs land on the 8 px grid `nvvfx` requires, and an upscale never lands below the source
  size: each side is at least the source side rounded up to the grid (a source side of 834 used
  to give 832 at scale 1, which the SDK rejects).
- The frame copies to and from the GPU run on the default CUDA stream, the one the SDK uses when
  no `stream_ptr` is given, so they stay ordered with its input transfer and output conversion
  (NVIDIA's notes on DLPack and CUDA streams).
- An SDK failure is reported with the source and target sizes. A rejected frame size (error −7 or
  −16), an effect that cannot initialize (−12, the missing `ngx` capability) and an unsupported
  GPU or driver (−17 to −19) are each explained.

A caller that wants an exact size sends, for `video_upscale`: `intent: "upscale"`,
`quality: "ultra"`, `target_short_side: <pixels>`, `keep_audio: true` and `input_video`.

## Interpolate

| Field | Values | What it does |
|---|---|---|
| `target_fps` | 0 (Multiplier), 24, 25, 30, 48, 50, 60 | The output frame rate; 0 uses `factor`. |
| `factor` | 2, 4 | Frame-rate multiplier when `target_fps` is 0. |
| `mode` | `low`, `medium`, `high` | The Video Frame Generation model; Medium is NVIDIA's default. |
| `scene_detection` | yes (default) or no | The SDK's automatic shot-change detection. |
| `keep_audio` | yes or no | The source audio is muxed back. |

**Which frames are made.** For N source frames at `src_fps` and an output at `out_fps`:

- the output has `max(1, round(N × out_fps / src_fps))` frames, so the duration is kept;
- output frame k falls at source position `p = k × src_fps / out_fps`, between frame
  `i = floor(p + 1e-6)` and the next, at `t = p − i`;
- it is source frame i itself when `t < 1e-3`, the last source frame when `i ≥ N − 1`, and
  otherwise the frame the SDK makes at `t` between frames i and i + 1;
- with a multiplier, `out_fps = src_fps × factor`: N × factor frames, the last source frame held
  for the slots after it, as the core's `interpolator/rife` does. The SDK then runs in its
  multiplier mode (`frame_multiplier`, `frame_index`); a target frame rate uses
  `run_at_timestep`.

So 16 → 24 fps keeps one source frame in three and makes the two others at t = 1/3 and 2/3,
24 → 60 makes four frames in five at t = 0.2, 0.4, 0.6 and 0.8, and 25 → 60 makes eleven in
twelve. Frames are read and written as a stream: only the two source frames around the current
output frame are held in memory. A failure mid-clip aborts the encoder and removes the partial
file; a cancellation keeps the frames written so far, as `upscaler/rtx_vsr` does.

## Tests

`tests/` runs with a fake `nvvfx` module and never touches the GPU: the constructor with and
without `strength`, the error explanations, the size guard and `target_short_side`; for frame
generation, the frame plan (16 → 24, 24 → 60, 25 → 60, 25 → 24, ×2, ×4, the last frame held), the
SDK calls, the cleanup after a failure, and a full pass over a small synthetic video with and
without sound (frame count, frame rate and duration through ffprobe). The manifest, the
installer check of both pipes and the preset's form-to-pipe wiring are tested under
`tests/plugins/marketplace/`.

## What's bundled

Nothing from NVIDIA. The plugin contains only the pipes and preset that call `import nvvfx` at
run time — no NVIDIA SDK, wheel, or binary is vendored in this repository; you install
`nvidia-vfx` yourself from NVIDIA's package index.
