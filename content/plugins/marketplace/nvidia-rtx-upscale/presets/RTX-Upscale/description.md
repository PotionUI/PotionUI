# RTX Upscale

NVIDIA RTX Video Super Resolution running locally as a native pipe. This is a
Tensor Core effect, not a diffusion model — there is no checkpoint to pick and
no prompt to write, just an image or video and four intents:

- **Upscale** — increase resolution, tuned for a clean source.
- **Upscale — compressed source** — increase resolution, tuned for a heavily
  compressed or lossy source (streamed video, old encodes).
- **Denoise** — same-resolution noise reduction.
- **Deblur** — same-resolution sharpening of blurred detail.

Denoise and Deblur do not change the image's dimensions — the Scale control
only applies to the two Upscale intents.

Requires an NVIDIA RTX GPU (Turing generation or newer), a Linux NVIDIA driver
`>= 570.190`, and the `nvidia-vfx` Python package.
