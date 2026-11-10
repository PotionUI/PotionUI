from __future__ import annotations

import torch

import src.platform.runtime.native.vae.loader as vae_loader
from src.platform.runtime.native.detect.vae_detect import (
    detect_minimax_h3_image_vae_config,
    detect_minimax_h3_video_vae_config,
)
from src.platform.runtime.native.kernels import pick_operations
from src.platform.runtime.native.vae.minimax_h3_image import (
    LATENTS_MEAN,
    LATENTS_STD,
    MiniMaxH3ImageVAE,
    pixels_to_vae_input,
    vae_output_to_unit,
)
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE

from .._header_listings import load_header

UPSTREAM_LATENTS_MEAN = [
    0.858090341091156, -0.9606591463088989, 1.0661640167236328, -0.5090325474739075, -0.2727581858634949,
    -1.3675414323806763, -0.2553254961967468, -0.26907554268836975, -0.5376840829849243, -0.0464097298681736,
    0.6657370328903198, 0.19690127670764923, -0.5460608005523682, -0.4035342037677765, -0.23683024942874908,
    0.25928452610969543, -0.30133944749832153, 0.211341992020607, -1.1206848621368408, 0.3581933379173279,
    -0.04225143790245056, 0.2604829967021942, 0.22864092886447906, 0.7056031823158264,
]
UPSTREAM_LATENTS_STD = [
    1.2223774194717407, 1.2767263650894165, 1.6831774711608887, 1.7549455165863037, 1.5636216402053833,
    2.194143533706665, 0.9653137922286987, 1.0569885969161987, 0.841948926448822, 0.7729952931404114,
    1.8955937623977661, 0.946841835975647, 0.7996809482574463, 0.44988900423049927, 0.7197399735450745,
    0.6936293244361877, 2.961095094680786, 2.7694199085235596, 3.0496184825897217, 2.1088054180145264,
    3.276226282119751, 3.1627357006073, 2.2816812992095947, 2.6127843856811523,
]
DTYPES = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16}


def _state(listing: str) -> dict[str, torch.Tensor]:
    return {name: torch.empty(entry["shape"], dtype=DTYPES.get(entry["dtype"], torch.float16), device="meta")
            for name, entry in load_header(listing).items()}


def _tiny_decoder(vae_class=MiniMaxH3ImageVAE, **kwargs):
    with torch.device("meta"):
        vae = vae_class(decoder_num_layers=1, decoder_num_attention_heads=2, decoder_attention_head_dim=16,
                        operations=pick_operations(torch.float32, torch.float32), **kwargs)
    vae.drop_encoder()
    vae.to_empty(device="cpu")
    generator = torch.Generator().manual_seed(0)
    with torch.no_grad():
        for parameter in vae.parameters():
            parameter.copy_(torch.randn(parameter.shape, generator=generator) * 0.1)
    vae.post_load()
    return vae.eval()


def test_the_image_vae_file_is_detected_as_the_image_vae_only():
    image = _state("minimax_h3_image/vae_fp16")
    assert detect_minimax_h3_image_vae_config(image) == {
        "latent_channels": 24, "decoder_num_layers": 24,
        "decoder_num_attention_heads": 16, "decoder_attention_head_dim": 64,
    }
    assert detect_minimax_h3_video_vae_config(image) is None
    video = _state("minimax_h3/video_vae")
    assert detect_minimax_h3_image_vae_config(video) is None
    assert detect_minimax_h3_video_vae_config(video) is not None


def test_the_image_vae_has_exactly_the_files_keys():
    image = _state("minimax_h3_image/vae_fp16")
    config = detect_minimax_h3_image_vae_config(image)
    with torch.device("meta"):
        vae = MiniMaxH3ImageVAE.from_config(config, pick_operations(torch.float16, torch.float16))
    assert {k: tuple(v.shape) for k, v in vae.state_dict().items()} == {k: tuple(v.shape) for k, v in image.items()}
    assert vae.decoder.rope.num_axes == 2
    assert vae.decoder.rope.inv_freq.numel() == 8
    assert vae.decoder.patch_size_t == 1


def test_latent_statistics_are_the_upstream_config_values():
    assert list(LATENTS_MEAN) == UPSTREAM_LATENTS_MEAN
    assert list(LATENTS_STD) == UPSTREAM_LATENTS_STD
    vae = _tiny_decoder()
    assert torch.equal(vae.latents_mean, torch.tensor(UPSTREAM_LATENTS_MEAN))
    latent = torch.randn(1, 24, 1, 2, 3)
    assert torch.allclose(vae.denormalize(vae.normalize(latent)), latent, atol=1e-6)


def test_one_latent_frame_decodes_to_one_image_sixteen_times_larger():
    vae = _tiny_decoder()
    with torch.no_grad():
        decoded = vae.decode(torch.randn(1, 24, 1, 4, 6))
    assert decoded.shape == (1, 3, 1, 64, 96)
    assert torch.isfinite(decoded).all()


def test_the_image_decoder_appends_zeros_where_the_video_decoder_appends_its_mask_token():
    latent = torch.randn(1, 24, 1, 2, 2)
    image = _tiny_decoder()
    with torch.no_grad():
        before = image.decode(latent)
        image.decoder.mask_token.add_(5.0)
        after = image.decode(latent)
    assert torch.equal(before, after)
    video = _tiny_decoder(MiniMaxH3VideoVAE)
    tokens = torch.randn(1, 24, 1, 2, 2)
    with torch.no_grad():
        before = video.decoder(tokens)
        video.decoder.mask_token.add_(5.0)
        after = video.decoder(tokens)
    assert not torch.equal(before, after)


def test_pixels_round_trip_through_the_imagenet_normalisation():
    pixels = torch.rand(1, 3, 1, 4, 4) * 2.0 - 1.0
    assert torch.allclose(vae_output_to_unit(pixels_to_vae_input(pixels)) * 2.0 - 1.0, pixels, atol=1e-6)


def test_a_vae_file_with_the_image_decoder_loads_through_the_image_loader(monkeypatch):
    calls = []
    monkeypatch.setattr(vae_loader, "load_minimax_h3_image_vae", lambda *a, **k: calls.append("image") or "image")
    monkeypatch.setattr(vae_loader, "load_minimax_h3_video_vae", lambda *a, **k: calls.append("video") or "video")
    assert vae_loader.build_native_vae("vae.safetensors", None, _state("minimax_h3_image/vae_fp16"), {}) == "image"
    assert vae_loader.build_native_vae("vae.safetensors", None, _state("minimax_h3/video_vae"), {}) == "video"
    assert calls == ["image", "video"]
