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
    effect: "Selects the step algorithm (euler, dpmpp_2m, unipc, euler_sde, euler_ancestral, euler_cfg_pp, euler_ancestral_cfg_pp, euler_restart, dpmpp_2m_sde, dpmpp_3m, er_sde, res_multistep, lcm)"
  - key: sampler_options
    surface: preset
    default: "{}"
    effect: "Per-sampler parameters, e.g. {eta: 0.5} for euler_sde, {restart_count: 2} for euler_restart, {max_stage: 2} for er_sde"
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

Neither list is fixed. Both are registries — core registers the algorithms and schedules described
below, a plugin adds its own through the `samplers:` / `schedules:` manifest roots, and
`GET /api/sampling/catalog` reports whatever this instance actually carries. See
[Plugin API → Samplers and schedules](../plugin-api.md#samplers-and-schedules) for the two callable
contracts and a worked example.

## Samplers

Thirteen samplers are registered:

- **`euler`** (default) — deterministic first-order flow-matching step. The baseline: fast, exact
  for constant-velocity predictions, and the reference every other sampler is derived against.
- **`dpmpp_2m`** — DPM-Solver++(2M), a deterministic second-order multistep solver. A common choice
  when you want more accuracy than `euler` at the same step count without going stochastic.
- **`unipc`** — multistep predictor-corrector solver tuned for flow models (matches the Wan
  defaults). Good for pushing step counts down on video without a large quality drop.
- **`euler_sde`** — stochastic (ancestral) variant of `euler`. Injects a configurable fraction of
  fresh noise at every step (`sampler_options={"eta": ...}`; `eta=1.0` is fully ancestral, lower
  values interpolate back toward deterministic `euler`, `eta=0` is identical to `euler`).
- **`euler_ancestral`** — a different ancestral parameterization from `euler_sde`: it works in
  `alpha = 1 - sigma` space with a `sigma_down`/renoise split (a faithful port of the per-step math
  in diffusers' `LTXEulerAncestralRFScheduler`), which is what LTX-2.5's stage-1 pass runs. Options:
  `eta` (default `1.0`, fully ancestral) and `s_noise` (default `1.0`). At `eta=0` it is
  bit-identical to `euler`; away from `0` it walks a genuinely different trajectory than
  `euler_sde` at the same `eta`, which is why both exist.
- **`euler_cfg_pp`** — deterministic Euler with the CFG++ target/direction split (Chung et al.,
  arXiv:2406.08070). Plain CFG's scale inflates the step size as well as the guidance, pushing the
  trajectory off the data manifold at the scales that give good prompt adherence; CFG++ keeps the
  guided prediction as the step's *target* but takes the step's *direction* from the raw
  unconditional prediction. With no uncond branch to read (embedded guidance, or true CFG at scale
  1.0) the split is a no-op and this reduces exactly to `euler`.
- **`euler_ancestral_cfg_pp`** — the same CFG++ split with ancestral noise injection on top,
  `eta`-configurable via `sampler_options` (variance-preserving mix, same as `euler_sde`).
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
- **`er_sde`** — ER-SDE-Solver-3 (Cui et al., arXiv:2309.06169), a stochastic third-order
  multistep solver. Its extra reverse-time diffusion term replaces more of the latent with fresh
  noise early in the trajectory than an ancestral step does, which is what people reach for on
  Krea-2/Flux-class models when they want photographic texture instead of the smoother look `euler`
  converges to. Configured via `sampler_options={"s_noise": ..., "max_stage": ...}`: `s_noise`
  (default `1.0`) scales the injected noise, `max_stage` (default `3`) caps the solver order at 1,
  2 or 3.
- **`lcm`** — for distilled/consistency (LCM, TCD) checkpoints. Re-noises the clean estimate with
  fresh noise every step; on a normal (non-distilled) model this degrades quality, so it is an
  explicit choice rather than a default anyone would fall into. It is the one sampler the shipped
  presets do not put in their **Sampler** dropdown (`exclude: ["lcm"]` on the field): whether it is
  valid depends on the checkpoint a pipeline loads, not on what the user picks at generation time,
  so a preset that wants it pins it in `pipeline.yml` rather than offering it as a row someone can
  select onto an ordinary checkpoint.

`euler_sde`, `euler_ancestral`, `euler_ancestral_cfg_pp`, `dpmpp_2m_sde`, `er_sde`, and `lcm`
inject fresh random noise each step (the registry marks them `stochastic`), so they need a seeded
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

- Reach for a stochastic sampler (`euler_sde`, `dpmpp_2m_sde`, `er_sde`, `lcm` on distilled
  checkpoints) to add variation between otherwise-identical seeds, or to soften artifacts that a
  deterministic sampler compounds. `er_sde` is the one to try first for skin and fabric realism on a
  flow-matching image model.
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

- Stochastic samplers (`euler_sde`, `dpmpp_2m_sde`, `er_sde`, `lcm`) trade determinism-adjacent
  stability for variation; two runs with the same seed but different `eta` will diverge more as `eta`
  increases. `er_sde` has no `eta` — its noise level follows the solver's own noise scaler, scaled by
  `s_noise` — so it never collapses back to a deterministic solver the way `euler_sde` does at
  `eta=0`.
- `lcm` is only appropriate for distilled/consistency checkpoints — using it on a normal checkpoint
  degrades output quality by design.
- `euler_restart` and higher-order multistep solvers cost more compute per configured step count
  than plain `euler` (restarts add real sampling passes; multistep solvers carry extra state but not
  extra forwards).
- Not every sampler/schedule combination has been benchmarked per family — treat unfamiliar
  combinations as worth a visual check before relying on them for production output.
