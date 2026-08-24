---
type: technique
title: Numerics Watchdog (NaN/Inf Guard)
category_group: Quality
status: stable
families: [all-native]
authors: []
paper: null
reference_impl: null
knobs:
  - key: sampler_options.nan_check_interval
    surface: preset
    default: 4
    effect: "How often (in steps) the running latent is checked for NaN/Inf; 0 disables the check"
related: [attention-backends]
---

# Numerics Watchdog (NaN/Inf Guard)

Occasionally, a bug in a model, an attention kernel, or a numerically extreme combination of inputs
can push the latent being denoised into NaN or Inf values partway through a generation. Left
unchecked, this doesn't produce an obvious error — it silently decodes to a black or garbage image,
and the failure is hard to trace back to its cause. The numerics watchdog closes that gap: it checks
the running latent for non-finite values periodically during sampling and, if it finds any, fails
the generation immediately with a clear error instead of letting it complete into a corrupted result.

The check runs every 4 steps by default, plus always after the first step and always on the final
step, so a corruption that appears early is caught quickly rather than only surfacing once decoding
is already underway. The check itself only reads the latent — it never modifies it — so a clean
generation is completely unaffected by having the watchdog enabled.

## When to use it

This is on by default and is meant to stay on for normal use — it only ever fires on a genuinely
corrupted generation, and a false positive on healthy output is not expected. The only reason to
lower `nan_check_interval` is to catch a corruption a step or two earlier while debugging; the only
reason to disable it (`nan_check_interval: 0`) is to shave a small amount of overhead from
performance-critical batch runs where you've already ruled out numerics problems for your specific
model/backend combination.

## How to enable it

No action needed — the watchdog runs automatically on every native generation. To change the check
interval or turn it off, set `nan_check_interval` inside `sampler_options` in the preset's generation
config:

```yaml
sampler_options:
  nan_check_interval: 8   # check every 8 steps instead of every 4; 0 disables the check entirely
```

When the watchdog fires, the generation fails with an error identifying the step index, the sampler
in use, and (when resolvable) the active attention backend — for example, a message like "Numerical
instability at step 4 (sampler=euler, attention=sage2)" — so you have a concrete starting point for
diagnosing which combination of settings triggered it. This surfaces to the UI as a normal
generation failure, not a silent bad output.

## Tradeoffs and limitations

- Each check forces a GPU-to-CPU synchronization, so a very short interval adds measurable overhead
  on long runs; the default of every 4 steps keeps that overhead negligible for typical step counts.
- The watchdog can only tell you *that* the latent went non-finite and roughly *where* — it does not
  diagnose the root cause. If it fires consistently, treat the reported sampler/attention-backend
  combination as your first thing to change (for example, switching attention backend) before
  assuming the model itself is at fault.
- Disabling the check (`nan_check_interval: 0`) trades this early-failure guarantee for a small
  speed gain; a corrupted trajectory will still decode into a bad image, it just won't be caught
  during sampling.
