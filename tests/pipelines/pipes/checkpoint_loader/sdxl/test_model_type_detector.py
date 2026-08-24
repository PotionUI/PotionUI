"""
Tests for SDXLModelTypeDetector.

Tests auto-detection of SDXL model characteristics (anime vs realistic,
prediction type, ZTSNR usage) from scheduler config and alphas_cumprod values.
"""

import pytest
import torch
from unittest.mock import Mock, MagicMock, PropertyMock
from src.pipelines.pipes.checkpoint_loader.sdxl.model_type_detector import (
    SDXLModelTypeDetector,
    SDXLModelTypeInfo,
)


def _make_scheduler_config(**kwargs):
    """Create a mock scheduler config with given attributes."""
    config = Mock()
    defaults = {
        'prediction_type': 'epsilon',
        'beta_start': 0.00085,
        'beta_end': 0.012,
        'rescale_betas_zero_snr': False,
    }
    defaults.update(kwargs)
    for key, value in defaults.items():
        setattr(config, key, value)
    return config


def _make_pipe(scheduler_config=None, alphas_cumprod=None, has_scheduler=True):
    """Create a mock pipeline with scheduler and optional alphas_cumprod."""
    pipe = Mock()
    if not has_scheduler:
        del pipe.scheduler
        return pipe

    scheduler = Mock()
    scheduler.config = scheduler_config

    if alphas_cumprod is not None:
        scheduler.alphas_cumprod = alphas_cumprod
    else:
        del scheduler.alphas_cumprod

    pipe.scheduler = scheduler
    return pipe


class TestSDXLModelTypeInfoDefaults:
    """Test SDXLModelTypeInfo dataclass defaults."""

    def test_default_values(self):
        info = SDXLModelTypeInfo()
        assert info.prediction_type == "epsilon"
        assert info.uses_ztsnr is False
        assert info.model_style == "realistic"
        assert info.recommended_adm_enabled is True
        assert info.recommended_adm_positive_scale == 1.5
        assert info.recommended_adm_negative_scale == 0.8
        assert info.recommended_adm_scaler_end == 0.3
        assert info.recommended_guidance_rescale == 0.0
        assert info.detection_details == {}


