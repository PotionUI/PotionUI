"""
Maya Model Wrapper for text-to-speech generation.

This module provides a wrapper for the Maya TTS model:
- Maya1: 3B parameter Llama-style transformer for emotionally expressive TTS
- SNAC: Streaming Neural Audio Codec for 24kHz audio output

The model generates natural-sounding speech from text with voice descriptions
and emotion tags for expressive synthesis.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional

import torch

from src.pipelines.models import BaseModel
from src.platform.runtime.device import clear_gpu_memory

logger = logging.getLogger(__name__)

# Maya-specific token constants
# These define the audio code token ranges used by Maya
CODE_START_TOKEN_ID = 128257  # Start of audio generation
CODE_END_TOKEN_ID = 128258    # End of audio generation
CODE_TOKEN_OFFSET = 128266    # Offset for SNAC codes
SNAC_MIN_ID = 128266          # Minimum SNAC token ID
SNAC_MAX_ID = 156937          # Maximum SNAC token ID


class MayaModel:
    """Wrapper for Maya text-to-speech model components."""

    def __init__(self, template: Dict[str, Any], config: Dict[str, Any]):
        """
        Initialize Maya model wrapper.

        Args:
            template: Model template configuration
            config: Model configuration including paths and settings
        """
        self.template = template
        self.config = config
        self.device = config.get("device", "cuda")
        self.dtype_str = config.get("dtype", "bfloat16")

        # Convert dtype string to torch dtype
        dtype_map = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32
        }
        self.dtype = dtype_map.get(self.dtype_str, torch.bfloat16)

        # Model components (loaded lazily)
        self.model = None
        self.tokenizer = None
        self.snac = None

        logger.debug(f"[MAYA MODEL] Initialized with device={self.device}, dtype={self.dtype_str}")

    def load(self, mode: str = "txt2speech"):
        """
        Load Maya model components.

        Args:
            mode: Generation mode (currently only "txt2speech" supported)
        """
        if mode != "txt2speech":
            raise ValueError(f"Unsupported mode: {mode}. Maya only supports 'txt2speech'")

        self._require_local("model_id")
        self._require_local("snac_model")

        logger.info("[MAYA MODEL] Loading model components...")

        # Load main model and tokenizer
        self._load_model()

        # Load SNAC codec
        self._load_snac()

        logger.info("[MAYA MODEL] All components loaded successfully")

    def _require_local(self, key: str) -> None:
        """Refuse a Hugging Face repo id in `config[key]`.

        Every `from_pretrained` below is handed `config[key]` verbatim, so a
        repo id here would make transformers/SNAC fetch the weights
        themselves - outside the download manager, with no history,
        containment or progress. `model_loader/maya` mirrors the repo through
        the ASSETS service and passes the resulting directory; this makes a
        regression to that fail loudly instead of silently re-opening the
        bypass.
        """
        value = self.config.get(key)
        if not value or not Path(str(value)).is_dir():
            raise ValueError(
                f"MayaModel config '{key}' must be a local directory, got {value!r}. "
                f"Repo ids must be mirrored into the model depot first "
                f"(see ModelLoaderMayaPipe._resolve_repo)."
            )

    def _load_model(self):
        """Load Maya TTS model and tokenizer."""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            model_id = self.config.get("model_id", "maya-research/maya1")

            logger.debug(f"[MAYA MODEL] Loading model from {model_id}...")

            # Try to use Flash Attention 2 if available
            try:
                import flash_attn
                attn_implementation = "flash_attention_2"
                logger.debug("[MAYA MODEL] Flash Attention 2 detected - using optimized attention")
            except ImportError:
                attn_implementation = "eager"
                logger.warning("[MAYA MODEL] Flash Attention 2 not available - using standard attention")

            # Check available VRAM to decide loading strategy
            if torch.cuda.is_available():
                total_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                logger.debug(f"[MAYA MODEL] Available VRAM: {total_vram_gb:.1f}GB")

                # 3B model needs ~6-8GB in bfloat16
                if total_vram_gb < 12:
                    logger.warning(f"[MAYA MODEL] Limited VRAM ({total_vram_gb:.1f}GB) - using device_map='auto'")
                    device_map = "auto"
                else:
                    logger.debug("[MAYA MODEL] Sufficient VRAM - loading directly to GPU")
                    device_map = None
            else:
                logger.warning("[MAYA MODEL] No CUDA available - loading to CPU")
                device_map = None

            # Load model
            if device_map is not None:
                self.model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    torch_dtype=self.dtype,
                    device_map=device_map,
                    attn_implementation=attn_implementation,
                )
            else:
                self.model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    torch_dtype=self.dtype,
                    attn_implementation=attn_implementation,
                    low_cpu_mem_usage=True
                )
                self.model = self.model.to(self.device)

            # Load tokenizer
            logger.debug(f"[MAYA MODEL] Loading tokenizer from {model_id}...")
            self.tokenizer = AutoTokenizer.from_pretrained(model_id)

            logger.debug("[MAYA MODEL] Model and tokenizer loaded successfully")

        except Exception as e:
            logger.error(f"[MAYA MODEL] Failed to load model: {str(e)}")
            raise

    def _load_snac(self):
        """Load SNAC neural audio codec."""
        try:
            from snac import SNAC

            snac_model = self.config.get("snac_model", "hubertsiuzdak/snac_24khz")

            logger.debug(f"[MAYA MODEL] Loading SNAC codec from {snac_model}...")

            self.snac = SNAC.from_pretrained(snac_model)
            self.snac = self.snac.eval()

            # Move to device
            if self.device != "cpu":
                self.snac = self.snac.to(self.device)

            logger.debug("[MAYA MODEL] SNAC codec loaded successfully")

        except ImportError:
            logger.error("[MAYA MODEL] SNAC not installed. Install with: pip install snac")
            raise ImportError("SNAC not installed. Install with: pip install snac")
        except Exception as e:
            logger.error(f"[MAYA MODEL] Failed to load SNAC codec: {str(e)}")
            raise

    def unload(self):
        """Unload all model components to free VRAM."""
        logger.info("[MAYA MODEL] Unloading all components...")

        if self.model is not None:
            del self.model
            self.model = None

        if self.tokenizer is not None:
            del self.tokenizer
            self.tokenizer = None

        if self.snac is not None:
            del self.snac
            self.snac = None

        # Clear CUDA cache
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        clear_gpu_memory()

        logger.info("[MAYA MODEL] All components unloaded, VRAM cleared")

    def get_code_end_token_id(self) -> int:
        """Get the end-of-audio code token ID.

        Maya uses CODE_END_TOKEN_ID (128258) to signal end of audio generation.
        """
        return CODE_END_TOKEN_ID

    def get_code_start_token_id(self) -> int:
        """Get the start-of-audio code token ID.

        Maya uses CODE_START_TOKEN_ID (128257) to signal start of audio generation.
        """
        return CODE_START_TOKEN_ID

    def get_snac_token_range(self) -> tuple:
        """Get the SNAC token ID range.

        Returns:
            Tuple of (min_id, max_id) for SNAC token range.
        """
        return (SNAC_MIN_ID, SNAC_MAX_ID)

    def get_code_token_offset(self) -> int:
        """Get the offset for converting Maya token IDs to SNAC codes.

        To convert Maya token ID to SNAC code: snac_code = maya_token - CODE_TOKEN_OFFSET
        """
        return CODE_TOKEN_OFFSET
