# RTX Upscale

NVIDIA RTX Video Super Resolution running locally as a native pipe. This is a
Tensor Core effect, not a diffusion model — there is no checkpoint to pick and
no prompt to write, just an image or video and four intents:

- **Upscale** — increase resolution and remove compression artifacts, for
  typical compressed video.
- **Upscale — clean source** — increase resolution without artifact
  suppression, for a high-bitrate or lossless source with few artifacts.
- **Denoise** — same-resolution noise reduction.
- **Deblur** — same-resolution sharpening of blurred detail.

Denoise and Deblur do not change the image's dimensions — the Scale and Target
Short Side controls only apply to the two Upscale intents. Target Short Side
asks for an exact output size (1080 for 1080p) instead of a multiplier.

Requires an NVIDIA RTX GPU (Turing generation or newer), an NVIDIA driver
`>= 570.190` on Linux (`>= 570.65` on Windows), and the `nvidia-vfx` Python
package, installed from NVIDIA's index:
`pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com`.
