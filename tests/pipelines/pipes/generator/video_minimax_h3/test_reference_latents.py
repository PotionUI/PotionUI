from __future__ import annotations

from dataclasses import replace

import pytest
import torch
from PIL import Image

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeInput,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.pipes._shared.generation.reference_order import pack_references
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe
from src.pipelines.pipes.generator.video_minimax_h3.windows import DirectorPlanError, build_director_plan
from src.plugin_api.pipes import PreEncodedReference
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    _director_document,
    _fake_bundle,
    _fake_conditioning,
    _run_ref2va_generate_one,
)
from tests.pipelines.pipes.generator.video_minimax_h3.test_reference_rows import _picture, _window_condition_rows


def _latent(kind, h, w, seed, frames=1):
    if kind == "audio":
        return torch.randn(2, 32, frames, generator=torch.Generator().manual_seed(seed))
    return torch.randn(1, 24, frames, h, w, generator=torch.Generator().manual_seed(seed)) * 3


def _reference(kind, name, h=2, w=6, seed=0, frames=1):
    return PreEncodedReference(kind=kind, latent=_latent(kind, h, w, seed, frames), name=name, fingerprint=f"fp:{name}")


class _LatentSourcePipe(BasePipe):
    name = "test/latent_source"

    def __init__(self, references):
        super().__init__({})
        self._references = references

    @classmethod
    def configuration(cls):
        return []

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec("reference_latents", IOType.LATENT, "Pre-encoded references", is_array=True)]

    def process(self, pipe_input, generation_outputs):
        return PipeOutput(output={"reference_latents": list(self._references)})


def _emit(references):
    return _LatentSourcePipe(references).process(PipeInput(input={}), lambda output: None).output["reference_latents"]


def _build(reference_latents, reference_images=(), **config):
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references", "reference_short_edge": 32,
        "references": [{"path": f"ref{i}"} for i in range(len(reference_images))], **config,
    })
    return pipe.build_context(PipeInput(input={
        "model": _fake_bundle(), "conditioning": [_fake_conditioning(3)],
        "reference_images": list(reference_images), "reference_latents": reference_latents,
    }))


def test_the_generator_declares_reference_latents_as_a_latent_array_input():
    spec = next(s for s in GeneratorMinimaxH3Pipe.inputs() if s.name == "reference_latents")
    assert (spec.io_type, spec.is_array, spec.required) == (IOType.LATENT, True, False)


def test_reference_latents_from_an_upstream_pipe_are_packed_after_the_native_references_of_their_kind():
    image_extra = _reference("image", "face", seed=1)
    audio_extra = _reference("audio", "voice", seed=2, frames=3)
    ctx = _build(_emit([audio_extra, image_extra]), [Image.new("RGB", (64, 64), (200, 20, 20))])
    references = ctx.extra.references
    assert [r.kind for r in references] == ["image", "image", "audio"]
    assert references[0].latent is None
    assert torch.equal(references[1].latent, image_extra.latent)
    assert torch.equal(references[2].latent, audio_extra.latent)


def test_reference_latents_merged_from_two_providers_keep_their_order():
    first, second = _reference("image", "a", seed=1), _reference("image", "b", seed=2)
    ctx = _build([[first], [second]])
    assert [r.latent.data_ptr() for r in ctx.extra.references] == [first.latent.data_ptr(), second.latent.data_ptr()]


def test_reference_latents_alone_satisfy_references_mode():
    ctx = _build(_emit([_reference("video", "clip", frames=2)]))
    assert [r.kind for r in ctx.extra.references] == ["video"]


def test_reference_latents_count_toward_the_total_reference_limit():
    from src.pipelines.pipes.generator.video_minimax_h3.conditioning import MAX_REFERENCES

    with pytest.raises(ValueError, match="at most"):
        _build(_emit([_reference("image", str(i), seed=i) for i in range(MAX_REFERENCES + 1)]))


def test_a_reference_latent_reaches_the_packed_condition_rows_after_the_native_picture(tmp_path):
    extra = _reference("image", "face", h=2, w=6, seed=4)
    ctx = _build(_emit([extra]), [Image.new("RGB", (64, 64), (200, 20, 20))])
    captured = _run_ref2va_generate_one(ctx.extra.references, tmp_path)
    blocks, condition_latents, _audio = captured["args"][1:4]
    assert [b.kind for b in blocks] == ["image", "image"]
    assert [tuple(latent.shape[2:5]) for latent in condition_latents] == [(1, 32, 32), (1, 2, 6)]
    other = _build(_emit([_reference("image", "other", h=2, w=6, seed=5)]), [Image.new("RGB", (64, 64), (200, 20, 20))])
    other_latents = _run_ref2va_generate_one(other.extra.references, tmp_path)["args"][2]
    assert torch.equal(condition_latents[0], other_latents[0])
    assert not torch.equal(condition_latents[1], other_latents[1])


def _plan_with(reference_indices):
    plan = build_director_plan(_director_document(1), default_seed=-1)
    return replace(plan, windows=(replace(plan.windows[0], reference_indices=reference_indices),))


def test_a_director_window_may_cite_a_reference_latent_by_its_packed_index():
    kinds = [kind for kind, _media in pack_references(["picture"], [], [], extras=[_reference("image", "face")])]
    GeneratorMinimaxH3Pipe._validate_refs_director_plan(_plan_with((1,)), reference_kinds=kinds)
    with pytest.raises(DirectorPlanError, match="out of range"):
        GeneratorMinimaxH3Pipe._validate_refs_director_plan(_plan_with((2,)), reference_kinds=kinds)


def test_a_director_window_citing_a_reference_latent_carries_exactly_its_rows():
    extra = _reference("image", "face", h=4, w=4, seed=3)
    picture = Image.new("RGB", (64, 64), (250, 10, 10))
    pool = GeneratorMinimaxH3Pipe._load_reference_media(pack_references([picture], [], [], extras=[extra]))
    alone = GeneratorMinimaxH3Pipe._load_reference_media(pack_references([], [], [], extras=[extra]))
    cited = _window_condition_rows(pool, (1,))
    assert cited.shape[0] == 4
    assert torch.equal(cited, _window_condition_rows(alone, (0,)))
    assert not torch.equal(cited, _window_condition_rows([_picture((250, 10, 10))], (0,)))
