"""
SDXL Model Type Detector

Auto-detects SDXL model characteristics (anime vs realistic, prediction type, ZTSNR usage)
from scheduler config and alphas_cumprod values. Enables smart defaults without user config.

Inspired by ComfyUI's model detection approach.
"""

import torch
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from src.platform.observability.logger import logger


@dataclass
class SDXLModelTypeInfo:
    """Detected model characteristics and recommended defaults."""
    prediction_type: str = "epsilon"  # "epsilon" or "v_prediction"
    uses_ztsnr: bool = False
    model_style: str = "realistic"  # "anime" or "realistic"

    # Recommended ADM guidance defaults based on model type
    recommended_adm_enabled: bool = True
    recommended_adm_positive_scale: float = 1.5
    recommended_adm_negative_scale: float = 0.8
    recommended_adm_scaler_end: float = 0.3

    # Recommended guidance rescale
    recommended_guidance_rescale: float = 0.0

    # Detection confidence info
    detection_details: Dict[str, Any] = field(default_factory=dict)


class SDXLModelTypeDetector:
    """Detects SDXL model type from pipeline configuration.

    Detection heuristics:
    - ZTSNR models (terminal alphas_cumprod near 0) are almost always anime
    - High beta_end (> 0.015) hints anime training
    - rescale_betas_zero_snr flag is explicit ZTSNR indicator
    - v_prediction models may need different guidance handling
    """

    @staticmethod
    def detect(pipe) -> SDXLModelTypeInfo:
        """Detect model type from a loaded pipeline.

        Args:
            pipe: Loaded StableDiffusionXL pipeline with scheduler

        Returns:
            SDXLModelTypeInfo with detected characteristics and recommended defaults
        """
        info = SDXLModelTypeInfo()
        details = {}

        if not hasattr(pipe, 'scheduler'):
            logger.warning("[MODEL_DETECTOR] No scheduler found, using defaults")
            return info

        scheduler = pipe.scheduler
        config = getattr(scheduler, 'config', None)

        # 1. Detect prediction type
        if config:
            prediction_type = getattr(config, 'prediction_type', 'epsilon')
            info.prediction_type = prediction_type
            details['prediction_type'] = prediction_type

        # 2. Detect ZTSNR from alphas_cumprod
        if hasattr(scheduler, 'alphas_cumprod'):
            alphas = scheduler.alphas_cumprod
            terminal_alpha = alphas[-1].item()
            details['terminal_alpha'] = terminal_alpha

            # If terminal alpha is very small, model uses ZTSNR
            if terminal_alpha < 0.001:
                info.uses_ztsnr = True
                details['ztsnr_source'] = 'terminal_alpha'

        # 3. Check explicit rescale_betas_zero_snr flag
        if config:
            rescale_flag = getattr(config, 'rescale_betas_zero_snr', False)
            if rescale_flag:
                info.uses_ztsnr = True
                details['ztsnr_source'] = details.get('ztsnr_source', '') + '+rescale_flag'
            details['rescale_betas_zero_snr'] = rescale_flag

        # 4. Check beta_end for anime heuristic
        if config:
            beta_end = getattr(config, 'beta_end', 0.012)
            details['beta_end'] = beta_end

            # High beta_end is common in anime models
            if beta_end > 0.015:
                details['high_beta_end'] = True

        # 5. Determine model style
        anime_signals = 0
        if info.uses_ztsnr:
            anime_signals += 2  # Strong signal
        if config and getattr(config, 'beta_end', 0.012) > 0.015:
            anime_signals += 1
        if info.prediction_type == 'v_prediction':
            anime_signals += 1  # Some anime models use v-prediction

        if anime_signals >= 2:
            info.model_style = "anime"
        else:
            info.model_style = "realistic"

        details['anime_signals'] = anime_signals

        # 6. Set recommended defaults based on detected style
        if info.model_style == "anime":
            # Anime models: disable ADM guidance to prevent oversaturation
            info.recommended_adm_enabled = False
            info.recommended_adm_positive_scale = 1.0
            info.recommended_adm_negative_scale = 1.0
            info.recommended_adm_scaler_end = 0.0
            # ComfyUI produces correct results without guidance_rescale, so keep at 0.
            info.recommended_guidance_rescale = 0.0
        else:
            # Realistic models: keep Fooocus defaults
            info.recommended_adm_enabled = True
            info.recommended_adm_positive_scale = 1.5
            info.recommended_adm_negative_scale = 0.8
            info.recommended_adm_scaler_end = 0.3
            info.recommended_guidance_rescale = 0.0

        info.detection_details = details

        logger.info(
            f"[MODEL_DETECTOR] Detected: style={info.model_style}, "
            f"prediction={info.prediction_type}, ztsnr={info.uses_ztsnr}, "
            f"adm_enabled={info.recommended_adm_enabled}"
        )

        return info
