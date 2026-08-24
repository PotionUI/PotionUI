"""Tests for VAE detection (flux 16ch AE vs flux2 32ch AE vs causal-3D)."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.detect.vae_detect import (
    detect_causal3d_v2_vae_config,
    detect_causal3d_vae_config,
    detect_ltx_duration_head_config,
    detect_ltx_latent_upsampler_config,
    detect_minimax_h3_audio_vae_config,
    detect_minimax_h3_video_vae_config,
    detect_seedvr2_vae_config,
    detect_vae_config,
)

from .conftest import (
    causal3d_v2_vae_sd,
    causal3d_vae_sd,
    flux2_ae_sd,
    flux_ae_sd,
    minimax_h3_audio_vae_sd,
    minimax_h3_video_vae_sd,
    seedvr2_vae_sd,
)


def test_detect_flux_ae():
    c = detect_vae_config(flux_ae_sd(latent=16))
    assert c["vae_type"] == "flux_ae"
    assert c["latent_channels"] == 16
    assert c["in_channels"] == 3
    assert c["out_channels"] == 3
    assert c["key_layout"] == "ldm"
    assert c["has_quant_conv"] is False
    assert c["has_batchnorm"] is False


def test_detect_flux2_ae():
    c = detect_vae_config(flux2_ae_sd(latent=32))
    assert c["vae_type"] == "flux2_ae"
    assert c["latent_channels"] == 32
    assert c["key_layout"] == "diffusers"
    assert c["has_quant_conv"] is True
    assert c["has_batchnorm"] is True


def test_non_vae_returns_none():
    assert detect_vae_config({"foo": torch.zeros(1)}) is None


def test_flux2_distinguished_by_diffusers_keys():
    # both AEs share encoder/decoder conv keys; the mid_block / bn / quant_conv
    # markers are what separate flux2 from flux.
    flux = flux_ae_sd()
    flux2 = flux2_ae_sd()
    assert detect_vae_config(flux)["vae_type"] != detect_vae_config(flux2)["vae_type"]


def test_flux_detectors_reject_causal3d_shape():
    # the causal-3D signature key has no flux/flux2 equivalent, so the flux
    # detector must not accidentally match it.
    assert detect_vae_config(causal3d_vae_sd()) is None


class TestDetectCausal3DVae:
    def test_detects_qwen_image_shape(self):
        c = detect_causal3d_vae_config(causal3d_vae_sd(latent=16))
        assert c["vae_type"] == "qwen_image"
        assert c["latent_channels"] == 16
        assert c["in_channels"] == 3
        assert c["out_channels"] == 3

    def test_non_causal3d_returns_none(self):
        assert detect_causal3d_vae_config({"foo": torch.zeros(1)}) is None
        assert detect_causal3d_vae_config(flux_ae_sd()) is None
        assert detect_causal3d_vae_config(flux2_ae_sd()) is None

    def test_wan22_nested_upsamples_excluded(self):
        # Wan 2.2's nested upsamples.upsamples.* keys mean this detector must
        # decline -- that shape is handled by detect_causal3d_v2_vae_config instead.
        assert detect_causal3d_vae_config(causal3d_vae_sd(wan22=True)) is None


class TestDetectCausal3DV2Vae:
    def test_detects_wan22_shape(self):
        c = detect_causal3d_v2_vae_config(causal3d_v2_vae_sd(latent=48))
        assert c["vae_type"] == "wan2.2"
        assert c["latent_channels"] == 48
        assert c["in_channels"] == 3
        assert c["out_channels"] == 3

    def test_non_wan22_returns_none(self):
        assert detect_causal3d_v2_vae_config({"foo": torch.zeros(1)}) is None
        assert detect_causal3d_v2_vae_config(flux_ae_sd()) is None
        assert detect_causal3d_v2_vae_config(flux2_ae_sd()) is None

    def test_wan21_shape_rejected_missing_nested_key(self):
        # the plain Wan 2.1 shape (no nested upsamples.0.upsamples.0.*) must
        # not be picked up by the v2 detector.
        assert detect_causal3d_v2_vae_config(causal3d_vae_sd(wan22=False)) is None


class TestDetectSeedVR2Vae:
    def test_detects_seedvr2_shape(self):
        c = detect_seedvr2_vae_config(seedvr2_vae_sd(latent=16))
        assert c["vae_type"] == "seedvr2"
        assert c["latent_channels"] == 16
        assert c["in_channels"] == 3
        assert c["out_channels"] == 3

    def test_non_seedvr2_returns_none(self):
        # 4D flux convs, Wan gamma-keyed 3D, and bare junk must all decline.
        assert detect_seedvr2_vae_config({"foo": torch.zeros(1)}) is None
        assert detect_seedvr2_vae_config(flux_ae_sd()) is None
        assert detect_seedvr2_vae_config(flux2_ae_sd()) is None
        assert detect_seedvr2_vae_config(causal3d_vae_sd()) is None

    def test_quant_conv_excluded(self):
        # a 5D-conv AE that also ships quant_conv is NOT the SeedVR2 VAE.
        sd = seedvr2_vae_sd()
        sd["quant_conv.weight"] = torch.zeros(32, 32, 1, 1, 1)
        assert detect_seedvr2_vae_config(sd) is None


class TestSeedVR2VaeMutualExclusivity:
    """The SeedVR2 VAE shares ``encoder.conv_in``/``decoder.conv_out`` key names
    with the Flux 2D AE — the ONLY discriminator is conv rank (4D vs 5D). Verify
    each detector claims exactly its own family and declines the other."""

    def test_flux_2d_detector_rejects_seedvr2(self):
        # 5D conv_in -> the 2D-AE detector must decline (regression guard for the
        # ndim==4 gate added alongside SeedVR2).
        assert detect_vae_config(seedvr2_vae_sd()) is None

    def test_seedvr2_detector_rejects_flux_and_wan(self):
        assert detect_seedvr2_vae_config(flux_ae_sd()) is None
        assert detect_seedvr2_vae_config(causal3d_vae_sd()) is None
        assert detect_seedvr2_vae_config(causal3d_v2_vae_sd()) is None

    def test_causal3d_detectors_reject_seedvr2(self):
        # SeedVR2 has no encoder.conv1 / decoder.middle gamma, so the Wan
        # detectors decline it.
        assert detect_causal3d_vae_config(seedvr2_vae_sd()) is None
        assert detect_causal3d_v2_vae_config(seedvr2_vae_sd()) is None


class TestDetectMiniMaxH3VideoVaeConfig:
    def test_detects_from_synthetic_sd(self):
        c = detect_minimax_h3_video_vae_config(minimax_h3_video_vae_sd(latent=24, decoder_dim=128, num_layers=3))
        assert c["latent_channels"] == 24
        assert c["decoder_num_layers"] == 3
        assert c["clip_length"] == 17
        assert c["token_drop"] == 3

    def test_recomputes_head_dim_when_dim_does_not_match_fixed_heads(self):
        # decoder_dim=128 with the fixed 32-head default would need head_dim=4,
        # not the real checkpoint's 64 -- the detector must recompute head_dim
        # rather than silently building a mismatched module.
        c = detect_minimax_h3_video_vae_config(minimax_h3_video_vae_sd(decoder_dim=128))
        assert c["decoder_num_attention_heads"] * c["decoder_attention_head_dim"] == 128

    def test_reads_clip_length_and_token_drop_from_embedded_metadata(self):
        import json

        sd = minimax_h3_video_vae_sd()
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 9, "vae_token_drop": 2})}
        c = detect_minimax_h3_video_vae_config(sd, metadata)
        assert c["clip_length"] == 9
        assert c["token_drop"] == 2

    def test_missing_metadata_falls_back_to_fixed_defaults(self):
        c = detect_minimax_h3_video_vae_config(minimax_h3_video_vae_sd(), metadata=None)
        assert c["clip_length"] == 17
        assert c["token_drop"] == 3

    def test_non_h3_returns_none(self):
        assert detect_minimax_h3_video_vae_config({"foo": torch.zeros(1)}) is None
        assert detect_minimax_h3_video_vae_config(flux_ae_sd()) is None
        assert detect_minimax_h3_video_vae_config(causal3d_vae_sd()) is None
        assert detect_minimax_h3_video_vae_config(seedvr2_vae_sd()) is None

    def test_4d_conv_in_rejected(self):
        # decoder.mask_token/register_tokens present but a 4D conv_in (the
        # Flux-2D-AE shape) must not match -- H3's encoder is causal 3D.
        sd = minimax_h3_video_vae_sd()
        sd["encoder.conv_in.weight"] = torch.zeros(8, 3, 3, 3)
        assert detect_minimax_h3_video_vae_config(sd) is None

    def test_missing_mask_token_rejected(self):
        sd = minimax_h3_video_vae_sd()
        del sd["decoder.mask_token"]
        assert detect_minimax_h3_video_vae_config(sd) is None


class TestDetectMiniMaxH3VideoVaeQuantisedRepack:
    """Kijai's `minimax_h3_video_vae_int8_convrot.safetensors` carries the
    fp16 repack's key set verbatim plus, on each of the ViT decoder's four
    Linears per block, a `weight_scale`/`comfy_quant` sidecar pair -- with
    that Linear's `weight` stored as int8 codes rather than a float
    (`ai/minimax_h3/video_vae_int8_convrot_header.json`). Detection reads
    key names and shapes only, so it must be blind to both changes."""

    def _quantise(self, sd):
        sd = dict(sd)
        sd["decoder.x_embedder.weight"] = sd["decoder.x_embedder.weight"].to(torch.int8)
        sd["decoder.x_embedder.weight_scale"] = torch.zeros(sd["decoder.x_embedder.weight"].shape[0], 1)
        sd["decoder.x_embedder.comfy_quant"] = torch.zeros(72, dtype=torch.uint8)
        return sd

    def test_int8_codes_and_sidecars_still_detect(self):
        c = detect_minimax_h3_video_vae_config(self._quantise(minimax_h3_video_vae_sd(latent=24, num_layers=3)))
        assert c is not None
        assert c["latent_channels"] == 24
        assert c["decoder_num_layers"] == 3

    def test_detection_matches_the_unquantised_twin(self):
        plain = minimax_h3_video_vae_sd(latent=24, num_layers=3)
        assert detect_minimax_h3_video_vae_config(self._quantise(plain)) == detect_minimax_h3_video_vae_config(plain)

    def test_sidecars_alone_do_not_make_a_non_h3_checkpoint_match(self):
        sd = dict(flux_ae_sd())
        sd["decoder.x_embedder.weight_scale"] = torch.zeros(8, 1)
        sd["decoder.x_embedder.comfy_quant"] = torch.zeros(72, dtype=torch.uint8)
        assert detect_minimax_h3_video_vae_config(sd) is None


class TestDetectMiniMaxH3AudioVaeConfig:
    def test_detects_from_synthetic_sd(self):
        c = detect_minimax_h3_audio_vae_config(minimax_h3_audio_vae_sd(latent_channels=32, latent_dim=64, encoder_dim=8))
        assert c["latent_channels"] == 32
        assert c["latent_dim"] == 64
        assert c["encoder_dim"] == 8
        assert c["sample_rate"] == 32000

    def test_reads_sample_rate_from_embedded_metadata(self):
        import json

        sd = minimax_h3_audio_vae_sd()
        metadata = {"minimax_h3_audio_vae": json.dumps({"sample_rate": 44100})}
        c = detect_minimax_h3_audio_vae_config(sd, metadata)
        assert c["sample_rate"] == 44100

    def test_non_h3_returns_none(self):
        assert detect_minimax_h3_audio_vae_config({"foo": torch.zeros(1)}) is None
        assert detect_minimax_h3_audio_vae_config(flux_ae_sd()) is None
        assert detect_minimax_h3_audio_vae_config(seedvr2_vae_sd()) is None

    def test_video_and_audio_detectors_are_mutually_exclusive(self):
        video_sd = minimax_h3_video_vae_sd()
        audio_sd = minimax_h3_audio_vae_sd()
        assert detect_minimax_h3_audio_vae_config(video_sd) is None
        assert detect_minimax_h3_video_vae_config(audio_sd) is None


class TestMiniMaxH3HeaderKeysMatchDetectionSignature:
    """Cross-check the real Comfy-Org repack headers (fetched via range
    request, saved to ai/minimax_h3/*.json -- no weights downloaded) against
    the detectors' own signature keys, so a naming drift in a future repack
    upload is caught here instead of silently mismatching at load time."""

    def _load_header(self, name: str) -> dict:
        import json
        from pathlib import Path

        path = Path("ai/minimax_h3") / name
        if not path.exists():
            pytest.skip(f"{path} not present (fetched once via range request; not part of the repo checkout)")
        with path.open() as f:
            header = json.load(f)
        header.pop("__metadata__", None)
        return header

    def test_video_header_matches_detector_signature_keys(self):
        header = self._load_header("video_vae_header.json")
        assert "decoder.mask_token" in header
        assert "decoder.register_tokens" in header
        assert header["encoder.conv_in.weight"]["shape"][0:1] and len(header["encoder.conv_in.weight"]["shape"]) == 5

    def test_audio_header_matches_detector_signature_keys(self):
        header = self._load_header("audio_vae_header.json")
        assert "pre_block.attn.qkv.weight" in header
        assert "dec_in_proj.weight" in header
        assert "decoder.conv_pre.weight" in header

    def test_audio_header_has_no_weight_norm_keys(self):
        """The discrepancy documented in vae/minimax_h3_audio.py's module
        docstring: the repack was exported with weight_norm already fused
        (remove_weight_norm), so no weight_g/weight_v/parametrizations keys
        exist anywhere in the real checkpoint."""
        header = self._load_header("audio_vae_header.json")
        offenders = [k for k in header if "weight_g" in k or "weight_v" in k or "parametriz" in k]
        assert offenders == []


class TestDetectLtxDurationHead:
    """``detect_ltx_duration_head_config`` -- shape-derived, no embedded
    config (see ``vae_detect.py`` module docstring #10)."""

    def _sd(self, prefix: str = "", *, video_dim: int = 4096, audio_dim: int = 2048,
            pooler: int = 256, queries: int = 1, mlp: int = 256) -> dict:
        keys = {
            "video_input_proj.weight": torch.zeros(pooler, video_dim),
            "video_input_proj.bias": torch.zeros(pooler),
            "audio_input_proj.weight": torch.zeros(pooler, audio_dim),
            "audio_input_proj.bias": torch.zeros(pooler),
            "video_modality_emb": torch.zeros(pooler),
            "audio_modality_emb": torch.zeros(pooler),
            "attention_pooler.query_tokens": torch.zeros(queries, pooler),
            "attention_pooler.cross_attn.in_proj_weight": torch.zeros(3 * pooler, pooler),
            "attention_pooler.cross_attn.in_proj_bias": torch.zeros(3 * pooler),
            "attention_pooler.cross_attn.out_proj.weight": torch.zeros(pooler, pooler),
            "attention_pooler.cross_attn.out_proj.bias": torch.zeros(pooler),
            "mlp_hidden.weight": torch.zeros(mlp, pooler * queries),
            "mlp_hidden.bias": torch.zeros(mlp),
            "mlp_out.weight": torch.zeros(1, mlp),
            "mlp_out.bias": torch.zeros(1),
        }
        return {f"{prefix}{k}": v for k, v in keys.items()}

    def test_dims_come_from_weight_shapes(self):
        c = detect_ltx_duration_head_config(self._sd())
        assert c == {
            "video_cross_attention_dim": 4096,
            "audio_cross_attention_dim": 2048,
            "pooler_hidden_dim": 256,
            "num_queries": 1,
            "num_pooler_heads": 4,
            "mlp_hidden_dim": 256,
        }

    def test_detects_through_the_duration_head_prefix(self):
        assert detect_ltx_duration_head_config(self._sd("duration_head.")) == \
            detect_ltx_duration_head_config(self._sd())

    def test_non_default_dims_are_read_back(self):
        c = detect_ltx_duration_head_config(self._sd(video_dim=64, audio_dim=32, pooler=8, queries=3, mlp=16))
        assert c["video_cross_attention_dim"] == 64
        assert c["audio_cross_attention_dim"] == 32
        assert c["pooler_hidden_dim"] == 8
        assert c["num_queries"] == 3
        assert c["mlp_hidden_dim"] == 16

    def test_head_count_falls_back_because_shapes_cannot_recover_it(self):
        """``to_q`` is square whatever the head count, so 4 (the trained
        value) is the only available answer -- same fallback diffusers'
        converter makes."""
        assert detect_ltx_duration_head_config(self._sd())["num_pooler_heads"] == 4

    def test_embedded_config_overrides_the_head_count(self):
        import json

        metadata = {"config": json.dumps({"num_pooler_heads": 8})}
        assert detect_ltx_duration_head_config(self._sd(), metadata)["num_pooler_heads"] == 8

    def test_empty_state_dict_returns_none(self):
        assert detect_ltx_duration_head_config({}) is None

    def test_a_vae_is_not_mistaken_for_a_duration_head(self):
        assert detect_ltx_duration_head_config(flux_ae_sd(latent=16)) is None

    def test_a_partial_head_returns_none(self):
        sd = self._sd()
        del sd["audio_input_proj.weight"]
        assert detect_ltx_duration_head_config(sd) is None


class TestDetectLtxLatentUpsamplerSpatialVsTemporal:
    """The temporal x2 upsampler and the spatial x1.5/x2.0 upsamplers share a
    checkpoint shape and a ``_class_name``; the ONLY thing separating them is
    the embedded config's ``temporal_upsample``/``spatial_upsample`` flags, so
    a mis-slotted file is silently wrong (see ``latent_upscaler/ltx``'s
    ``_resolve_upsampler``)."""

    def _metadata(self, **overrides) -> dict:
        import json

        config = {
            "_class_name": "LatentUpsampler",
            "in_channels": 128,
            "mid_channels": 512,
            "num_blocks_per_stage": 4,
            "dims": 3,
            "spatial_upsample": True,
            "temporal_upsample": False,
            "spatial_scale": 2.0,
            "rational_resampler": False,
        }
        config.update(overrides)
        return {"config": json.dumps(config)}

    def test_spatial_checkpoint_does_not_declare_temporal(self):
        c = detect_ltx_latent_upsampler_config(self._metadata())
        assert c["temporal_upsample"] is False
        assert c["spatial_upsample"] is True

    def test_temporal_checkpoint_declares_temporal_only(self):
        c = detect_ltx_latent_upsampler_config(
            self._metadata(spatial_upsample=False, temporal_upsample=True)
        )
        assert c["temporal_upsample"] is True
        assert c["spatial_upsample"] is False

    def test_a_duration_head_is_not_a_latent_upsampler(self):
        assert detect_ltx_latent_upsampler_config({"config": '{"_class_name": "DurationHead"}'}) is None
