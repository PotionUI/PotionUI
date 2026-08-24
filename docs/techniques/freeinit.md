---
type: technique
title: FreeInit
category_group: Quality
status: needs-gpu-validation
families: [wan, ltx]
authors: []
paper: {arxiv: "2312.07537", title: "FreeInit: Bridging Initialization Gap in Video Diffusion Models"}
reference_impl: {name: "TianxingWu/FreeInit", url: "https://github.com/TianxingWu/FreeInit", license: "MIT"}
knobs:
  - key: freeinit_iterations
    surface: preset
    default: 0
    effect: "Extra full denoise passes that re-noise and refine the previous pass's output to reduce temporal flicker; 0 = off."
  - key: freeinit_cutoff
    surface: preset
    default: 0.25
    effect: "Normalized frequency cutoff separating the low-frequency band kept from the high-frequency band replaced with fresh noise each iteration."
  - key: freeinit_order
    surface: preset
    default: 4
    effect: "Steepness of the frequency-domain filter used to split low/high bands (higher = closer to a hard cutoff)."
related: [riflex]
---

# FreeInit

Video diffusion models are trained on plain random noise that has no structure from one frame to the next. But a real video, once you noise it forward toward pure noise, doesn't actually end up as pure independent noise per frame — because the underlying frames are correlated, the noised version still carries real frame-to-frame correlation, especially in its low-frequency (broad, slow-moving) content. Starting generation from ordinary independent noise, as models normally do, misses that correlation, which is one source of the flicker and inconsistency that shows up between frames in generated video.

FreeInit closes this gap by iterating: run a full denoise pass to get a clean video, then re-noise that result back up to the starting noise level, but do it in frequency space rather than all at once — keep the low-frequency band of the re-noised result (which now carries genuine temporal structure from an actual generated video) and replace its high-frequency band with a fresh random draw (a denoised video's high frequencies tend to be oversmoothed, so reusing them would compound that rather than help). That blended noise becomes the starting point for another full denoise pass. Each additional iteration produces a video whose noise initialization is a little closer to what a "real" noised video would look like, which reduces flicker.

`freeinit_cutoff` sets where the low/high frequency split happens, and `freeinit_order` sets how sharp that split is (a Butterworth-style filter, not a hard boundary — even at `freeinit_order` values that make it steep, some blending happens at the cutoff itself).

## When to use it

- Use it when generated video shows visible flicker or per-frame inconsistency that isn't explained by other settings (motion, prompt, guidance scale).
- Start with `freeinit_iterations: 1` or `2` — published results explore 3-5 iterations, but each one re-runs the entire sampling schedule, so cost rises fast. Two iterations is a reasonable practical ceiling for most use.
- Skip it entirely when generation time is constrained, since the cost scales linearly and unavoidably with iteration count.

## How to enable it

```yaml
- name: "generator/txt2vid_wan22"
  configuration:
    steps: 30
    freeinit_iterations: 2
    freeinit_cutoff: 0.25
    freeinit_order: 4
```

Also available on `generator/txt2vid_ltx` with the same three keys.

## Tradeoffs and limitations

- Cost multiplies generation time by roughly `1 + freeinit_iterations` — this is not a free quality dial, it's a direct tradeoff of time for temporal consistency. Two iterations means roughly three times the sampling cost of a single pass.
- Only wired into the plain text-to-video generator pipes (`generator/txt2vid_wan22`, `generator/txt2vid_ltx`); the image-to-video and chained Wan pipes (`generator/img2vid_wan22`, `generator/chain_video_wan22`) and the LTX image-conditioned pipe (`generator/video_ltx`) do not expose these keys.
- No GPU-validated before/after comparison has been recorded for this implementation yet — treat flicker-reduction claims as expected from the published technique rather than confirmed against this codebase's output.
