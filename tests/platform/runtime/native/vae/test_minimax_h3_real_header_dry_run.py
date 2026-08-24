"""Full end-to-end dry-run load tests for the MiniMax-H3 VAEs, driven by the
REAL Comfy-Org repack safetensors headers (``ai/minimax_h3/*_header.json`` --
no weights, only key/shape/dtype metadata fetched via range request).

**Why this file exists, distinct from the other unit tests in this
directory:** the tiny-config tests elsewhere build a module and load ITS OWN
``state_dict()`` back into a fresh instance of the SAME module -- a
tautology that cannot catch "the module's constructed key set doesn't
actually match the real checkpoint's key set" (exactly the class of bug a
sibling agent hit for a text encoder: a submodule the real checkpoint
doesn't have). This file instead builds a module whose key SET is asserted
equal to the real header's key set, then drives the REAL, unmodified
production path end to end: writes real (tiny-but-valid) safetensors files
to a temp dir with the checkpoint's own embedded metadata key, and calls
``NativeEngineLoader._load_vae`` / ``._load_audio_vae`` exactly as
production does -- detection -> loader dispatch -> ``_VaeSpec`` allowlist
gate -> ``post_load`` -> the loader's own NaN/meta-device sanity checks.

**Shrink strategy -- "exact key set, proportional shapes":** every FIELD
this file's config dicts set is either

  * shape-derived by the detector (``detect_minimax_h3_*_vae_config`` reads
    it off an actual tensor shape in the state dict) -- safe to shrink
    freely, the detector will recover whatever value was actually used, or
  * NOT shape-derived (the detector returns a hardcoded literal regardless
    of what's in the state dict, because there is only one released variant
    -- see both detectors' docstrings) -- MUST be pinned to the real
    production value here, or the detector rebuilds a module shaped
    differently from the state dict this file wrote, and `load_into_module`
    raises a shape-mismatch that has nothing to do with a real bug.

Concretely: video's `block_out_channels`/`norm_num_groups`/
`decoder_num_register_tokens`/`decoder_ffn_mult`/`decoder_num_attention_heads`
and audio's `decoder_dim`/`decoder_rates`/`decoder_kernel_sizes`/
`resblock_kernel_sizes`/`resblock_dilation_sizes`/`encoder_rates` are ALL
hardcoded-not-derived, so this file pins every one of them to the real
value (see ``ai/minimax_h3/*_header.json``'s embedded metadata / the
detector's own `_H3_*` constants). Only video's `decoder_attention_head_dim`
(64 -> 8, the detector recomputes it as `decoder_dim // 32` whenever
`decoder_dim` isn't the real-default 2048) and audio's `encoder_dim`/
`latent_dim`/`latent_channels` (all genuinely shape-derived) are actually
shrunk -- which is exactly where the expensive-to-materialize width lives
for each VAE (video: the 36-layer ViT decoder; audio: the DAC encoder trunk).

**What this CANNOT catch:** numeric correctness against the real weights
(everything here is randomly initialized -- shapes and wiring only, no
value comparison against a real decode); a bug specifically inside one of
the hardcoded-not-derived fields listed above (they're pinned to the
CURRENT real value by construction, so this file can't independently vary
them without producing a false failure -- see "Shrink strategy" above); and
any second real checkpoint variant with a genuinely different key layout
(this whole family, by design, targets the single released variant).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.detect.vae_detect import (
    detect_minimax_h3_audio_vae_config,
    detect_minimax_h3_video_vae_config,
)
from src.platform.runtime.native.engine import NativeEngineLoader
from src.platform.runtime.native.errors import NativeEngineLoadIntegrityError
from src.platform.runtime.native.vae.minimax_h3_audio import MiniMaxH3AudioVAE
from src.platform.runtime.native.vae.minimax_h3_video import MiniMaxH3VideoVAE
from vendor.gpl.comfyui.ops import QUANT_FP8_SCALED, _convrot_unrotate_weight, disable_weight_init

from .._quant_layouts import convrot_descriptor, descriptor_blob, reference_quantize_convrot

_HEADER_DIR = Path("ai/minimax_h3")

# See module docstring "Shrink strategy" -- every field here that is NOT
# shape-derived by the detector is pinned to its real production value.
_VIDEO_CONFIG = dict(
    latent_channels=24,
    block_out_channels=(128, 256, 256, 512, 512, 1024),
    layers_per_block=2,
    spatial_downsample_factors=(2, 2, 2, 2, 1, 1),
    temporal_downsample_factors=(1, 2, 2, 1, 1, 1),
    norm_num_groups=32,
    decoder_num_layers=36,
    decoder_num_attention_heads=32,
    decoder_attention_head_dim=8,  # real is 64 -- shape-derived, safe to shrink
    decoder_num_register_tokens=4,
    decoder_ffn_mult=4,
    clip_length=17,
    token_drop=3,
)

_AUDIO_CONFIG = dict(
    encoder_dim=8,          # real is 64 -- shape-derived, safe to shrink
    encoder_rates=(2, 4, 4, 5, 5),
    latent_dim=64,          # real is 2048 -- shape-derived, safe to shrink
    latent_channels=4,      # real is 32 -- shape-derived, safe to shrink
    num_attention_heads=2,  # doesn't affect any audio state-dict shape
    decoder_dim=1024,
    decoder_rates=(5, 5, 2, 2, 2, 2, 2),
    decoder_kernel_sizes=(9, 9, 4, 4, 4, 4, 4),
    resblock_kernel_sizes=(3, 7, 11),
    resblock_dilation_sizes=((1, 3, 5), (1, 3, 5), (1, 3, 5)),
    sample_rate=32000,
)


# Kijai's quantised repack of the same VAE. Only the ViT decoder's four
# Linears per transformer block carry int8 codes; every Conv3d in the
# causal-3D encoder stays float, so no scaled-conv path is involved. The
# group size is the one its own `comfy_quant` descriptors state.
_INT8_HEADER = "video_vae_int8_convrot_header.json"
_CONVROT_GROUPSIZE = 256


def _load_real_header(name: str) -> dict:
    path = _HEADER_DIR / name
    if not path.exists():
        pytest.skip(f"{path} not present (fetched once via range request; not part of the repo checkout)")
    with path.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


def _randomize(sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    with torch.no_grad():
        for v in sd.values():
            if v.is_floating_point():
                v.normal_(std=0.02)
    # Detach from the live module and make contiguous -- save_file requires
    # both (a state_dict's views/strided buffers can otherwise alias).
    return {k: v.contiguous().clone() for k, v in sd.items()}


def _build_video_state_dict() -> dict[str, torch.Tensor]:
    module = MiniMaxH3VideoVAE.from_config(_VIDEO_CONFIG, disable_weight_init)
    return _randomize(module.state_dict())


def _build_audio_state_dict() -> dict[str, torch.Tensor]:
    module = MiniMaxH3AudioVAE.from_config(_AUDIO_CONFIG, disable_weight_init)
    return _randomize(module.state_dict())


def _quantised_weight_keys(header: dict) -> set[str]:
    return {k for k, v in header.items() if v["dtype"] == "I8"}


def _quantise_video_state_dict(sd: dict[str, torch.Tensor], header: dict) -> dict[str, torch.Tensor]:
    """Re-lay a float video state dict as the int8 header describes it.

    Which tensors get quantised is read off the real header rather than
    hardcoded here, so the layout under test tracks the published file.
    """
    quantised = _quantised_weight_keys(header)
    assert quantised <= set(sd), sorted(quantised - set(sd))[:5]
    out: dict[str, torch.Tensor] = {}
    for key, tensor in sd.items():
        if key not in quantised:
            out[key] = tensor.float()
            continue
        codes, scale = reference_quantize_convrot(tensor.float(), _CONVROT_GROUPSIZE)
        base = key[: -len(".weight")]
        out[key] = codes
        out[base + ".weight_scale"] = scale
        out[base + ".comfy_quant"] = descriptor_blob(convrot_descriptor(_CONVROT_GROUPSIZE))
    return out


def _rel_err(got: torch.Tensor, want: torch.Tensor) -> float:
    return ((got - want).norm() / want.norm()).item()


class TestVideoStateDictMatchesRealHeaderKeySet:
    def test_exact_key_set(self):
        """Catches: a registered buffer/param the real checkpoint lacks, or
        one the real checkpoint has that this module doesn't register (e.g.
        forgetting `mask_token`, `latents_mean`/`latents_std`, or a Kaiser
        filter buffer -- the exact class of bug flagged upstream)."""
        header = _load_real_header("video_vae_header.json")
        sd = _build_video_state_dict()
        assert set(sd.keys()) == set(header.keys())

    def test_shape_ranks_match(self):
        """A weaker but independent check beyond key presence: every
        tensor's NUMBER of dimensions must match the real header (catches a
        conv accidentally built 1D/2D/3D short, etc.), even though the
        absolute sizes differ (shrunk here, real in the header)."""
        header = _load_real_header("video_vae_header.json")
        sd = _build_video_state_dict()
        for key, tensor in sd.items():
            assert tensor.ndim == len(header[key]["shape"]), key


class TestQuantisedVideoRepackHeaderStructure:
    """Pins what the published int8 repack actually is, so the dry-run below
    is testing the real layout and not an assumed one."""

    def test_key_set_is_the_fp16_key_set_plus_two_sidecars_per_quantised_weight(self):
        fp16 = _load_real_header("video_vae_header.json")
        int8 = _load_real_header(_INT8_HEADER)
        quantised = _quantised_weight_keys(int8)
        expected = set(fp16) | {k[: -len(".weight")] + s for k in quantised for s in (".weight_scale", ".comfy_quant")}
        assert set(int8) == expected

    def test_only_vit_decoder_linears_are_quantised(self):
        """No Conv3d carries int8 codes -- which is why the encoder needs no
        scaled-convolution path at all."""
        int8 = _load_real_header(_INT8_HEADER)
        quantised = _quantised_weight_keys(int8)
        assert quantised
        for key in quantised:
            assert key.startswith("decoder.transformer_blocks."), key
            assert key.rsplit(".", 2)[0].endswith(("attn", "ff")), key
            assert len(int8[key]["shape"]) == 2, key

    def test_every_quantised_weight_has_a_per_output_channel_scale_and_descriptor(self):
        int8 = _load_real_header(_INT8_HEADER)
        for key in _quantised_weight_keys(int8):
            base = key[: -len(".weight")]
            scale = int8[base + ".weight_scale"]
            assert scale["dtype"] == "F32"
            assert scale["shape"] == [int8[key]["shape"][0], 1]
            assert int8[base + ".comfy_quant"]["dtype"] == "U8"

    def test_unquantised_tensors_are_float(self):
        int8 = _load_real_header(_INT8_HEADER)
        quantised = _quantised_weight_keys(int8)
        for key, entry in int8.items():
            if key not in quantised and not key.endswith(".comfy_quant"):
                assert entry["dtype"] == "F32", key

    def test_in_features_are_divisible_by_the_convrot_group_size(self):
        """The un-rotation reshapes the contraction dimension into whole
        groups; a non-multiple would silently misgroup it."""
        int8 = _load_real_header(_INT8_HEADER)
        for key in _quantised_weight_keys(int8):
            assert int8[key]["shape"][1] % _CONVROT_GROUPSIZE == 0, key


class TestAudioStateDictMatchesRealHeaderKeySet:
    def test_exact_key_set(self):
        header = _load_real_header("audio_vae_header.json")
        sd = _build_audio_state_dict()
        assert set(sd.keys()) == set(header.keys())

    def test_shape_ranks_match(self):
        header = _load_real_header("audio_vae_header.json")
        sd = _build_audio_state_dict()
        for key, tensor in sd.items():
            assert tensor.ndim == len(header[key]["shape"]), key


class TestVideoDetectionOnRealShapedStateDict:
    def test_detects_config_matching_the_built_module(self):
        _load_real_header("video_vae_header.json")  # skip if headers absent, for symmetry with the other classes
        sd = _build_video_state_dict()
        config = detect_minimax_h3_video_vae_config(sd)
        assert config is not None
        assert config["latent_channels"] == _VIDEO_CONFIG["latent_channels"]
        assert config["decoder_num_layers"] == _VIDEO_CONFIG["decoder_num_layers"]
        assert config["decoder_num_attention_heads"] == _VIDEO_CONFIG["decoder_num_attention_heads"]
        assert config["decoder_attention_head_dim"] == _VIDEO_CONFIG["decoder_attention_head_dim"]

    def test_reads_clip_length_and_token_drop_from_embedded_metadata(self):
        sd = _build_video_state_dict()
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 17, "vae_token_drop": 3})}
        config = detect_minimax_h3_video_vae_config(sd, metadata)
        assert config["clip_length"] == 17
        assert config["token_drop"] == 3


class TestAudioDetectionOnRealShapedStateDict:
    def test_detects_config_matching_the_built_module(self):
        _load_real_header("audio_vae_header.json")
        sd = _build_audio_state_dict()
        config = detect_minimax_h3_audio_vae_config(sd)
        assert config is not None
        assert config["encoder_dim"] == _AUDIO_CONFIG["encoder_dim"]
        assert config["latent_dim"] == _AUDIO_CONFIG["latent_dim"]
        assert config["latent_channels"] == _AUDIO_CONFIG["latent_channels"]


class TestFullEngineDispatchDryRun:
    """Writes real (tiny) safetensors files with the checkpoint's own
    embedded metadata key and drives the UNMODIFIED production entry points
    (`NativeEngineLoader._load_vae` / `._load_audio_vae`) end to end:
    file read -> `_load_vae_module` dispatch -> detection -> this family's
    loader -> `_VaeSpec` allowlist gate -> `load_into_module`'s missing/
    unexpected-key assertions -> `post_load` -> NaN/meta-device sanity."""

    def test_video_loads_through_the_real_engine_and_decodes(self):
        _load_real_header("video_vae_header.json")
        sd = _build_video_state_dict()
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 17, "vae_token_drop": 3})}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_video_vae_fp16.safetensors"
            save_file(sd, str(path), metadata=metadata)

            loader = NativeEngineLoader(device="cpu")
            native_model = loader._load_vae(path)

        assert isinstance(native_model.module, MiniMaxH3VideoVAE)
        assert native_model.module.clip_length == 17
        assert native_model.module.token_drop == 3

        module = native_model.module
        module.use_tiling = False
        # `_decode`'s chunk math needs num_latent_frames > tokens_chunk_size
        # - token_drop (real: 5 - 3 = 2, so >= 3) or `num_chunks` computes to
        # 0 and `torch.cat([])` raises -- see minimax_h3_video.py's `_decode`
        # docstring and the chunk-math tests in test_minimax_h3_video.py.
        latent = torch.randn(1, _VIDEO_CONFIG["latent_channels"], 3, 2, 2)
        with torch.no_grad():
            pixels = module.decode(latent)
        assert torch.isfinite(pixels).all()

    def test_audio_loads_through_the_real_engine_and_decodes(self):
        _load_real_header("audio_vae_header.json")
        sd = _build_audio_state_dict()
        metadata = {"minimax_h3_audio_vae": json.dumps({"sample_rate": 32000})}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_audio_vae_fp32.safetensors"
            save_file(sd, str(path), metadata=metadata)

            loader = NativeEngineLoader(device="cpu")
            native_model = loader._load_audio_vae(path)

        assert isinstance(native_model.module, MiniMaxH3AudioVAE)
        assert native_model.module.sample_rate == 32000

        module = native_model.module
        latent = torch.randn(1, _AUDIO_CONFIG["latent_channels"], 5)
        with torch.no_grad():
            waveform = module.decode(latent)
        assert torch.isfinite(waveform).all()

    def test_quantised_video_repack_loads_and_decodes(self):
        """The int8_tensorwise/ConvRot video VAE repack, laid out exactly as
        the real header describes it, through the same unmodified entry
        point -- covering the part the fp16 dry-run above cannot reach: the
        `_VaeSpec` allowlist gate meeting 288 sidecar keys, and the ops
        selection landing on a namespace that can dequantise int8 codes."""
        header = _load_real_header(_INT8_HEADER)
        sd = _quantise_video_state_dict(_build_video_state_dict(), header)
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 17, "vae_token_drop": 3})}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_video_vae_int8_convrot.safetensors"
            save_file(sd, str(path), metadata=metadata)
            native_model = NativeEngineLoader(device="cpu")._load_vae(path)

        assert isinstance(native_model.module, MiniMaxH3VideoVAE)
        assert native_model.quant_format == QUANT_FP8_SCALED

        block = native_model.module.decoder.transformer_blocks[0]
        for linear in (block.attn.to_qkv, block.attn.to_out, block.ff.w1, block.ff.w2):
            assert linear.weight.dtype == torch.int8
            assert linear.weight_scale is not None
            assert linear.convrot_hadamard is not None
            assert linear.convrot_groupsize == _CONVROT_GROUPSIZE

        module = native_model.module
        module.use_tiling = False
        latent = torch.randn(1, _VIDEO_CONFIG["latent_channels"], 3, 2, 2)
        with torch.no_grad():
            pixels = module.decode(latent)
        assert torch.isfinite(pixels).all()

    def test_quantised_repack_recovers_the_weights_it_was_quantised_from(self):
        """int8 codes + per-output-channel scale + the ConvRot un-rotation
        must reconstruct the float weight the quantiser started from. Without
        the un-rotation the same codes dequantise to something else entirely
        (asserted below), so this is what proves the rotation is applied and
        applied in the right direction."""
        header = _load_real_header(_INT8_HEADER)
        float_sd = _build_video_state_dict()
        sd = _quantise_video_state_dict(float_sd, header)
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 17, "vae_token_drop": 3})}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_video_vae_int8_convrot.safetensors"
            save_file(sd, str(path), metadata=metadata)
            module = NativeEngineLoader(device="cpu")._load_vae(path).module

        linear = module.decoder.transformer_blocks[0].attn.to_qkv
        original = float_sd["decoder.transformer_blocks.0.attn.to_qkv.weight"].float()
        rotated = linear.weight.float() * linear.weight_scale.float()
        recovered = _convrot_unrotate_weight(rotated, linear.convrot_hadamard.float(), linear.convrot_groupsize)

        assert _rel_err(recovered, original) < 0.05
        # Bite-check: skipping the un-rotation is not a near-miss.
        assert _rel_err(rotated, original) > 0.5

    def test_a_non_sidecar_unexpected_key_still_fails_the_load(self):
        """The sidecars survive the allowlist gate because the ops layer
        consumes them, not because the gate was widened -- so an ordinary
        junk key must still abort the load."""
        header = _load_real_header(_INT8_HEADER)
        sd = _quantise_video_state_dict(_build_video_state_dict(), header)
        sd["decoder.transformer_blocks.0.attn.to_qkv.bogus"] = torch.zeros(4)
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 17, "vae_token_drop": 3})}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_video_vae_int8_convrot.safetensors"
            save_file(sd, str(path), metadata=metadata)
            with pytest.raises(NativeEngineLoadIntegrityError) as excinfo:
                NativeEngineLoader(device="cpu")._load_vae(path)

        # The junk key is what it objected to -- not a sidecar, and not some
        # unrelated shape mismatch that would make this pass for free.
        message = str(excinfo.value)
        assert "decoder.transformer_blocks.0.attn.to_qkv.bogus" in message
        assert "weight_scale" not in message
        assert "comfy_quant" not in message

    def test_video_and_audio_dtype_assumptions_are_respected(self):
        """Video's real repack is fp16, audio's is fp32 -- confirm the
        loaded module's weights land at the dtype the file actually stored
        (not silently upcast/downcast by the ops selection), since a
        storage-dtype mixup is exactly the kind of thing a shape-only key-
        parity check would miss."""
        _load_real_header("video_vae_header.json")
        video_sd = {k: v.to(torch.float16) for k, v in _build_video_state_dict().items()}
        metadata = {"minimax_h3_video_vae": json.dumps({"vae_clip_length": 17, "vae_token_drop": 3})}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_video_vae_fp16.safetensors"
            save_file(video_sd, str(path), metadata=metadata)
            video_model = NativeEngineLoader(device="cpu")._load_vae(path)
        assert video_model.module.encoder.conv_in.weight.dtype == torch.float16

        _load_real_header("audio_vae_header.json")
        audio_sd = {k: v.to(torch.float32) for k, v in _build_audio_state_dict().items()}
        audio_metadata = {"minimax_h3_audio_vae": json.dumps({"sample_rate": 32000})}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "minimax_h3_audio_vae_fp32.safetensors"
            save_file(audio_sd, str(path), metadata=audio_metadata)
            audio_model = NativeEngineLoader(device="cpu")._load_audio_vae(path)
        assert audio_model.module.dec_in_proj.weight.dtype == torch.float32
