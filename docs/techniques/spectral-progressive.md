---
type: technique
title: Spectral Progressive Diffusion
category_group: Performance
status: needs-gpu-validation
families: [flux, z_image]
authors: []
paper: {arxiv: "2605.18736", title: "Spectral Progressive Diffusion for Efficient Image and Video Generation"}
reference_impl: {name: "howardhx/speed", url: "https://github.com/howardhx/speed", license: "MIT"}
knobs:
  - key: spectral_progressive.enabled
    surface: preset
    default: true (once the dict is present)
    effect: "Enables staged low-to-full resolution denoising"
  - key: spectral_progressive.scales
    surface: preset
    default: "(0.5, 1.0)"
    effect: "Strictly-increasing resolution fractions ending at 1.0; one growth per extra entry"
  - key: spectral_progressive.delta
    surface: preset
    default: 0.01
    effect: "Single error tolerance driving the derived transition points"
  - key: spectral_progressive.basis
    surface: preset
    default: fft
    effect: "Spectral basis for the resolution-growth step: fft (GPU-native) or dct (CPU, scipy)"
related: [trajectory-warm-start]
---

# Spectral Progressive Diffusion

Diffusion models fill in an image's frequency content coarse-to-fine: low frequencies (overall
layout, large shapes) emerge early in the denoise trajectory, and high frequencies (fine detail,
texture) only emerge late. Spectral Progressive Diffusion exploits this by running the early,
high-noise steps of a generation on a **reduced-resolution** latent — those steps aren't producing
meaningful high-frequency content yet, so computing them at full resolution wastes work — and only
growing to full resolution once the trajectory reaches the noise level where the next frequency band
actually starts carrying real information.

At each resolution transition, PotionUI embeds the low-resolution latent's spectrum into a larger
one and fills the newly-opened high-frequency band with fresh, sigma-scaled Gaussian noise, so the
latent stays exactly on the same noise-interpolation path the sampler expects. Transition points are
derived automatically from a single tolerance value rather than requiring per-model tuning.

## When to use it

Consider this on eligible image families when you want faster generations at a given step count and
can tolerate a training-free approximation rather than a change to step count or sampler. It composes
for free with FBCache. It is mutually exclusive with trajectory warm-start (Iterate mode) — the two
techniques both change what latent resolution/trajectory the sampler starts from, so the engine will
not combine them; enabling both on the same request is treated as a conflict and only one takes
effect per the engine's internal gating.

Eligibility is gated automatically: it only applies to **text-to-image** generations (not img2img)
on a **constant-shift** family with a **4D image latent**. Families that use a resolution-dependent
dynamic shift schedule (Flux1's `base_shift`/`max_shift` mu, Krea-2's anchored dynamic shift) are
excluded — the technique's resolution-growth math isn't ported for a mu that itself depends on token
count — and families whose stills ride a 5D causal-3D video latent (Qwen-Image, Krea-2, Anima) are
excluded by the 4D requirement. In practice this currently means it applies to Flux2 (not Flux1 —
both share the `flux` family, but only the Flux2 variant is eligible) and Z-Image. Requesting it on
an ineligible generation (img2img, or an excluded family/variant) is not an error — PotionUI logs
that it was ignored and runs the normal path.

## How to enable it

Set `spectral_progressive` in the preset's generation config as a nested dict:

```yaml
spectral_progressive:
  enabled: true          # optional; presence of the dict already implies true
  scales: [0.5, 1.0]     # one growth step, from 50% resolution to full
  delta: 0.01             # error tolerance driving the transition sigma(s)
  basis: fft               # or "dct"
```

Omit the key entirely to leave the feature off (the default, byte-identical to the normal path).

## Tradeoffs and limitations

- This has only been validated on CPU so far — GPU behavior, timing, and visual quality have not
  been confirmed on real hardware yet. Treat it as experimental until validated.
- It is training-free and approximate: the resolution-growth schedule assumes a generic radial
  power-law frequency spectrum, which may not match every model or every kind of content equally
  well.
- Mutually exclusive with trajectory warm-start (Iterate mode) on the same request.
- Only applies to text-to-image generation on constant-shift families with a 4D image latent; it
  silently no-ops (with a log line) on img2img, on dynamic-shift families/variants such as Flux1 and
  Krea-2, and on families whose still images use a 5D causal-3D VAE latent (Qwen-Image, Krea-2,
  Anima) — Flux2 and Z-Image are the only currently-eligible families.
- The `dct` basis path runs on CPU via scipy rather than natively on GPU; prefer `fft` unless you
  specifically need DCT's spectral properties.
