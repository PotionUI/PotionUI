---
type: technique
title: Detail Daemon Schedule Warp
category_group: Quality
status: stable
families: ["all-native"]
authors: []
paper: null
reference_impl: {name: "muerrilla/sd-webui-detail-daemon", url: "https://github.com/muerrilla/sd-webui-detail-daemon", license: "MIT"}
knobs:
  - key: schedule_settings.detail_strength
    surface: preset
    default: 0.0
    effect: "How strongly the sigma schedule is warped toward more (positive) or less (negative) fine detail"
  - key: schedule_settings.detail_start
    surface: preset
    default: 0.1
    effect: "Start of the trajectory-fraction window the warp applies in"
  - key: schedule_settings.detail_end
    surface: preset
    default: 0.9
    effect: "End of the trajectory-fraction window the warp applies in"
related: [samplers-and-schedules]
---

# Detail Daemon Schedule Warp

The detail daemon is a dial on the noise schedule itself, not a new sampling algorithm. Every
generation follows a sequence of noise levels (sigmas) from high noise down to zero — this
technique multiplies a window of those sigmas by a small bump curve, nudging the model to spend
comparatively more (or less) of the denoising trajectory refining fine detail, without changing how
many steps run or which sampler is used.

Concretely, it applies a half-sine "hump" over a chosen fraction-of-trajectory window: zero effect
at the window's edges, maximum effect at its midpoint, zero again outside it. The very first and
last sigma are never touched, and the result is re-clamped to stay strictly decreasing, so the
schedule can't accidentally invert two neighboring steps even at a large strength or a narrow
window. Think of it as a detail dial layered on top of whatever schedule and sampler you've already
chosen — it composes with any of them.

## When to use it

Push it positive when a generation reads as slightly soft or under-detailed and you don't want to
add steps or switch samplers — a small positive `detail_strength` biases the trajectory toward
resolving more fine texture. Push it negative for a softer, less textured look (useful for smoother
skin/background renders, or to counteract a checkpoint that's naturally oversharpened). Leave it at
`0.0` (the default) for the schedule's original, untouched behavior.

## How to enable it

It's a `sampling_settings` knob, nested under `schedule_settings` in a preset's generation config
(`src/pipelines/pipes/_shared/generation/guidance_options.py`'s `schedule_settings_config_specs()`), alongside
`schedule`/`schedule_options`. All three `detail_*` keys default to `None` at the pipe-config layer
(meaning "inherit"), and `build_sigmas` itself treats `detail_strength: 0.0` as the true no-op
default:

```yaml
schedule_settings:
  detail_strength: 0.15   # 0.0 = off (default); expected range roughly -0.3 to 0.3
  detail_start: 0.1       # trajectory-fraction window start (default)
  detail_end: 0.9         # trajectory-fraction window end (default)
```

This is reachable from every native image and video preset today — the pipe layer forwards
`schedule_settings` into `NativeGenerator.sample()`, which threads it through to
`sampling/flow_schedule.py`'s `build_sigmas`. Whether a given preset exposes it as a form control
(a slider labeled something like "Detail") depends on that preset's own form; as a preset author you
wire a slider to `detail_strength`/`detail_start`/`detail_end` the same way you'd wire any other
`get_form(...)` value into `schedule_settings`.

## Tradeoffs and limitations

- It reshapes the noise schedule, not the model — it cannot invent detail the model isn't capable
  of producing; it only redistributes how much of the trajectory is spent in the fine-detail
  regime.
- Values well outside the documented `-0.3` to `0.3` range are not hard-blocked by the schedule
  builder itself, but the pipe-level config spec enforces those bounds (`min_value=-0.3,
  max_value=0.3`) — going outside them at the preset-authoring layer is rejected.
- A narrow window (`detail_end` close to `detail_start`) combined with a large strength is the
  scenario most likely to need the strictly-decreasing re-clamp, which can flatten the intended
  effect at the window's edges.
- Applies uniformly across every native schedule mode (shift-based, beta, exponential,
  linear_quadratic) — it doesn't have per-schedule tuning, so a strength that works well on one
  schedule family may feel different on another.
