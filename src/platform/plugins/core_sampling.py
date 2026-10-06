from __future__ import annotations

import importlib
from typing import Any

from src.platform.plugins.sampling import (
    OptionSpec,
    SamplerDefinition,
    ScheduleDefinition,
    sampler_registry,
    schedule_registry,
)

_ALGORITHMS_MODULE = "src.platform.runtime.native.sampling.algorithms"
_SCHEDULES_MODULE = "src.platform.runtime.native.sampling.flow_schedule"


class LazyCallable:
    def __init__(self, module: str, attribute: str):
        self.module = module
        self.attribute = attribute
        self.__name__ = attribute

    def resolve(self) -> Any:
        return getattr(importlib.import_module(self.module), self.attribute)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.resolve()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"LazyCallable({self.module}.{self.attribute})"


def _sampler(attribute: str) -> LazyCallable:
    return LazyCallable(_ALGORITHMS_MODULE, attribute)


def _schedule(attribute: str) -> LazyCallable:
    return LazyCallable(_SCHEDULES_MODULE, attribute)


_ETA_OPTION = OptionSpec(
    "eta", "float", 1.0,
    "Fraction of the implied per-step noise replaced with fresh noise, in [0, 1]. "
    "0.0 reduces exactly to plain Euler; 1.0 is fully ancestral.",
    min_value=0.0, max_value=1.0,
)
_S_NOISE_OPTION = OptionSpec(
    "s_noise", "float", 1.0, "Extra scale on the injected noise.", min_value=0.0,
)

CORE_SAMPLERS = (
    SamplerDefinition(
        "euler", _sampler("sample_euler"), "Euler",
        description="First-order flow-matching step; deterministic and the reference "
                    "implementation of the sampler contract.",
    ),
    SamplerDefinition(
        "dpmpp_2m", _sampler("sample_dpmpp_2m"), "DPM++ 2M",
        description="Second-order multistep solver reusing the previous step's x0 estimate.",
    ),
    SamplerDefinition(
        "unipc", _sampler("sample_unipc"), "UniPC",
        description="Unified predictor-corrector multistep solver.",
    ),
    SamplerDefinition(
        "euler_sde", _sampler("sample_euler_sde"), "Euler SDE", stochastic=True,
        options=(_ETA_OPTION,),
        description="Euler with a per-step fresh-noise injection (ancestral SDE).",
    ),
    SamplerDefinition(
        "euler_ancestral", _sampler("sample_euler_ancestral"), "Euler Ancestral", stochastic=True,
        options=(_ETA_OPTION, _S_NOISE_OPTION),
        description="Ancestral Euler; LTX-2.5's stage-1 sampler.",
    ),
    SamplerDefinition(
        "euler_ancestral_cfg_pp", _sampler("sample_euler_ancestral_cfg_pp"), "Euler Ancestral CFG++",
        stochastic=True, options=(_ETA_OPTION,),
        description="Ancestral Euler stepping along the uncond prediction (CFG++).",
    ),
    SamplerDefinition(
        "euler_cfg_pp", _sampler("sample_euler_cfg_pp"), "Euler CFG++",
        description="Deterministic Euler stepping along the uncond prediction (CFG++).",
    ),
    SamplerDefinition(
        "euler_restart", _sampler("sample_euler_restart"), "Euler Restart",
        options=(
            OptionSpec(
                "restart_count", "int", 0,
                "Number of re-noise/re-descend restarts appended after the main descent; "
                "0 is exactly plain Euler.",
                min_value=0,
            ),
            OptionSpec(
                "restart_strength", "float", 0.3,
                "How far back up the schedule each restart re-noises.",
                min_value=0.0, max_value=1.0,
            ),
        ),
        description="Euler with Restart sampling (arXiv:2306.14878). An explicit "
                    "'restarts' list of (sigma_hi, sigma_low, n_steps) triples in "
                    "sampler_options overrides the two convenience knobs.",
    ),
    SamplerDefinition(
        "dpmpp_2m_sde", _sampler("sample_dpmpp_2m_sde"), "DPM++ 2M SDE", stochastic=True,
        options=(_ETA_OPTION, _S_NOISE_OPTION),
        description="DPM++ 2M with a per-step fresh-noise injection.",
    ),
    SamplerDefinition(
        "dpmpp_3m", _sampler("sample_dpmpp_3m"), "DPM++ 3M",
        description="Third-order multistep solver; deterministic.",
    ),
    SamplerDefinition(
        "er_sde", _sampler("sample_er_sde"), "ER SDE", stochastic=True,
        options=(
            _S_NOISE_OPTION,
            OptionSpec(
                "max_stage", "int", 3,
                "Solver order (1/2/3); early steps warm up at a lower order until enough "
                "x0 history exists.",
                min_value=1, max_value=3,
            ),
            OptionSpec(
                "noise_scaler", "str", "default",
                "'default' for the paper's recommended phi, 'identity' to degenerate the "
                "solver to the probability-flow ODE (diagnostic).",
            ),
        ),
        description="Extended Reverse-Time SDE solver (arXiv:2410.11541).",
    ),
    SamplerDefinition(
        "res_multistep", _sampler("sample_res_multistep"), "RES Multistep",
        description="Exponential-integrator multistep solver; deterministic.",
    ),
    SamplerDefinition(
        "lcm", _sampler("sample_lcm"), "LCM", stochastic=True,
        description="Latent Consistency Model stepping: predict x0, re-noise to the next "
                    "sigma. Needs an LCM/consistency-distilled checkpoint.",
    ),
)

