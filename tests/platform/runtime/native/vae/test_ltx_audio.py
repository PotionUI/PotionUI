"""Tests for the LTX-2/2.3 audio VAE + vocoder (decode-only)."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.detect.vae_detect import detect_ltx_audio_vae_config, detect_ltx_vocoder_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.loader import load_ltx_audio_vae, load_ltx_vocoder
from src.platform.runtime.native.vae.ltx_audio import (
    LTXAudioAutoencoder,
    LTXVocoder,
    LTXVocoderAMP,
    _SnakeBeta,
    decode_audio_waveform,
)

_LTX2_AUDIO_PATH = Path("models/vae/LTX2_audio_vae_bf16.safetensors")
_LTX23_AUDIO_PATH = Path("models/vae/LTX23_audio_vae_bf16.safetensors")
_ALL_IN_ONE_PATH = Path("models/checkpoints/ltx-2-19b-dev-fp8.safetensors")

# Small (not real) ddconfig -- structurally faithful (ch_mult/num_res_blocks
# drive real module shape) but tiny enough to build/load/run instantly.
_TINY_DDCONFIG = {
    "double_z": True,
    # The vocoder's conv_pre input width is a fixed 64-mel-bin*2(stereo)=128
    # assumption independent of the audio VAE's own config (see
    # ``LTXVocoder.__init__``'s hardcoded ``in_channels = 128 if stereo else
    # 64``) -- mel_bins must stay 64 here so the composed
    # ``decode_audio_waveform`` test is shape-consistent, even in the "tiny"
    # fixture.
    "mel_bins": 64,
    "z_channels": 4,
    "resolution": 32,
    "in_channels": 2,
    "out_ch": 2,
    "ch": 8,
    "ch_mult": [1, 2],
    "num_res_blocks": 1,
    "attn_resolutions": [],
    "dropout": 0.0,
    "mid_block_add_attention": False,
    "norm_type": "pixel",
    "causality_axis": "height",
}

_TINY_AUDIO_VAE_CONFIG = {
    "model": {"params": {"ddconfig": _TINY_DDCONFIG, "sampling_rate": 16000}},
    "preprocessing": {"stft": {"hop_length": 160, "filter_length": 1024}},
}

_TINY_VOCODER_CONFIG = {
    "resblock_kernel_sizes": [3],
    "upsample_rates": [2, 2],
    "upsample_kernel_sizes": [4, 4],
    "resblock_dilation_sizes": [[1, 3]],
    "upsample_initial_channel": 16,
    "stereo": True,
    "resblock": "1",
}


def _randomize(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
        for name, b in module.named_buffers():
            if b is not None and b.is_floating_point():
                b.fill_(1.0 if "std-of-means" in name else 0.0)


class TestDetectLtxAudioVaeConfig:
    def test_returns_none_for_missing_metadata_config(self):
        assert detect_ltx_audio_vae_config({}) is None

    def test_returns_none_for_non_json_config(self):
        assert detect_ltx_audio_vae_config({"config": "not json"}) is None

    def test_returns_none_when_no_audio_vae_section(self):
        assert detect_ltx_audio_vae_config({"config": '{"vae": {}}'}) is None

    def test_returns_none_when_ddconfig_missing(self):
        assert detect_ltx_audio_vae_config({"config": '{"audio_vae": {"model": {"params": {}}}}'}) is None

    def test_extracts_audio_vae_config(self):
        import json

        metadata = {"config": json.dumps({"audio_vae": _TINY_AUDIO_VAE_CONFIG})}
        config = detect_ltx_audio_vae_config(metadata)
        assert config == _TINY_AUDIO_VAE_CONFIG


class TestDetectLtxVocoderConfig:
    def test_returns_none_for_missing_metadata_config(self):
        assert detect_ltx_vocoder_config({}) is None

    def test_returns_none_when_no_vocoder_section(self):
        assert detect_ltx_vocoder_config({"config": '{"vae": {}}'}) is None

    def test_extracts_flat_vocoder_config(self):
        import json

        metadata = {"config": json.dumps({"vocoder": _TINY_VOCODER_CONFIG})}
        assert detect_ltx_vocoder_config(metadata) == _TINY_VOCODER_CONFIG

    def test_extracts_nested_ltx23_shape_verbatim(self):
        """Detection is best-effort -- it returns whatever's embedded; rejecting
        the unsupported nested shape is ``LTXVocoder.from_config``'s job."""
        import json

        nested = {"vocoder": {"resblock": "AMP1"}, "bwe": {}}
        metadata = {"config": json.dumps({"vocoder": nested})}
        assert detect_ltx_vocoder_config(metadata) == nested


