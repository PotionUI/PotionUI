"""Contributing a step algorithm or a sigma schedule to the native engine.

A plugin declares them in its `manifest.yml` (`samplers:` / `schedules:`) and
implements the two callables below; core registers them onto the same
registries its own thirteen algorithms and six schedules live on, so a
plugin entry is selectable everywhere a core one is - preset `sampler:` /
`schedule:` fields, `GET /api/sampling/catalog`, the pipe config surface.

**A sampler** is a callable with the uniform signature every entry in the
native `sampling/algorithms/` package shares::

    sample(model_fn, x, sigmas, guidance, cond, uncond, hooks, is_cancelled,
           sampler_options) -> Tensor

`sample_euler` is re-exported here as the reference implementation: read it
before writing one. `model_fn(x, sigma, conditioning) -> velocity` is already
wrapped (expert routing, fp32 trajectory, step cache); `guidance` is a
`GuidanceStrategy` that combines the cond/uncond predictions; `hooks` are
`StepHook`s fired through `run_hooks(hooks, "on_step", ...)` once per step;
`is_cancelled()` is polled per step and a True answer must raise
`SamplingCancelled`. `sampler_options` is the opaque dict a preset's options
arrive in, plus two engine-supplied keys: `generator` (the request's seeded
`torch.Generator` - draw every stochastic sample from it, never from the
global RNG, or the same seed stops reproducing the same image) and
`discontinuity_steps` (step indices at which a multistep solver must clear
its history, set when a dual-expert model switches networks mid-schedule).

**A schedule** is a callable `build(ctx: ScheduleContext) -> Tensor` returning
`ctx.steps + 1` descending sigmas as float32 on CPU. `build_sigmas` owns
everything around that call: the denoise truncation, the detail-daemon warp
and the exact-zero terminal. A schedule whose own length dictates the step
count declares `owns_steps: true` in the manifest and is handed the raw
`steps`; one that needs the packed token count declares
`requires_image_seq_len: true` and is rejected when the caller has none.

**Flow-matching conventions** every native target shares: sigmas descend from
`1.0` (pure noise) to `0.0` (clean latent), the model returns a VELOCITY, and
the denoised estimate is `x0 = x - sigma * v`. A step of size
`sigma_next - sigma` along `v` is plain Euler.

The registries are exported READ-ONLY: inspect what is registered, but
register through the manifest rather than calling `register()` yourself, so
the entry is removed again when the plugin is disabled.

See docs/plugin-api.md ("Samplers and schedules") for a worked example.
"""

from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sampling.algorithms import sample_euler
from src.platform.runtime.native.sampling.cfg import GuidanceStrategy
from src.platform.runtime.native.sampling.hooks import run_hooks
from src.platform.runtime.native.sampling.registry import (
    ANY_FAMILY,
    OptionSpec,
    SamplerDefinition,
    ScheduleContext,
    ScheduleDefinition,
    sampler_registry,
    schedule_registry,
)

__all__ = [
    "ANY_FAMILY",
    "GuidanceStrategy",
    "OptionSpec",
    "SamplerDefinition",
    "SamplingCancelled",
    "ScheduleContext",
    "ScheduleDefinition",
    "run_hooks",
    "sample_euler",
    "sampler_registry",
    "schedule_registry",
]
