# NVIDIA RTX Upscale

Runs NVIDIA RTX Video Super Resolution (`nvidia-vfx`, `import nvvfx`) as a native-engine pipe:
`upscaler/rtx_vsr`. This is a per-frame RTX Tensor Core effect, not a diffusion model — no
checkpoint, no prompt, no seed. It exposes four intents (`upscale`, `upscale_highbitrate` for a
compressed/lossy source, `denoise`, `deblur`) at four quality tiers each, for both images and
video. Denoise and deblur are same-resolution enhancement; only the two upscale intents change
output size. The plugin ships one preset, **RTX Upscale** (`upscale` and `video_upscale` modes).

## Requirements

- An NVIDIA RTX GPU, Turing generation or newer.
- A Linux NVIDIA driver `>= 570.190`. `nvidia-vfx` ships a Linux-only wheel — there is no
  Windows or macOS build.
- The `nvidia-vfx` Python package (import name `nvvfx`).

## Install

```bash
pip install nvidia-vfx
```

The PyPI distribution name (`nvidia-vfx`) differs from its import name (`nvvfx`), and the wheel
is a several-hundred-MB download from `pypi.nvidia.com` — the plugin's manifest deliberately
declares no `dependencies:` block so the generic installer never runs `pip install nvvfx`
against the wrong name. Until `nvidia-vfx` is installed, the `upscaler/rtx_vsr` pipe stays
visible in the catalog marked not-installed, and the RTX Upscale preset's Requirements tab
reports it missing with the command above.

## What's bundled

Nothing from NVIDIA. The plugin contains only the pipe and preset that call `import nvvfx` at
run time — no NVIDIA SDK, wheel, or binary is vendored in this repository; you install
`nvidia-vfx` yourself from NVIDIA's package index.