class TestLtxAudioAutoencoderTiny:
    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        _randomize(module)
        sd = module.state_dict()

        module2 = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        from src.platform.runtime.native.vae.loader import _VaeSpec
        from src.platform.runtime.native.base import load_into_module

        load_into_module(module2, sd, _VaeSpec(family="vae", variant="ltx_audio"))

    def test_post_load_is_safe_noop(self):
        module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        module.post_load()

    def test_encoder_forward_raises_unsupported(self):
        """Decode-only scope: the encoder submodule exists for key-parity only."""
        module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        _randomize(module)
        x = torch.randn(1, 2, 16, 8)
        with pytest.raises(NativeEngineUnsupportedError):
            module.encoder(x)

    def test_decode_roundtrip_shape_and_finite(self):
        module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        _randomize(module)
        z_channels = _TINY_DDCONFIG["z_channels"]
        latents = torch.randn(1, z_channels, 5, 16)  # (batch, z_channels, time, freq_latent)
        with torch.no_grad():
            mel = module.decode(latents)
        assert mel.shape[0] == 1
        assert mel.shape[1] == _TINY_DDCONFIG["out_ch"]
        assert mel.shape[3] == _TINY_DDCONFIG["mel_bins"]
        assert torch.isfinite(mel).all()

    def test_repeated_decode_calls_are_independent(self):
        module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        _randomize(module)
        z_channels = _TINY_DDCONFIG["z_channels"]
        latents = torch.randn(1, z_channels, 5, 16)
        with torch.no_grad():
            mel_a = module.decode(latents)
            mel_b = module.decode(latents)
        assert torch.allclose(mel_a, mel_b)


class TestLtxVocoderTiny:
    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = LTXVocoder.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)
        _randomize(module)
        sd = module.state_dict()

        module2 = LTXVocoder.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)
        from src.platform.runtime.native.vae.loader import _VaeSpec
        from src.platform.runtime.native.base import load_into_module

        load_into_module(module2, sd, _VaeSpec(family="vae", variant="ltx_vocoder"))

    def test_rejects_non_hifigan_resblock_type(self):
        bad_config = {**_TINY_VOCODER_CONFIG, "resblock": "AMP1"}
        with pytest.raises(NativeEngineUnsupportedError):
            LTXVocoder(config=bad_config, operations=disable_weight_init)

    def test_rejects_nested_ltx23_shape(self):
        nested = {"vocoder": {"resblock": "AMP1"}, "bwe": {}}
        with pytest.raises(NativeEngineUnsupportedError):
            LTXVocoder.from_config(nested, disable_weight_init)

    def test_forward_mono_and_stereo_finite(self):
        module = LTXVocoder.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)
        _randomize(module)
        x = torch.randn(1, 128, 6)  # 128 = 64 mel_bins * 2 (stereo channels concatenated)
        with torch.no_grad():
            out = module(x)
        assert out.shape[1] == 2  # stereo config -> 2 output channels
        assert torch.isfinite(out).all()


_TINY_AMP_MAIN_CONFIG = {
    "resblock": "AMP1",
    "activation": "snakebeta",
    "stereo": True,
    "resblock_kernel_sizes": [3],
    "resblock_dilation_sizes": [[1, 3, 5]],
    "upsample_rates": [2, 2],
    "upsample_kernel_sizes": [4, 4],
    "upsample_initial_channel": 16,
    "use_bias_at_final": False,
    "use_tanh_at_final": False,
}
# The bwe block carries the STFT/mel + sample-rate geometry the BWE stage needs.
# num_mels MUST be 64: the re-derived mel is stereo-stacked to 2*64=128 to feed
# bwe conv_pre (in_channels hardcoded 128 for stereo). Everything else is tiny
# but geometry-consistent: hop=2, win=n_fft=8 (frames = padded/hop), ratio=3, and
# bwe upsample product 3*2=6 = ratio*hop so the BWE residual and the resampled
# skip line up exactly (all (kernel-stride) even -> exact stride multiplication).
_TINY_AMP_BWE_CONFIG = {
    **_TINY_AMP_MAIN_CONFIG,
    "upsample_rates": [3, 2],
    "upsample_kernel_sizes": [5, 4],
    "upsample_initial_channel": 8,
    "n_fft": 8,
    "win_size": 8,
    "num_mels": 64,
    "hop_length": 2,
    "input_sampling_rate": 1,
    "output_sampling_rate": 3,
}
_TINY_AMP_NESTED_CONFIG = {"vocoder": _TINY_AMP_MAIN_CONFIG, "bwe": _TINY_AMP_BWE_CONFIG}


