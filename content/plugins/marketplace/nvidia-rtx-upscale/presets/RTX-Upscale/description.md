# RTX Upscale

NVIDIA RTX video effects running locally as native pipes. These are Tensor
Core effects, not diffusion models — there is no checkpoint to pick and no
prompt to write, just an image or video.

**Upscale an image or a video** with one of four intents:

- **Upscale** — increase resolution and remove compression artifacts, for
  typical compressed video.
- **Upscale — clean source** — increase resolution without artifact
  suppression, for a high-bitrate or lossless source with few artifacts.
- **Denoise** — same-resolution noise reduction.
- **Deblur** — same-resolution sharpening of blurred detail.

Denoise and Deblur do not change the image's dimensions — the Scale and Target
Short Side controls only apply to the two Upscale intents. Target Short Side
asks for an exact output size (1080 for 1080p) instead of a multiplier.

**Interpolate a video** with NVIDIA Video Frame Generation: double or quadruple
its frame rate, or convert it to 24, 25, 30, 48, 50 or 60 fps. The duration and
the audio stay the same.

Requires an NVIDIA RTX GPU (Turing generation or newer; Ada or Blackwell for
frame generation), an NVIDIA driver `>= 570.190` on Linux (`>= 570.65` on
Windows), and the `nvidia-vfx` Python package, 0.2 or later for frame
generation, installed from NVIDIA's index:
`pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com`.
