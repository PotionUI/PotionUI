from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
import torch.nn.functional as F
from PIL import Image

from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import ReferenceMedia
from src.pipelines.pipes.generator.video_minimax_h3.layout import TEXT_TAG
from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe, _MiniMaxH3Ctx
from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan
from src.platform.runtime.native.arch.minimax_h3.config import MiniMaxH3Config
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Model
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.kernels import pick_operations
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import (
    DIRECTOR_AUDIO_LATENTS,
    DIRECTOR_FRAMES,
    DIRECTOR_LATENTS,
    _director_document,
    _RecordingPipe,
    _stub_prepare_text_context,
)
from tests.pipelines.pipes.generator.video_minimax_h3.vae_stub import StubVideoVae

_MAIN = "src.pipelines.pipes.generator.video_minimax_h3.main"

_TINY = {
    "image_model": "minimax_h3", "hidden_size": 64, "num_layers": 2, "num_refiner_layers": 1,
    "num_attention_heads": 2, "attention_head_dim": 40, "ffn_dim": 48, "in_channels": 24,
    "audio_in_channels": 32, "patch_size": (1, 2, 2), "text_dim": 8, "rope_freq_dim": 3,
}
_CONFIGS = {
    "full": dict(_TINY, pruned=False, time_embed_dim=12, freq_dim=8, time_embed_hidden_dim=16),
    "pruned": dict(_TINY, pruned=True, time_embed_dim=6, adaln_curve_grid=5),
}
_PROJECTION = torch.randn(24, 3, generator=torch.Generator().manual_seed(1))


def _tiny_dit(config: dict) -> MiniMaxH3Model:
    torch.manual_seed(0)
    model = MiniMaxH3Model(
        MiniMaxH3Config.from_detect_config(config), pick_operations(torch.float32, torch.float32), dtype=torch.float32,
    )
    state = {
        key: value.clone() if not value.is_floating_point()
        else torch.ones_like(value) if ".norm" in key else torch.randn_like(value) * 0.2
        for key, value in model.state_dict().items()
    }
    load_into_module(model, state, match_model_spec(config))
    return model.eval()


class _ContentVae(StubVideoVae):
    def encode(self, pixels, sample_posterior=True, generator=None):
        pooled = F.avg_pool3d(pixels.float(), (1, 16, 16))
        return torch.einsum("oc,bcfhw->bofhw", _PROJECTION, pooled)

    def decode(self, z):
        b, _c, f, h, w = z.shape
        return torch.zeros(b, 3, f, h, w)


class _AudioVae:
    latents_mean = torch.zeros(32)
    latents_std = torch.ones(32)

    def decode(self, latents):
        return torch.zeros(2, 1, latents.shape[-1] * 4)


def _bundle(dit_module):
    return SimpleNamespace(
        spec=SimpleNamespace(family="minimax_h3", variant="h3", sampling_settings={},
                             latent_format={"format": "minimax_h3", "latent_channels": 24}),
        dit=SimpleNamespace(compute_dtype=torch.float32, estimated_vram_gb=0.0, module=dit_module,
                            move_to=lambda d: None, offload=lambda: None),
        video_vae=SimpleNamespace(module=_ContentVae(), compute_dtype=torch.float32,
                                  move_to=lambda d: None, offload=lambda: None),
        audio_vae=SimpleNamespace(module=_AudioVae(), move_to=lambda d: None, offload=lambda: None),
        te=None, te_cache_key=None,
    )


def _conditioning():
    torch.manual_seed(5)
    return SimpleNamespace(embeds={
        "context": torch.randn(1, 3, 8), "token_tags": torch.full((3,), TEXT_TAG, dtype=torch.long),
    })


def _generate(dit_module, references, **config) -> torch.Tensor:
    bundle = _bundle(dit_module)
    extra = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_conditioning()], steps=4, height=64, width=64, frames=22,
        num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=2, device="cpu",
        dtype=torch.float32, spec=bundle.spec, keyframe_images=[], keyframe_anchors=(),
        references=tuple(references), audio_source="generate", decode=False,
    )
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "preview": False, "steps": 4, "mode": "references", **config,
    })
    pipe._audio_results = []
    with patch(f"{_MAIN}.encode_frames_to_mp4", return_value="/tmp/out.mp4"):
        return pipe.generate_one(
            GeneratorContext(quantity=1, input_seeds=[7], extra=extra), 0, 7, ProgressEmitter([].append, title="t"),
        )


def _picture(rgb):
    return ReferenceMedia(kind="image", image=Image.new("RGB", (64, 64), rgb))


def _refmod(seed):
    return ReferenceMedia(kind="image", latent=torch.randn(1, 24, 1, 4, 4, generator=torch.Generator().manual_seed(seed)) * 3)