class TestRealisticModelDetection:
    """Test detection of realistic SDXL models."""

    def test_standard_realistic_model(self):
        """Realistic model: epsilon prediction, standard beta_end, no ZTSNR."""
        config = _make_scheduler_config(
            prediction_type='epsilon',
            beta_end=0.012,
            rescale_betas_zero_snr=False,
        )
        # Standard SDXL terminal alpha ~ 0.0292
        alphas = torch.linspace(0.9999, 0.0292, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.model_style == "realistic"
        assert info.prediction_type == "epsilon"
        assert info.uses_ztsnr is False
        assert info.recommended_adm_enabled is True
        assert info.recommended_adm_positive_scale == 1.5
        assert info.recommended_adm_negative_scale == 0.8
        assert info.recommended_adm_scaler_end == 0.3

    def test_realistic_with_high_terminal_alpha(self):
        """Realistic model with terminal alpha well above ZTSNR threshold."""
        config = _make_scheduler_config(prediction_type='epsilon', beta_end=0.012)
        alphas = torch.linspace(0.9999, 0.05, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.model_style == "realistic"
        assert info.uses_ztsnr is False


class TestAnimeModelDetection:
    """Test detection of anime SDXL models."""

    def test_ztsnr_anime_model(self):
        """Anime model with ZTSNR (terminal alpha near 0)."""
        config = _make_scheduler_config(
            prediction_type='epsilon',
            beta_end=0.012,
            rescale_betas_zero_snr=False,
        )
        # ZTSNR: terminal alpha very close to 0
        alphas = torch.linspace(0.9999, 0.0001, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.model_style == "anime"
        assert info.uses_ztsnr is True
        assert info.recommended_adm_enabled is False
        assert info.recommended_adm_positive_scale == 1.0
        assert info.recommended_adm_negative_scale == 1.0
        assert info.recommended_adm_scaler_end == 0.0

    def test_ztsnr_with_high_beta_end(self):
        """Anime model with both ZTSNR and high beta_end."""
        config = _make_scheduler_config(
            prediction_type='epsilon',
            beta_end=0.02,
            rescale_betas_zero_snr=False,
        )
        alphas = torch.linspace(0.9999, 0.0001, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.model_style == "anime"
        assert info.uses_ztsnr is True
        assert info.detection_details.get('high_beta_end') is True
        assert info.detection_details['anime_signals'] >= 3

    def test_rescale_betas_flag_triggers_anime(self):
        """Explicit rescale_betas_zero_snr flag triggers anime detection."""
        config = _make_scheduler_config(
            prediction_type='epsilon',
            beta_end=0.012,
            rescale_betas_zero_snr=True,
        )
        # Even with high terminal alpha, the flag overrides
        alphas = torch.linspace(0.9999, 0.05, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.uses_ztsnr is True
        assert info.model_style == "anime"
        assert '+rescale_flag' in info.detection_details.get('ztsnr_source', '')


class TestVPredictionDetection:
    """Test v_prediction detection."""

    def test_v_prediction_realistic(self):
        """v_prediction model without ZTSNR stays realistic (only 1 signal)."""
        config = _make_scheduler_config(
            prediction_type='v_prediction',
            beta_end=0.012,
            rescale_betas_zero_snr=False,
        )
        alphas = torch.linspace(0.9999, 0.05, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.prediction_type == "v_prediction"
        assert info.model_style == "realistic"  # Only 1 signal, needs >= 2

    def test_v_prediction_with_high_beta_end_becomes_anime(self):
        """v_prediction + high beta_end = 2 signals = anime."""
        config = _make_scheduler_config(
            prediction_type='v_prediction',
            beta_end=0.02,
            rescale_betas_zero_snr=False,
        )
        alphas = torch.linspace(0.9999, 0.05, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.prediction_type == "v_prediction"
        assert info.model_style == "anime"
        assert info.detection_details['anime_signals'] >= 2

    def test_v_prediction_with_ztsnr(self):
        """v_prediction + ZTSNR = clearly anime."""
        config = _make_scheduler_config(
            prediction_type='v_prediction',
            beta_end=0.012,
            rescale_betas_zero_snr=False,
        )
        alphas = torch.linspace(0.9999, 0.0001, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.prediction_type == "v_prediction"
        assert info.model_style == "anime"
        assert info.uses_ztsnr is True


class TestZTSNRDetection:
    """Test ZTSNR detection from different sources."""

    def test_ztsnr_from_terminal_alpha(self):
        """ZTSNR detected from very low terminal alpha."""
        config = _make_scheduler_config(rescale_betas_zero_snr=False)
        alphas = torch.linspace(0.9999, 0.0005, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.uses_ztsnr is True
        assert 'terminal_alpha' in info.detection_details.get('ztsnr_source', '')

    def test_ztsnr_from_rescale_flag(self):
        """ZTSNR detected from explicit rescale_betas_zero_snr flag."""
        config = _make_scheduler_config(rescale_betas_zero_snr=True)
        # Terminal alpha above threshold, but flag is set
        alphas = torch.linspace(0.9999, 0.05, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.uses_ztsnr is True
        assert '+rescale_flag' in info.detection_details.get('ztsnr_source', '')

    def test_ztsnr_from_both_sources(self):
        """ZTSNR detected from both terminal alpha and rescale flag."""
        config = _make_scheduler_config(rescale_betas_zero_snr=True)
        alphas = torch.linspace(0.9999, 0.0001, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.uses_ztsnr is True
        ztsnr_source = info.detection_details.get('ztsnr_source', '')
        assert 'terminal_alpha' in ztsnr_source
        assert '+rescale_flag' in ztsnr_source

    def test_no_ztsnr_for_standard_model(self):
        """No ZTSNR for standard SDXL model."""
        config = _make_scheduler_config(rescale_betas_zero_snr=False)
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.uses_ztsnr is False

    def test_boundary_alpha_at_threshold(self):
        """Terminal alpha exactly at the 0.001 threshold is not ZTSNR."""
        config = _make_scheduler_config(rescale_betas_zero_snr=False)
        alphas = torch.linspace(0.9999, 0.001, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        # 0.001 is NOT < 0.001, so no ZTSNR
        assert info.uses_ztsnr is False


class TestRecommendedDefaults:
    """Test recommended defaults for anime vs realistic models."""

    def test_realistic_defaults(self):
        """Realistic model gets Fooocus ADM defaults."""
        config = _make_scheduler_config()
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.recommended_adm_enabled is True
        assert info.recommended_adm_positive_scale == 1.5
        assert info.recommended_adm_negative_scale == 0.8
        assert info.recommended_adm_scaler_end == 0.3
        assert info.recommended_guidance_rescale == 0.0

    def test_anime_defaults(self):
        """Anime ZTSNR model gets disabled ADM and no guidance_rescale (matches ComfyUI)."""
        config = _make_scheduler_config()
        alphas = torch.linspace(0.9999, 0.0001, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.recommended_adm_enabled is False
        assert info.recommended_adm_positive_scale == 1.0
        assert info.recommended_adm_negative_scale == 1.0
        assert info.recommended_adm_scaler_end == 0.0
        # ComfyUI doesn't use guidance_rescale, so keep at 0
        assert info.recommended_guidance_rescale == 0.0


class TestGracefulDegradation:
    """Test graceful handling of missing or incomplete data."""

    def test_no_scheduler(self):
        """Pipeline without scheduler returns defaults."""
        pipe = _make_pipe(has_scheduler=False)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.prediction_type == "epsilon"
        assert info.uses_ztsnr is False
        assert info.model_style == "realistic"
        assert info.recommended_adm_enabled is True

    def test_scheduler_without_config(self):
        """Scheduler without config attribute."""
        pipe = Mock()
        pipe.scheduler = Mock()
        pipe.scheduler.config = None
        del pipe.scheduler.alphas_cumprod

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.prediction_type == "epsilon"
        assert info.uses_ztsnr is False
        assert info.model_style == "realistic"

    def test_scheduler_without_alphas_cumprod(self):
        """Scheduler with config but no alphas_cumprod."""
        config = _make_scheduler_config(prediction_type='v_prediction')
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=None)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.prediction_type == "v_prediction"
        assert info.uses_ztsnr is False  # Can't detect without alphas

    def test_config_missing_prediction_type_attribute(self):
        """Config object that lacks prediction_type uses default."""
        config = Mock(spec=[])  # Empty spec, no attributes
        pipe = Mock()
        pipe.scheduler = Mock()
        pipe.scheduler.config = config
        del pipe.scheduler.alphas_cumprod

        info = SDXLModelTypeDetector.detect(pipe)

        # getattr with default 'epsilon' should be used
        assert info.prediction_type == "epsilon"


class TestDetectionDetails:
    """Test that detection_details dict is properly populated."""

    def test_details_include_prediction_type(self):
        config = _make_scheduler_config(prediction_type='epsilon')
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.detection_details['prediction_type'] == 'epsilon'

    def test_details_include_terminal_alpha(self):
        config = _make_scheduler_config()
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert 'terminal_alpha' in info.detection_details
        assert abs(info.detection_details['terminal_alpha'] - 0.03) < 0.001

    def test_details_include_beta_end(self):
        config = _make_scheduler_config(beta_end=0.018)
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.detection_details['beta_end'] == 0.018
        assert info.detection_details.get('high_beta_end') is True

    def test_details_include_rescale_flag(self):
        config = _make_scheduler_config(rescale_betas_zero_snr=True)
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert info.detection_details['rescale_betas_zero_snr'] is True

    def test_details_include_anime_signals_count(self):
        config = _make_scheduler_config()
        alphas = torch.linspace(0.9999, 0.03, 1000)
        pipe = _make_pipe(scheduler_config=config, alphas_cumprod=alphas)

        info = SDXLModelTypeDetector.detect(pipe)

        assert 'anime_signals' in info.detection_details
        assert isinstance(info.detection_details['anime_signals'], int)