class TestLtxVocoderAMPTiny:
    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        _randomize(module)
        sd = module.state_dict()

        module2 = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        from src.platform.runtime.native.vae.loader import _VaeSpec
        from src.platform.runtime.native.base import load_into_module

        load_into_module(module2, sd, _VaeSpec(family="vae", variant="ltx_vocoder_amp"))

    def test_post_load_is_safe_noop(self):
        module = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        module.post_load()

    def test_rejects_flat_config(self):
        with pytest.raises(NativeEngineUnsupportedError):
            LTXVocoderAMP.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)

    def test_rejects_non_amp1_resblock(self):
        bad = {"vocoder": {**_TINY_AMP_MAIN_CONFIG, "resblock": "1"}, "bwe": _TINY_AMP_BWE_CONFIG}
        with pytest.raises(NativeEngineUnsupportedError):
            LTXVocoderAMP.from_config(bad, disable_weight_init)

    def test_missing_bwe_section_rejected(self):
        with pytest.raises(NativeEngineUnsupportedError):
            LTXVocoderAMP.from_config({"vocoder": _TINY_AMP_MAIN_CONFIG}, disable_weight_init)

    def test_forward_runs_full_bwe_chain(self):
        """``forward`` now composes main + BWE: the output is upsampled by the
        BWE ratio (output_sampling_rate/input_sampling_rate = 3x the stage-1
        waveform), finite, and clamped to [-1, 1] (the only clamp in the chain)."""
        module = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        _randomize(module)
        # Non-degenerate STFT bases so the mel re-analysis exercises sqrt/log
        # rather than _randomize's all-zero buffer fill.
        with torch.no_grad():
            module.mel_stft.stft_fn.forward_basis.normal_(std=0.1)
            module.mel_stft.mel_basis.normal_(std=0.1)

        x = torch.randn(1, 128, 6)
        with torch.no_grad():
            stage1 = module.vocoder(x)  # 16kHz-native stage-1 waveform
            out = module(x)             # full main + BWE chain
        ratio = _TINY_AMP_BWE_CONFIG["output_sampling_rate"] // _TINY_AMP_BWE_CONFIG["input_sampling_rate"]
        assert out.shape[1] == 2  # stereo config -> 2 output channels
        assert out.shape[-1] == ratio * stage1.shape[-1]
        assert torch.isfinite(out).all()
        assert out.min() >= -1.0 and out.max() <= 1.0

    def test_bwe_generator_and_mel_stft_and_resampler_exist(self):
        module = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        assert hasattr(module, "bwe_generator")
        assert hasattr(module, "mel_stft")
        assert hasattr(module, "resampler")
        assert isinstance(module.bwe_generator, torch.nn.Module)

    def test_resampler_filter_absent_from_state_dict(self):
        """The Hann skip resampler's filter is recomputed at load, NOT stored --
        no ``resampler.*`` keys exist in the real checkpoint, so the key-parity
        gate must not expect any (persistent=False on the buffer)."""
        module = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        assert not any(k.startswith("resampler.") for k in module.state_dict())
        # ...but the anti-alias filters inside the AMP blocks ARE persisted
        # (they exist as ``...upsample.filter`` keys in the checkpoint).
        assert any(k.endswith("upsample.filter") for k in module.state_dict())

    def test_reports_48k_output_sample_rate(self):
        module = LTXVocoderAMP.from_config(_TINY_AMP_NESTED_CONFIG, disable_weight_init)
        assert module.output_sample_rate == _TINY_AMP_BWE_CONFIG["output_sampling_rate"]