def _relative(a, b) -> float:
    return ((a - b).norm() / b.norm()).item()


@pytest.fixture(scope="module", params=sorted(_CONFIGS))
def tiny_dit(request):
    return _tiny_dit(_CONFIGS[request.param])


def test_the_reference_picture_steers_the_generated_video(tiny_dit):
    red = _generate(tiny_dit, [_picture((250, 10, 10))])
    assert torch.equal(red, _generate(tiny_dit, [_picture((250, 10, 10))]))
    assert _relative(_generate(tiny_dit, [_picture((10, 10, 250))]), red) > 0.05


def test_a_refmod_next_to_a_picture_steers_the_generated_video(tiny_dit):
    with_first = _generate(tiny_dit, [_picture((250, 10, 10)), _refmod(3)])
    with_second = _generate(tiny_dit, [_picture((250, 10, 10)), _refmod(4)])
    assert _relative(with_second, with_first) > 0.02


@pytest.mark.parametrize("chunk_rows", [3, 5, 7])
def test_sequence_chunking_leaves_a_reference_run_unchanged(tiny_dit, chunk_rows):
    references = [_picture((250, 10, 10)), _refmod(3)]
    torch.testing.assert_close(
        _generate(tiny_dit, references, seq_chunk_rows=chunk_rows), _generate(tiny_dit, references),
        rtol=1e-5, atol=1e-5,
    )


def _window_condition_rows(references, reference_indices):
    seen = []

    def dit_forward(*, hidden_states, audio_hidden_states, num_condition_video_rows=0, **kwargs):
        seen.append(hidden_states[0, :num_condition_video_rows].clone())
        return hidden_states * 0.1, audio_hidden_states * -0.2
    dit_forward.prepare_text_context = _stub_prepare_text_context

    document = _director_document(1, sub_types={0: "t2v"})
    document["segments"][0]["reference_indices"] = list(reference_indices)
    plan = build_director_plan(document, default_seed=-1)
    bundle = _bundle(dit_forward)
    extra = _MiniMaxH3Ctx(
        bundle=bundle, conditioning=[_conditioning()], steps=2, height=32, width=32, frames=DIRECTOR_FRAMES,
        num_latent_frames=DIRECTOR_LATENTS, latent_height=2, latent_width=2, num_audio_latents=DIRECTOR_AUDIO_LATENTS,
        device="cpu", dtype=torch.float32, spec=bundle.spec, keyframe_images=[], keyframe_anchors=(),
        references=tuple(references), audio_source="generate", decode=True, plan=plan,
    )
    pipe = _RecordingPipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "references", "preview": False, "steps": 2,
    })
    pipe._audio_results = []
    pipe._video_resolution = (32, 32)
    with patch(f"{_MAIN}.encode_frames_to_mp4") as encode:
        encode.side_effect = lambda frames, path, **kw: path
        pipe.generate_one(GeneratorContext(quantity=1, input_seeds=[7], extra=extra), 0, 7,
                          ProgressEmitter([].append, title="t"))
    return seen[0]


def test_a_director_window_citing_a_refmod_carries_exactly_its_rows():
    picture, mod = _picture((250, 10, 10)), _refmod(3)
    cited_in_pool = _window_condition_rows([picture, mod], (1,))
    alone = _window_condition_rows([mod], (0,))
    assert cited_in_pool.shape[0] == 4
    assert torch.equal(cited_in_pool, alone)
    assert not torch.equal(cited_in_pool, _window_condition_rows([picture, mod], (0,)))


def test_a_director_window_citing_nothing_carries_no_reference_rows():
    assert _window_condition_rows([_picture((250, 10, 10)), _refmod(3)], ()).shape[0] == 0


def _stacked_pictures_refmod(seed):
    latent = torch.randn(1, 24, 3, 4, 4, generator=torch.Generator().manual_seed(seed)) * 3
    return ReferenceMedia(kind="video", latent=latent)


def test_a_stacked_pictures_refmod_steers_the_generated_video(tiny_dit):
    first = _generate(tiny_dit, [_picture((250, 10, 10)), _stacked_pictures_refmod(3)])
    second = _generate(tiny_dit, [_picture((250, 10, 10)), _stacked_pictures_refmod(4)])
    assert _relative(second, first) > 0.02


def test_a_director_window_citing_a_stacked_pictures_refmod_carries_every_frame_of_it():
    picture, stack = _picture((250, 10, 10)), _stacked_pictures_refmod(3)
    cited = _window_condition_rows([picture, stack], (1,))
    assert cited.shape[0] == 3 * 2 * 2
    assert torch.equal(cited, _window_condition_rows([stack], (0,)))
