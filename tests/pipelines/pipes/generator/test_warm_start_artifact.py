"""Iterate-mode telemetry surfacing: warm-start pipe_artifact + status line.

Drives the shared flow-generator pipe (via the flux pipe) with a fake generator
whose ``sample`` sets ``last_warm_start`` (or not), and asserts what the pipe
emits. Also checks the artifact registration/serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from src.features.generation.handlers.artifact_handlers import serialize_warm_start_output
from src.features.generation.output_types import SerializeContext, output_type_registry
from src.pipelines.outputs import (
    ProgressGenerationOutput,
    WarmStartGenerationOutput,
)
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "flux"
    variant: str = "flux2"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 2.02, "guidance": "embedded"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []
    warm_payload: dict | None = None  # class-level knob: what sample() reports

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec()
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        from src.platform.runtime.native.resolution import snap_resolution
        return snap_resolution(width, height, 8, 2)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.last_warm_start = _FakeGenerator.warm_payload
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


def _cond_model():
    return SimpleNamespace(embeds={"context": torch.ones(1, 4, 8), "pooled": torch.ones(1, 8)}, n_embeds={})


def _pipe_input():
    return PipeInput(input={
        "model": SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=9.0), te_encoder=object(), vae=object()),
        "conditioning": [_cond_model()],
        "seed": [1],
    })


def _run(warm_payload):
    _FakeGenerator.instances.clear()
    _FakeGenerator.warm_payload = warm_payload
    pipe = GeneratorFluxPipe(config={**GeneratorFluxPipe.get_default_config(), "iterate_mode": True, "preview": False})
    emitted = []
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
         patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", _FakeGenerator):
        pipe.process(_pipe_input(), lambda o: emitted.append(o))
    return emitted


_PAYLOAD = {"resume_step": 6, "total_steps": 8, "steps_skipped": 6, "similarity": 0.999}


# --- pipe emission --------------------------------------------------------

def test_warm_start_emits_artifact_with_payload():
    emitted = _run(_PAYLOAD)
    arts = [o for o in emitted if isinstance(o, WarmStartGenerationOutput)]
    assert len(arts) == 1
    a = arts[0]
    assert (a.resume_step, a.total_steps, a.steps_skipped, a.similarity) == (6, 8, 6, 0.999)
    assert a.index == 0


def test_warm_start_emits_status_line_with_k_of_n():
    emitted = _run(_PAYLOAD)
    states = [getattr(o, "state", "") for o in emitted if isinstance(o, ProgressGenerationOutput)]
    assert any("resumed at step 6/8" in s for s in states)


def test_cold_run_emits_nothing_warm_start():
    emitted = _run(None)  # last_warm_start None -> cold
    assert not any(isinstance(o, WarmStartGenerationOutput) for o in emitted)
    assert not any("resumed at step" in getattr(o, "state", "") for o in emitted
                   if isinstance(o, ProgressGenerationOutput))


# --- registration + serialization ----------------------------------------

def test_registered_as_pipe_artifact():
    spec = output_type_registry.spec_for(
        WarmStartGenerationOutput(index=0, resume_step=6, total_steps=8, steps_skipped=6, similarity=0.99)
    )
    assert spec is not None
    assert spec.key == "warm_start"
    assert spec.resolve_message_type(None) == "pipe_artifact"


def test_serializer_shape():
    out = WarmStartGenerationOutput(index=0, resume_step=14, total_steps=28, steps_skipped=14, similarity=0.997)
    payload = serialize_warm_start_output(out, SerializeContext(generation_id="g"))
    assert payload["artifact_type"] == "warm_start"
    assert payload["artifact_data"] == {
        "resume_step": 14, "total_steps": 28, "steps_skipped": 14, "similarity": 0.997,
    }