class TestSnakeBeta:
    def test_log_space_alpha_behaves_as_exp(self):
        """The stored alpha/beta are log-space and exp()'d in forward: alpha =
        log(2) must act as an angular frequency of 2, beta = 0 as amplitude 1."""
        import math

        act = _SnakeBeta(1)
        with torch.no_grad():
            act.alpha.fill_(math.log(2.0))
            act.beta.fill_(0.0)  # exp(0) = 1
        x = torch.linspace(-3.0, 3.0, 7).view(1, 1, -1)
        with torch.no_grad():
            out = act(x)
        expected = x + (1.0 / (1.0 + act._eps)) * torch.sin(x * 2.0) ** 2
        assert torch.allclose(out, expected, atol=1e-6)

    def test_zero_params_are_neutral_frequency_one(self):
        """Un-loaded params init to zeros -> exp(0)=1: x + sin^2(x)/(1+eps)."""
        act = _SnakeBeta(3)
        x = torch.randn(2, 3, 5)
        with torch.no_grad():
            out = act(x)
        expected = x + (1.0 / (1.0 + act._eps)) * torch.sin(x) ** 2
        assert torch.allclose(out, expected, atol=1e-6)


class TestDecodeAudioWaveform:
    def test_composes_autoencoder_and_vocoder(self):
        vae = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
        _randomize(vae)
        voc = LTXVocoder.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)
        _randomize(voc)

        z_channels = _TINY_DDCONFIG["z_channels"]
        latents = torch.randn(1, z_channels, 5, 16)
        with torch.no_grad():
            waveform, sample_rate = decode_audio_waveform(vae, voc, latents)
        assert sample_rate == 16000
        assert waveform.shape[0] == 1
        assert waveform.shape[1] == 2  # stereo
        assert torch.isfinite(waveform).all()


def test_load_ltx_audio_vae_accepts_preloaded_sd_and_metadata_without_reading_file():
    """The engine's ``_load_audio_vae`` reads+slices the all-in-one checkpoint
    once and hands the (still ``audio_vae.``-prefixed) result straight to this
    loader -- pass an obviously-nonexistent path to prove no second read."""
    import json

    module = LTXAudioAutoencoder.from_config(_TINY_AUDIO_VAE_CONFIG, disable_weight_init)
    _randomize(module)
    sd = {f"audio_vae.{k}": v for k, v in module.state_dict().items()}
    metadata = {"config": json.dumps({"audio_vae": _TINY_AUDIO_VAE_CONFIG})}

    loaded = load_ltx_audio_vae(
        Path("does/not/exist.safetensors"), disable_weight_init, device="cpu",
        sd=sd, metadata=metadata,
    )
    assert loaded.mel_bins == 64


def test_load_ltx_vocoder_accepts_preloaded_sd_and_metadata_without_reading_file():
    import json

    module = LTXVocoder.from_config(_TINY_VOCODER_CONFIG, disable_weight_init)
    _randomize(module)
    sd = {f"vocoder.{k}": v for k, v in module.state_dict().items()}
    metadata = {"config": json.dumps({"vocoder": _TINY_VOCODER_CONFIG})}

    loaded = load_ltx_vocoder(
        Path("does/not/exist.safetensors"), disable_weight_init, device="cpu",
        sd=sd, metadata=metadata,
    )
    assert isinstance(loaded, LTXVocoder)


