"""generator/krea2 wiring for step-windowed LoRAs.

Where ``tests/platform/runtime/native/lora/test_lora_step_window.py`` proves the
hook's arithmetic against real weights, this file proves the PIPE wiring: that a
windowed entry off the bundle reaches the sampler as a hook, that the patch
lands and leaves on the right step edges, and — the cache-safety guarantee —
that the removal still runs when sampling raises.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe
from src.platform.runtime.native.lora.step_window import LoraStepWindow

_FLOW = "src.pipelines.pipes._shared.generation.flow_generator_pipe"
_WINDOW = "src.platform.runtime.native.lora.step_window"


class _FakeSpec:
    family = "krea2"
    variant = "krea2_turbo"
    latent_format = {"latent_channels": 16}
    sampling_settings = {"guidance": "none"}


class _SteppingGenerator:
    """A generator whose ``sample`` drives the real sampler hook protocol:
    ``on_start`` then one ``on_step`` per step, exactly as the euler loop does."""

    instances: list["_SteppingGenerator"] = []
    raise_at_step: "int | None" = None

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.dit = dit
        self.spec = _FakeSpec()
        self.sample_calls = []
        _SteppingGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        return width, height

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, 1, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append(kw)
        steps, hooks = kw["steps"], kw["hooks"]
        for hook in hooks:
            hook.on_start(steps)
        for i in range(steps):
            if self.raise_at_step is not None and i == self.raise_at_step:
                raise RuntimeError("sampling blew up mid-window")
            for hook in hooks:
                hook.on_step(i, steps, torch.zeros(1), 1.0, None)
        for hook in hooks:
            hook.on_end()
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


class _Journal:
    """Records windowed apply/restore as the sampler walks the steps."""

    def __init__(self):
        self.events: list[str] = []
        self.step = "pre"

    def apply(self, module, stack):
        self.events.append(f"{self.step}:apply({len(stack)})")

    def restore(self, snapshot):
        self.events.append(f"{self.step}:restore")


@pytest.fixture
def journal(monkeypatch):
    j = _Journal()
    monkeypatch.setattr(f"{_WINDOW}.apply_loras", j.apply)
    monkeypatch.setattr(f"{_WINDOW}.restore_lora_state", j.restore)
    monkeypatch.setattr(f"{_WINDOW}.snapshot_lora_state", lambda module: "SNAP")

    original = _SteppingGenerator.sample

    def tracked(self, conditioning, latents_shape, **kw):
        steps, hooks = kw["steps"], kw["hooks"]
        j.step = "start"
        for hook in hooks:
            hook.on_start(steps)
        for i in range(steps):
            if self.raise_at_step is not None and i == self.raise_at_step:
                raise RuntimeError("sampling blew up mid-window")
            j.step = f"after{i}"
            for hook in hooks:
                hook.on_step(i, steps, torch.zeros(1), 1.0, None)
        j.step = "end"
        for hook in hooks:
            hook.on_end()
        self.sample_calls.append(kw)
        return torch.zeros(latents_shape)

    monkeypatch.setattr(_SteppingGenerator, "sample", tracked)
    yield j
    _SteppingGenerator.sample = original


def _bundle(windowed_loras=()):
    return SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=26.0, module=object()),
        te_encoder=object(),
        vae=object(),
        te_cache_key=None,
        windowed_loras=tuple(windowed_loras),
    )


def _pipe_input(windowed_loras=()):
    return PipeInput(input={
        "model": _bundle(windowed_loras),
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})],
        "seed": [1],
    })


def _make_pipe(**over):
    cfg = GeneratorKrea2Pipe.get_default_config()
    cfg.update(over)
    return GeneratorKrea2Pipe(config=cfg)


def _entry(start, end, path="/m/turbo-sda.safetensors", weight=1.0):
    return {"file_path": path, "weight": weight, "window": LoraStepWindow(start, end)}


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    _SteppingGenerator.instances.clear()
    _SteppingGenerator.raise_at_step = None
    monkeypatch.setattr(f"{_FLOW}.make_device_plan", lambda **_: None)
    monkeypatch.setattr(f"{_FLOW}.NativeGenerator", _SteppingGenerator)
    # No disk: one fake state dict per windowed entry.
    monkeypatch.setattr(
        f"{_FLOW}.load_windowed_lora_stack",
        lambda loras: [({"fake": torch.zeros(1)}, l["weight"], l["window"]) for l in loras],
    )
    yield


# --- ordering ------------------------------------------------------------

def test_first_two_of_eight_steps_applies_at_start_and_removes_after_step_two(journal):
    """The motivating case: F16/krea2-turbo-sda, on for steps 1-2 of 8."""
    pipe = _make_pipe(steps=8, preview=False)
    pipe.process(_pipe_input([_entry(1, 2)]), lambda o: None)

    assert journal.events == ["start:apply(1)", "after1:restore"], (
        "expected one apply entering step 1 and one restore after step 2 (0-based index 1)"
    )


def test_a_late_window_applies_and_removes_at_its_own_edges(journal):
    pipe = _make_pipe(steps=8, preview=False)
    pipe.process(_pipe_input([_entry(3, 5)]), lambda o: None)

    assert journal.events == ["after1:apply(1)", "after4:restore"], (
        "window 3-5 (1-based) opens entering 0-based step 2 and closes after 0-based step 4"
    )


def test_an_open_ended_window_is_closed_once_by_the_pipe_not_left_applied(journal):
    pipe = _make_pipe(steps=4, preview=False)
    pipe.process(_pipe_input([_entry(1, None)]), lambda o: None)

    assert journal.events == ["start:apply(1)", "end:restore"], (
        "a window running to the last step must still be removed when the run ends"
    )


# --- cache safety --------------------------------------------------------

def test_sampling_error_inside_the_window_still_removes_the_patch(journal):
    """try/finally, not on_end: the DiT is shared through the MODELS cache, so a
    crash mid-window must not leave it patched for the next generation."""
    _SteppingGenerator.raise_at_step = 3
    pipe = _make_pipe(steps=8, preview=False)

    with pytest.raises(RuntimeError, match="blew up"):
        pipe.process(_pipe_input([_entry(1, 6)]), lambda o: None)

    assert journal.events[0] == "start:apply(1)"
    assert journal.events[-1].endswith(":restore"), \
        f"the patch was left on the cached DiT: {journal.events}"


def test_the_hook_is_dropped_after_each_seed(journal):
    """The hook is per-sampling-call state; leaking it across seeds would
    reuse a closed hook's snapshot on the next image."""
    pipe = _make_pipe(steps=4, quantity=2, preview=False)
    pipe.process(PipeInput(input={
        "model": _bundle([_entry(1, 2)]),
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})] * 2,
        "seed": [1, 2],
    }), lambda o: None)

    assert journal.events == ["start:apply(1)", "after1:restore"] * 2
    assert pipe.extra_step_hooks() == ()


