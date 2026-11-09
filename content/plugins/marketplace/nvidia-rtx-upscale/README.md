# NVIDIA RTX Upscale

Runs NVIDIA RTX Video Super Resolution (`nvidia-vfx`, `import nvvfx`) as a native-engine pipe:
`upscaler/rtx_vsr`. This is a per-frame RTX Tensor Core effect, not a diffusion model — no
checkpoint, no prompt, no seed. It exposes four intents (`upscale`, `upscale_highbitrate` for a
high-bitrate or lossless source, `denoise`, `deblur`) at four quality tiers each, for both images and
video. Denoise and deblur are same-resolution enhancement; only the two upscale intents change
output size. The plugin ships one preset, **RTX Upscale** (`upscale` and `video_upscale` modes).

## Requirements

- An NVIDIA RTX GPU, Turing generation or newer.
- An NVIDIA driver `>= 570.190` on Linux (`580.82+` and `590.44+` branches too), `>= 570.65` on
  Windows.
- The `nvidia-vfx` Python package (import name `nvvfx`), 0.1 or 0.2. The pipe is written against
  0.2.0.0, which bundles VFX SDK 1.3.0.0.

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
  "The effect has not been properly initialized (code -12)". Checked on Docker Desktop under WSL2
  (driver 617.42, RTX 4070 Ti). `LD_LIBRARY_PATH` does not help in a container, where
  `/usr/lib/wsl/lib` does not exist.
- **WSL2 outside Docker.** NVIDIA documents no WSL2 support. Users report that the library loads
  only when `LD_LIBRARY_PATH` starts with `/usr/lib/wsl/lib` before Python starts
  (`export LD_LIBRARY_PATH="/usr/lib/wsl/lib:$LD_LIBRARY_PATH"`); without it, `load()` can exit
  the process silently.

The PyPI distribution name (`nvidia-vfx`) differs from its import name (`nvvfx`) — the plugin's
manifest deliberately declares no `dependencies:` block so the generic installer never runs
`pip install nvvfx` against the wrong name. Until `nvidia-vfx` is installed, the `upscaler/rtx_vsr`
pipe stays visible in the catalog marked not-installed, and the RTX Upscale preset's Requirements
tab reports the package missing with the command above.

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

## Tests

`tests/` runs with a fake `nvvfx` module and never touches the GPU: the constructor with and
without `strength`, the error explanations, the size guard and `target_short_side`. The manifest,
the installer check and the preset's form-to-pipe wiring are tested under
`tests/plugins/marketplace/`.

## What's bundled

Nothing from NVIDIA. The plugin contains only the pipe and preset that call `import nvvfx` at
run time — no NVIDIA SDK, wheel, or binary is vendored in this repository; you install
`nvidia-vfx` yourself from NVIDIA's package index.
