---
type: technique
title: Speed Profiles
category_group: Performance
status: stable
families: ["all-native"]
authors: []
paper: null
reference_impl: null
knobs:
  - key: speed_profiles
    surface: preset
    default: "none (opt-in per preset)"
    effect: "Lets a preset expose a named set of generation-knob bundles (e.g. Draft/Standard/Max) that a form field switches between atomically"
related: []
---

# Speed Profiles

Speed profiles are how an individual preset can offer a one-click quality/speed tradeoff — a
Draft/Standard/Max-style selector — without you having to manually retune steps, guidance,
sampler, and LoRA stack every time you want a fast preview versus a slow final render. Instead of
exposing steps, CFG, sampler, and a distilled-LoRA toggle as four separate controls you'd have to
coordinate by hand, a preset can bundle them into named profiles and expose a single selector that
switches all of them at once.

This is a preset-authoring convention, not a universal app feature: it lives entirely in how an
individual preset's `preset.yml` and form are written. Some presets will have a Speed selector,
others won't — it depends on whether that preset's author chose to define `speed_profiles:` and
wire a selector field to it.

## When to use it

If a preset you're using has a Speed/Quality selector (commonly labeled "Draft", "Standard", "Max",
or similar), use "Draft" while iterating on prompt/composition — fewer steps, lower guidance,
often a distilled LoRA swapped in for extra speed — then switch to "Standard" or "Max" once you're
happy with the composition and want the higher-fidelity final render. If a preset has no such
selector, its author hasn't opted into this pattern for that preset — the option simply isn't
there.

## How to enable it

There's no app-wide setting for this — it's declared per preset in `preset.yml` and appears in the
UI only as whatever form control that preset's author built. A `speed_profiles:` block looks like:

```yaml
speed_profiles:
  draft:
    steps: 6
    guidance: 1.0
    shift: 5.0
    loras:
      - { file: "lightx2v.safetensors", weight: 1.0 }
  standard:
    steps: 28
    guidance: 5.0
  max:
    steps: 40
    guidance: 6.5
    sampler: "dpmpp_2m"
    schedule: "karras"
```

The preset's form then exposes a plain `select` field listing the profile names (e.g. a "Speed"
dropdown with Draft/Standard/Max options), and the pipeline reads whichever knobs the selected
profile sets via the `get_speed_profile(name)` helper. As a user, you interact with this purely as
that dropdown on the generation form for presets that have one — selecting "Draft" or "Max" swaps
steps, guidance, sampler, schedule, and LoRA stack together, in one action, rather than you tuning
each field individually.

## Tradeoffs and limitations

- Not universal: whether a preset has a speed-profile selector at all depends entirely on that
  preset's own authoring — many presets don't define one.
- The knob set a profile can override is a fixed, typed whitelist (`steps`, `guidance`, `shift`,
  `sampler`, `schedule`, `loras`), plus a free-form `extra:` bucket for anything not on that list —
  a preset author can't add arbitrary new top-level keys to a profile without going through
  `extra:`.
- Profiles are all-or-nothing per generation: selecting "Draft" applies every knob that profile
  defines at once — you can't cherry-pick, say, Draft's step count with Max's sampler, without the
  preset author defining a profile that does exactly that combination.
- A profile referenced by name that doesn't exist in the preset's `speed_profiles:` block raises a
  clear error identifying the preset and profile (unless the pipeline explicitly supplies a
  fallback default) — it does not silently fall back to some other behavior.
