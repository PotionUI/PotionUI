---
type: technique
title: Samplers and Sigma Schedules
category_group: Sampling
status: stable
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs:
  - key: sampler
    surface: preset
    default: euler
    effect: "Selects the step algorithm (euler, dpmpp_2m, unipc, euler_sde, euler_restart, dpmpp_2m_sde, dpmpp_3m, res_multistep, lcm)"
  - key: sampler_options
    surface: preset
    default: "{}"
    effect: "Per-sampler parameters, e.g. {eta: 0.5} for euler_sde, {restart_count: 2} for euler_restart"
  - key: schedule_settings.schedule
    surface: preset
    default: null (shift-based)
    effect: "Selects the sigma schedule mode: null/shift (default), beta, exponential, or linear_quadratic"
  - key: schedule_settings.schedule_options
    surface: preset
    default: "{}"
    effect: "Per-schedule parameters, e.g. {alpha, beta} for beta, {sigma_min} for exponential"
related: [detail-daemon-schedule]
---

# Samplers and Sigma Schedules

Two independent choices shape how a native generation walks from pure noise to a finished image or
video: the **sampler** (the step algorithm that integrates the model's predicted velocity into an
updated latent) and the **schedule** (the sequence of noise levels, or sigmas, the sampler steps
through). PotionUI exposes both as preset-level settings, and every native family — image and video
alike — reads them through the same code path, so changing sampler or schedule does not require
switching models or presets.

The default combination (`euler` sampler, shift-based schedule) reproduces exactly what earlier
versions of the engine always did — nothing here changes behavior unless you opt in.

## Samplers

Nine samplers are registered:

- **`euler`** (default) — deterministic first-order flow-matching step. The baseline: fast, exact
  for constant-velocity predictions, and the reference every other sampler is derived against.
- **`dpmpp_2m`** — DPM-Solver++(2M), a deterministic second-order multistep solver. A common choice
  when you want more accuracy than `euler` at the same step count without going stochastic.
- **`unipc`** — multistep predictor-corrector solver tuned for flow models (matches the Wan
  defaults). Good for pushing step counts down on video without a large quality drop.
- **`euler_sde`** — stochastic (ancestral) variant of `euler`. Injects a configurable fraction of
  fresh noise at every step (`sampler_options={"eta": ...}`; `eta=1.0` is fully ancestral, lower
  values interpolate back toward deterministic `euler`, `eta=0` is identical to `euler`).
- **`euler_restart`** — restart sampling: re-noises partway through the trajectory and re-descends,
  giving the model extra passes at correcting compounding discretization error, at the cost of extra
  steps. Configured via `sampler_options={"restart_count": ..., "restart_strength": ...}`.
- **`dpmpp_2m_sde`** — stochastic second-order multistep solver (DPM-Solver++(2M) SDE). Combines the
  accuracy of a multistep solver with ancestral noise injection.
- **`dpmpp_3m`** — deterministic third-order multistep solver. The highest-order deterministic
  option in the roster; useful when you want maximum accuracy per step and are willing to pay for
  the extra history it tracks.
- **`res_multistep`** — second-order exponential multistep solver derived from the RES paper's
  corrected integrator coefficients. A deterministic alternative to `dpmpp_2m` with different
  numerical behavior.
- **`lcm`** — for distilled/consistency (LCM, TCD) checkpoints. Re-noises the clean estimate with
  fresh noise every step; on a normal (non-distilled) model this degrades quality, so it is an
  explicit choice rather than a default anyone would fall into.

`euler_sde`, `dpmpp_2m_sde`, and `lcm` inject fresh random noise each step, so they need a seeded
generator to stay reproducible; PotionUI wires this automatically from the generation's own seed.

## Sigma schedules

Four schedule modes are available via `schedule_settings.schedule`:

- **Shift-based (default, `schedule` unset or `"shift"`)** — the original per-family schedule:
  either a constant shift value, or (for families like Flux1 that declare `base_shift`/`max_shift`)
  a resolution-dependent dynamic shift. This is what every native preset used before schedule
  selection existed.
- **`beta`** — Beta-CDF spacing of the sigmas. Options: `alpha`, `beta` (both default `0.6`, both
  must be > 0).
- **`exponential`** — geometric spacing. Options: `sigma_min` (default `1e-3`, must be in `(0, 1)`).
- **`linear_quadratic`** — a linear-then-quadratic ramp (LTX lineage). Options: `threshold_noise`
  (default `0.025`), `linear_steps` (default half the step count).

## When to use it

Stay on `euler` with the default shift schedule unless you have a specific reason to deviate:

- Reach for a stochastic sampler (`euler_sde`, `dpmpp_2m_sde`, `lcm` on distilled checkpoints) to add
  variation between otherwise-identical seeds, or to soften artifacts that a deterministic sampler
  compounds.
- Reach for a higher-order deterministic solver (`dpmpp_2m`, `dpmpp_3m`, `res_multistep`, `unipc`)
  when you want to reduce step count without a proportional quality loss — these tend to converge
  faster than `euler` per step.
- Reach for `euler_restart` when a generation is close but has visible layout errors you want the
  model to get a second attempt at correcting, at the cost of extra compute.
- Try alternate schedules (`beta`, `exponential`, `linear_quadratic`) when the default shift-based
  spacing under- or over-samples a region of the trajectory for your content — e.g.
  `linear_quadratic` for video families in the LTX lineage.

## How to enable it

Set `sampler`, `sampler_options`, and/or `schedule_settings` in the preset's generation config:

```yaml
sampler: euler_sde
sampler_options:
  eta: 0.6

schedule_settings:
  schedule: beta
  schedule_options:
    alpha: 0.6
    beta: 0.6
```

`schedule_settings` also carries `detail_strength`/`detail_start`/`detail_end` for the
detail-daemon sigma warp — see [detail-daemon-schedule](detail-daemon-schedule) for that knob
specifically.

## Tradeoffs and limitations

- Stochastic samplers (`euler_sde`, `dpmpp_2m_sde`, `lcm`) trade determinism-adjacent stability for
  variation; two runs with the same seed but different `eta` will diverge more as `eta` increases.
- `lcm` is only appropriate for distilled/consistency checkpoints — using it on a normal checkpoint
  degrades output quality by design.
- `euler_restart` and higher-order multistep solvers cost more compute per configured step count
  than plain `euler` (restarts add real sampling passes; multistep solvers carry extra state but not
  extra forwards).
- Not every sampler/schedule combination has been benchmarked per family — treat unfamiliar
  combinations as worth a visual check before relying on them for production output.