@pytest.mark.requires_models
@pytest.mark.skipif(not _LTX2_AUDIO_PATH.exists(), reason="LTX2 audio VAE checkpoint not present locally")
class TestRealLtx2AudioVaeAndVocoder:
    def test_loads_and_decodes(self):
        vae = load_ltx_audio_vae(_LTX2_AUDIO_PATH, disable_weight_init, device="cpu")
        voc = load_ltx_vocoder(_LTX2_AUDIO_PATH, disable_weight_init, device="cpu")
        assert vae.mel_bins == 64
        assert vae.sampling_rate == 16000

        latents = torch.randn(1, vae.decoder.z_channels, 25, 16, dtype=torch.bfloat16)
        with torch.no_grad():
            waveform, sr = decode_audio_waveform(vae, voc, latents)
        assert sr == 16000
        assert waveform.shape[1] == 2
        assert torch.isfinite(waveform.float()).all()

    def test_short_and_longer_latents_are_independent(self):
        vae = load_ltx_audio_vae(_LTX2_AUDIO_PATH, disable_weight_init, device="cpu")
        voc = load_ltx_vocoder(_LTX2_AUDIO_PATH, disable_weight_init, device="cpu")

        short = torch.randn(1, vae.decoder.z_channels, 4, 16, dtype=torch.bfloat16)
        long = torch.randn(1, vae.decoder.z_channels, 12, 16, dtype=torch.bfloat16)
        with torch.no_grad():
            wave_short, _ = decode_audio_waveform(vae, voc, short)
            wave_long, _ = decode_audio_waveform(vae, voc, long)
        assert wave_long.shape[-1] > wave_short.shape[-1]
        assert torch.isfinite(wave_short.float()).all()
        assert torch.isfinite(wave_long.float()).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _LTX23_AUDIO_PATH.exists(), reason="LTX23 audio VAE checkpoint not present locally")
class TestRealLtx23AudioVae:
    def test_audio_vae_loads(self):
        """LTX23's audio VAE (encoder+decoder+per_channel_statistics) is the
        same architecture as LTX2 -- only the vocoder config shape drifts."""
        vae = load_ltx_audio_vae(_LTX23_AUDIO_PATH, disable_weight_init, device="cpu")
        assert vae.mel_bins == 64

        latents = torch.randn(1, vae.decoder.z_channels, 8, 16, dtype=torch.bfloat16)
        with torch.no_grad():
            mel = vae.decode(latents)
        assert torch.isfinite(mel.float()).all()

    def test_vocoder_loads_as_amp_and_decodes(self):
        """LTX23's vocoder is the nested AMP1/SnakeBeta shape -- ``load_ltx_vocoder``
        dispatches to ``LTXVocoderAMP``. ``forward`` runs the full main + BWE
        chain (``bwe_generator``/``mel_stft``/``resampler`` all wired)."""
        voc = load_ltx_vocoder(_LTX23_AUDIO_PATH, disable_weight_init, device="cpu")
        assert isinstance(voc, LTXVocoderAMP)

        mel = torch.randn(1, 2, 5, 64, dtype=torch.bfloat16)
        with torch.no_grad():
            waveform = voc(mel.transpose(2, 3))
        assert waveform.shape[1] == 2
        assert torch.isfinite(waveform.float()).all()

    def test_full_decode_audio_waveform(self):
        vae = load_ltx_audio_vae(_LTX23_AUDIO_PATH, disable_weight_init, device="cpu")
        voc = load_ltx_vocoder(_LTX23_AUDIO_PATH, disable_weight_init, device="cpu")
        latents = torch.randn(1, vae.decoder.z_channels, 8, 16, dtype=torch.bfloat16)
        with torch.no_grad():
            waveform, sr = decode_audio_waveform(vae, voc, latents)
        # BWE upsamples 16kHz -> 48kHz; decode_audio_waveform reports the
        # vocoder's own output rate.
        assert sr == 48000
        assert waveform.shape[1] == 2
        assert torch.isfinite(waveform.float()).all()


@pytest.mark.requires_models
@pytest.mark.skipif(not _ALL_IN_ONE_PATH.exists(), reason="all-in-one LTX-2 19B checkpoint not present locally")
class TestRealAllInOneCheckpointPrefixExtraction:
    def test_audio_vae_and_vocoder_prefix_extraction(self):
        """Both the standalone audio VAE files AND the all-in-one checkpoint
        use ``audio_vae.``/``vocoder.`` prefixes (verified via header dump --
        unlike the video VAE, there's no bare-vs-prefixed ambiguity here)."""
        vae = load_ltx_audio_vae(_ALL_IN_ONE_PATH, disable_weight_init, device="cpu")
        voc = load_ltx_vocoder(_ALL_IN_ONE_PATH, disable_weight_init, device="cpu")

        latents = torch.randn(1, vae.decoder.z_channels, 4, 16, dtype=torch.bfloat16)
        with torch.no_grad():
            waveform, sr = decode_audio_waveform(vae, voc, latents)
        assert sr == 16000
        assert torch.isfinite(waveform.float()).all()
