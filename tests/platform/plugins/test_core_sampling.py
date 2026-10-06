import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.platform.plugins import core_sampling
from src.platform.plugins.sampling import (
    SamplerDefinition,
    ScheduleContext,
    sampler_registry,
    schedule_registry,
)

ROOT = Path(__file__).resolve().parents[3]

EXPECTED_SAMPLERS = [
    ("euler", "Euler", False, ()),
    ("dpmpp_2m", "DPM++ 2M", False, ()),
    ("unipc", "UniPC", False, ()),
    ("euler_sde", "Euler SDE", True, ("eta",)),
    ("euler_ancestral", "Euler Ancestral", True, ("eta", "s_noise")),
    ("euler_ancestral_cfg_pp", "Euler Ancestral CFG++", True, ("eta",)),
    ("euler_cfg_pp", "Euler CFG++", False, ()),
    ("euler_restart", "Euler Restart", False, ("restart_count", "restart_strength")),
    ("dpmpp_2m_sde", "DPM++ 2M SDE", True, ("eta", "s_noise")),
    ("dpmpp_3m", "DPM++ 3M", False, ()),
    ("er_sde", "ER SDE", True, ("s_noise", "max_stage", "noise_scaler")),
    ("res_multistep", "RES Multistep", False, ()),
    ("lcm", "LCM", True, ()),
]

EXPECTED_SCHEDULES = [
    ("shift", "Model default", ("*",), False, False, ()),
    ("beta", "Beta", ("*",), False, False, ("alpha", "beta")),
    ("exponential", "Exponential", ("*",), False, False, ("sigma_min",)),
    ("linear_quadratic", "Linear-quadratic", ("*",), False, False, ("threshold_noise", "linear_steps")),
    ("manual", "Manual sigmas", ("*",), True, False, ("sigmas",)),
    ("ltx_dynamic", "LTX dynamic shift", ("ltx",), False, True, ("base_shift", "max_shift", "stretch", "terminal")),
]

FORBIDDEN_MODULES = ("torch", "src.platform.runtime.native.sampling.denoise_loop")

_PROBE = """
import asyncio, json, sys

from src.features.fields.sampling_fields import SamplerField, ScheduleField
from src.features.sampling.routes import SamplingController

def field(kind, family):
    return {"type": kind, "name": kind, "configuration": {"family": family}}

catalog = asyncio.run(SamplingController().get_catalog()).data

print("PROBE=" + json.dumps({
    "sampler_options": SamplerField(None).output(field("sampler", "krea2"))["options"],
    "schedule_options": ScheduleField(None).output(field("schedule", "krea2"))["options"],
    "ltx_schedule_options": ScheduleField(None).output(field("schedule", "ltx"))["options"],
    "catalog_samplers": [d["key"] for d in catalog["samplers"]],
    "catalog_schedules": [d["key"] for d in catalog["schedules"]],
    "loaded": [m for m in %r if m in sys.modules],
}))
""" % (FORBIDDEN_MODULES,)


@pytest.fixture(scope="module")
def probe():
    env = {
        "PYTHONPATH": f"{ROOT / 'venv/lib/python3.12/site-packages'}{os.pathsep}{ROOT}",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(ROOT),
    }
    for name in ("SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC"):
        if name in os.environ:
            env[name] = os.environ[name]
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("PROBE=")]
    assert len(lines) == 1, result.stdout[-4000:]
    return json.loads(lines[0].split("=", 1)[1])


def test_sampler_field_lists_every_core_sampler_without_the_engine(probe):
    assert [o["value"] for o in probe["sampler_options"]] == [e[0] for e in EXPECTED_SAMPLERS]
    assert [o["label"] for o in probe["sampler_options"]] == [e[1] for e in EXPECTED_SAMPLERS]


def test_schedule_field_lists_the_core_schedules_for_a_family_without_the_engine(probe):
    universal = [e[0] for e in EXPECTED_SCHEDULES if e[2] == ("*",)]
    assert [o["value"] for o in probe["schedule_options"]] == universal
    assert [o["value"] for o in probe["ltx_schedule_options"]] == [e[0] for e in EXPECTED_SCHEDULES]


def test_catalog_controller_serves_the_full_catalog_without_the_engine(probe):
    assert probe["catalog_samplers"] == [e[0] for e in EXPECTED_SAMPLERS]
    assert probe["catalog_schedules"] == [e[0] for e in EXPECTED_SCHEDULES]


def test_serving_the_catalog_does_not_import_torch_or_the_denoise_loop(probe):
    assert probe["loaded"] == []


def test_core_samplers_keep_their_keys_order_labels_and_flags():
    core = [d for d in sampler_registry.definitions() if d.source == "core"]
    assert [
        (d.key, d.label, d.stochastic, tuple(o.name for o in d.options)) for d in core
    ] == EXPECTED_SAMPLERS
    assert all(d.families == ("*",) for d in core)


def test_core_schedules_keep_their_keys_order_labels_and_flags():
    core = [d for d in schedule_registry.definitions() if d.source == "core"]
    assert [
        (d.key, d.label, d.families, d.owns_steps, d.requires_image_seq_len, tuple(o.name for o in d.options))
        for d in core
    ] == EXPECTED_SCHEDULES


def test_every_sampler_shim_resolves_to_the_algorithm_function():
    from src.platform.runtime.native.sampling import algorithms

    for definition in core_sampling.CORE_SAMPLERS:
        assert definition.sample.resolve() is getattr(algorithms, definition.sample.attribute)


def test_every_schedule_shim_resolves_to_the_flow_schedule_builder():
    from src.platform.runtime.native.sampling import flow_schedule

    for definition in core_sampling.CORE_SCHEDULES:
        assert definition.build.resolve() is getattr(flow_schedule, definition.build.attribute)


def test_sampler_shim_delegates_arguments_to_the_algorithm(monkeypatch):
    from src.platform.runtime.native.sampling import algorithms

    calls = []
    monkeypatch.setattr(algorithms, "sample_euler", lambda *a, **kw: calls.append((a, kw)) or "latent")

    result = sampler_registry.get("euler").sample(1, 2, 3, option=4)

    assert result == "latent"
    assert calls == [((1, 2, 3), {"option": 4})]


def test_schedule_shim_builds_the_same_sigmas_as_the_builder():
    import torch

    from src.platform.runtime.native.sampling import flow_schedule

    ctx = ScheduleContext(steps=4, options={"sigma_min": 0.01})
    shimmed = schedule_registry.get("exponential").build(ctx)

    assert torch.equal(shimmed, flow_schedule._build_exponential_schedule(ctx))
    assert shimmed.shape == (5,)


def test_disabling_a_plugin_leaves_the_core_samplers_in_place():
    sampler_registry.register(SamplerDefinition("zz_plugin_sampler", lambda *a: None, "ZZ", source="zz-plugin"))
    try:
        sampler_registry.unregister_source("zz-plugin")
        assert not sampler_registry.has("zz_plugin_sampler")
        assert sampler_registry.keys()[: len(EXPECTED_SAMPLERS)] == [e[0] for e in EXPECTED_SAMPLERS]
    finally:
        sampler_registry.unregister("zz_plugin_sampler")