# --- no window = unchanged behaviour -------------------------------------

def test_no_windowed_loras_installs_no_hook_and_touches_nothing(journal):
    pipe = _make_pipe(steps=8, preview=False)
    pipe.process(_pipe_input([]), lambda o: None)

    assert journal.events == []
    gen = _SteppingGenerator.instances[-1]
    installed = [type(hook).__name__ for hook in gen.sample_calls[0]["hooks"]]
    assert "LoraStepWindowHook" not in installed
    assert installed == ["ProgressHook"], "the hook list must be exactly what it was before windows existed"


def test_a_bundle_without_the_field_is_supported(journal):
    """Families whose loader has not adopted windows hand over a bundle with no
    ``windowed_loras`` at all — that must read as "none", not crash."""
    bundle = SimpleNamespace(
        dit=SimpleNamespace(estimated_vram_gb=26.0, module=object()),
        te_encoder=object(), vae=object(), te_cache_key=None,
    )
    pipe = _make_pipe(steps=4, preview=False)
    pipe.process(PipeInput(input={
        "model": bundle,
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})],
        "seed": [1],
    }), lambda o: None)
    assert journal.events == []


def test_a_generator_that_drops_the_hook_fails_loudly(journal):
    """A subclass (e.g. the krea2-edit plugin pipe) that overrides generate_one
    and builds its own hook list without extra_step_hooks() would apply the LoRA
    for the whole run. That must be an error, not a quietly wrong image."""

    class _DropsTheHook(GeneratorKrea2Pipe):
        def generate_one(self, ctx, index, seed, progress):
            gen = ctx.extra["generator"]
            return gen.sample(None, (1, 16, 1, 8, 8), steps=ctx.extra["steps"], hooks=[])

    pipe = _DropsTheHook(config={**GeneratorKrea2Pipe.get_default_config(), "steps": 8, "preview": False})
    with pytest.raises(RuntimeError, match="never applied"):
        pipe.process(_pipe_input([_entry(1, 2)]), lambda o: None)


def test_the_guard_does_not_fire_without_a_window(journal):
    class _DropsTheHook(GeneratorKrea2Pipe):
        def generate_one(self, ctx, index, seed, progress):
            gen = ctx.extra["generator"]
            gen.sample(None, (1, 16, 1, 8, 8), steps=ctx.extra["steps"], hooks=[])
            return None

    pipe = _DropsTheHook(config={**GeneratorKrea2Pipe.get_default_config(), "steps": 8, "preview": False})
    pipe.process(_pipe_input([]), lambda o: None)  # must not raise


# --- interaction with warm start -----------------------------------------

def test_iterate_mode_is_disabled_when_a_window_is_present():
    """Warm start resumes on a truncated schedule whose step indices restart at
    0, which would fire every window at the wrong point in the run."""
    pipe = _make_pipe(steps=8, iterate_mode=True, preview=False)
    windowed = pipe.build_context(_pipe_input([_entry(1, 2)]))
    plain = pipe.build_context(_pipe_input([]))

    assert windowed.extra["iterate_mode"] is False
    assert plain.extra["iterate_mode"] is True, "iterate mode must be untouched without a window"
