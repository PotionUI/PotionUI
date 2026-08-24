"""Tests for the shared seed-loop base: cancellation is threaded onto the
context so `generate_one` (and anything it calls) can observe it, and a
result produced while cancelled is never emitted."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInput, PipeInputSpec, PipeOutputSpec
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext


class _FakePipe(BaseGeneratorPipe):
    name = "test_generator"
    description = "test"

    def __init__(self, config, *, generate_fn):
        super().__init__(config)
        self._generate_fn = generate_fn
        self.seen_is_cancelled: List[Any] = []

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {"quantity": 1, "seed": -1}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return []

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "", is_array=True)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return []

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        return GeneratorContext(quantity=int(self.config.get("quantity", 1)), input_seeds=None)

    def generate_one(self, ctx: GeneratorContext, index, seed, progress) -> Any:
        # Record what the base actually stashed, so the test can assert it's
        # always callable regardless of what the manager passed to process().
        self.seen_is_cancelled.append(ctx.is_cancelled)
        return self._generate_fn(ctx, index, seed)


def _pipe(generate_fn, config_overrides=None):
    config = {**_FakePipe.get_default_config(), **(config_overrides or {})}
    return _FakePipe(config, generate_fn=generate_fn)


# -- ctx.is_cancelled is always callable -------------------------------------

def test_ctx_is_cancelled_is_callable_when_manager_passes_none():
    pipe = _pipe(lambda ctx, i, seed: "item")
    pipe.process(PipeInput(input={}), lambda o: None, is_cancelled=None)

    assert len(pipe.seen_is_cancelled) == 1
    assert callable(pipe.seen_is_cancelled[0])
    assert pipe.seen_is_cancelled[0]() is False


def test_ctx_is_cancelled_reflects_the_manager_probe():
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return False

    pipe = _pipe(lambda ctx, i, seed: "item")
    pipe.process(PipeInput(input={}), lambda o: None, is_cancelled=is_cancelled)

    assert pipe.seen_is_cancelled[0] is is_cancelled
    assert calls["n"] >= 1


# -- a result produced while cancelled is discarded, not emitted ------------

def test_result_produced_while_cancelled_is_not_emitted():
    """`is_cancelled` flips to True DURING `generate_one` (as a mid-sampling
    SamplingCancelled-raising loop's caller would observe if it raced past
    the check) -- generate_one still returns normally here, but the base
    must not treat that as a real result."""
    cancelled = {"flag": False}

    def is_cancelled():
        return cancelled["flag"]

    def generate_fn(ctx, index, seed):
        cancelled["flag"] = True  # cancellation observed mid-generate_one
        return "half-sampled"

    pipe = _pipe(generate_fn, config_overrides={"quantity": 3})
    emitted = []
    result = pipe.process(PipeInput(input={}), emitted.append, is_cancelled=is_cancelled)

    assert result.output["image"] == []
    # `emit_results` (the default Gallery emission) ran with an empty
    # results list -- "half-sampled" never reached it.
    gallery = [o for o in emitted if getattr(o, "images", None)]
    assert gallery == []


def test_uncancelled_result_is_emitted_normally():
    pipe = _pipe(lambda ctx, i, seed: "ok", config_overrides={"quantity": 1})
    result = pipe.process(PipeInput(input={}), lambda o: None, is_cancelled=lambda: False)

    assert result.output["image"] == ["ok"]