CORE_SCHEDULES = (
    ScheduleDefinition(
        "shift", _schedule("_build_shift_schedule"), "Model default",
        description="The model's own shift-based ramp: fixed mu, resolution-anchored mu, "
                    "Flux1 dynamic mu, or a constant shift -- whichever the ModelSpec's "
                    "sampling_settings supply.",
    ),
    ScheduleDefinition(
        "beta", _schedule("_build_beta_schedule"), "Beta",
        options=(
            OptionSpec("alpha", "float", 0.6, "Beta distribution alpha.", min_value=0.0),
            OptionSpec("beta", "float", 0.6, "Beta distribution beta.", min_value=0.0),
        ),
        description="Beta-CDF spacing; alpha, beta < 1 concentrates steps near both ends.",
    ),
    ScheduleDefinition(
        "exponential", _schedule("_build_exponential_schedule"), "Exponential",
        options=(
            OptionSpec(
                "sigma_min", "float", 1e-3,
                "Geometric floor the ramp descends to before the exact-zero terminal.",
                min_value=0.0, max_value=1.0,
            ),
        ),
        description="Geometrically-spaced sigmas from 1.0 down to sigma_min.",
    ),
    ScheduleDefinition(
        "linear_quadratic", _schedule("_build_linear_quadratic_schedule"), "Linear-quadratic",
        options=(
            OptionSpec(
                "threshold_noise", "float", 0.025,
                "Noise level the linear segment ramps to before the quadratic tail.",
                min_value=0.0, max_value=1.0,
            ),
            OptionSpec(
                "linear_steps", "int", None,
                "Length of the linear segment; defaults to half the step count.",
                min_value=1,
            ),
        ),
        description="LTX-lineage linear-then-quadratic noise ramp.",
    ),
    ScheduleDefinition(
        "manual", _schedule("_build_manual_schedule"), "Manual sigmas", owns_steps=True,
        options=(
            OptionSpec(
                "sigmas", "str", None,
                "Descending sigma list -- a comma-separated string or a sequence of "
                "floats. Its length IS the step count.",
            ),
        ),
        description="An explicit, hand-authored sigma list (ComfyUI 'ManualSigmas'-style); "
                    "ignores steps and denoise entirely.",
    ),
    ScheduleDefinition(
        "ltx_dynamic", _schedule("_build_ltx_dynamic_schedule"), "LTX dynamic shift",
        families=("ltx",), requires_image_seq_len=True,
        options=(
            OptionSpec("base_shift", "float", 0.95, "mu at the low token-count anchor."),
            OptionSpec("max_shift", "float", 2.05, "mu at the high token-count anchor."),
            OptionSpec("stretch", "bool", True, "Stretch the last nonzero sigma onto 'terminal'."),
            OptionSpec("terminal", "float", 0.1, "Stretch target for the last nonzero sigma.",
                       min_value=0.0, max_value=1.0),
        ),
        description="LTX-2.5's resolution-dependent shift: mu interpolated from the packed "
                    "video token count.",
    ),
)

for _definition in CORE_SAMPLERS:
    sampler_registry.register(_definition)

for _definition in CORE_SCHEDULES:
    schedule_registry.register(_definition)
