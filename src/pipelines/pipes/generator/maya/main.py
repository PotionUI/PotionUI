"""
Maya Generator Pipe - Text-to-Speech Generation

This pipe implements text-to-speech generation using the Maya model:
- Supports natural language voice descriptions
- Inline emotion tags for expressive synthesis
- SNAC codec for 24kHz audio output

The generator creates speech audio from text with configurable voice
characteristics and emotional expressions.
"""

import logging
import torch
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional
import tempfile
try:
    import soundfile as sf
except ImportError:
    sf = None  # Will be checked at runtime

from src.pipelines.outputs import (
    GalleryGenerationOutput,
    AudioGenerationOutput,
    ProgressGenerationOutput,
    ParamGenerationOutput,
)
from src.pipelines.contracts import PipeInput, IOType, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import Progress, Icon
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.models.maya.maya_model import MayaModel

logger = logging.getLogger(__name__)

# Maya-specific token constants
# These define the audio code token ranges used by Maya
CODE_START_TOKEN_ID = 128257  # Start of audio generation (SOS)
CODE_END_TOKEN_ID = 128258    # End of audio generation (EOS)
CODE_TOKEN_OFFSET = 128266    # Offset for SNAC codes
SNAC_MIN_ID = 128266          # Minimum SNAC token ID
SNAC_MAX_ID = 156937          # Maximum SNAC token ID
SNAC_TOKENS_PER_FRAME = 7     # 7 tokens per audio frame
SNAC_CODEBOOK_SIZE = 4096     # SNAC codebook size (codes must be < 4096)

# Special prompt tokens
SOH_ID = 128259  # Start of header
EOH_ID = 128260  # End of header
SOA_ID = 128261  # Start of audio
BOS_ID = 128000  # Beginning of sequence
TEXT_EOT_ID = 128009  # End of text


class GeneratorMayaPipe(BaseGeneratorPipe):
    """Maya text-to-speech generator pipe."""

    name = "generator"
    description = "Maya text-to-speech generation with emotional expressions"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "text": "Hello, how are you today?",
            "voice_description": "",
            "voice_age": "30s",
            "voice_gender": "male",
            "voice_pitch": "medium",
            "voice_accent": "american",
            "voice_warmth": 0.5,
            "temperature": 0.4,
            "top_p": 0.9,
            "repetition_penalty": 1.1,
            "max_new_tokens": 2048,
            "min_new_tokens": 28,
            "seed": -1,
            "sample_rate": 24000,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Configuration specifications for Maya generator."""
        return [
            PipeConfigSpec("text", str, "Hello!", "Text to synthesize into speech",
                          required=True),
            PipeConfigSpec("voice_description", str, "", "Custom voice description",
                          required=False),
            PipeConfigSpec("voice_age", str, "30s", "Voice age range",
                          required=False),
            PipeConfigSpec("voice_gender", str, "male", "Voice gender",
                          required=False, choices=["male", "female"]),
            PipeConfigSpec("voice_pitch", str, "medium", "Voice pitch level",
                          required=False, choices=["low", "medium-low", "medium", "medium-high", "high"]),
            PipeConfigSpec("voice_accent", str, "american", "Voice accent",
                          required=False),
            PipeConfigSpec("voice_warmth", float, 0.5, "Voice warmth (0-1)",
                          required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("temperature", float, 0.4, "Generation temperature",
                          required=False, min_value=0.1, max_value=1.0),
            PipeConfigSpec("top_p", float, 0.9, "Nucleus sampling top-p",
                          required=False, min_value=0.5, max_value=1.0),
            PipeConfigSpec("repetition_penalty", float, 1.1, "Repetition penalty",
                          required=False, min_value=1.0, max_value=2.0),
            PipeConfigSpec("max_new_tokens", int, 2048, "Max tokens to generate",
                          required=False, min_value=256, max_value=4096),
            PipeConfigSpec("min_new_tokens", int, 28, "Min tokens to generate",
                          required=False, min_value=1, max_value=256),
            PipeConfigSpec("seed", int, -1, "Random seed (-1 for random)",
                          required=False),
            PipeConfigSpec("sample_rate", int, 24000, "Audio sample rate",
                          required=False, choices=[24000]),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Input specifications for Maya generator."""
        return [
            PipeInputSpec("model", IOType.MODEL, True,
                         "Loaded Maya model with all components", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Output specifications for Maya generator."""
        return [
            PipeOutputSpec("audio", IOType.AUDIO,
                          "Generated speech audio file", is_array=True),
        ]

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        # Maya always generates a single utterance per invocation and has no
        # upstream "seed" input (see inputs()) - seed comes purely from config.
        return GeneratorContext(
            quantity=1,
            input_seeds=None,
            extra={"model": pipe_input.input["model"]},
        )

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> AudioGenerationOutput:
        """
        Generate speech using Maya model.

        Returns:
            AudioGenerationOutput for the synthesized utterance
        """
        try:
            model: MayaModel = ctx.extra["model"]

            # Get configuration
            text = self.config.get("text", "Hello!")
            voice_description = self.config.get("voice_description", "")
            voice_age = self.config.get("voice_age", "30s")
            voice_gender = self.config.get("voice_gender", "male")
            voice_pitch = self.config.get("voice_pitch", "medium")
            voice_accent = self.config.get("voice_accent", "american")
            voice_warmth = float(self.config.get("voice_warmth", 0.5))
            temperature = float(self.config.get("temperature", 0.4))
            top_p = float(self.config.get("top_p", 0.9))
            repetition_penalty = float(self.config.get("repetition_penalty", 1.1))
            max_new_tokens = int(self.config.get("max_new_tokens", 2048))
            min_new_tokens = int(self.config.get("min_new_tokens", 28))
            configured_seed = int(self.config.get("seed", -1))
            sample_rate = int(self.config.get("sample_rate", 24000))

            logger.info(f"[GENERATOR MAYA] Generating speech for text: {text[:50]}...")
            logger.debug(f"[GENERATOR MAYA] Temperature: {temperature}, Top-p: {top_p}")

            # Set seed for reproducibility. `seed` was already resolved by
            # plan_seeds (explicit config value, or a fresh random one via
            # the same generate_seed()/torch.randint path Maya used inline);
            # cuda.manual_seed is only called for an explicitly-configured
            # seed, matching the original branching exactly.
            torch.manual_seed(seed)
            if configured_seed != -1:
                if torch.cuda.is_available():
                    torch.cuda.manual_seed(seed)
                logger.debug(f"[GENERATOR MAYA] Using seed: {seed}")
            else:
                logger.debug(f"[GENERATOR MAYA] Generated random seed: {seed}")

            progress.step(0, 3, state="Preparing voice description...", icon=Icon(name="mic", effect="pulse"))

            # Build voice description
            full_description = self._build_voice_description(
                custom_description=voice_description,
                age=voice_age,
                gender=voice_gender,
                pitch=voice_pitch,
                accent=voice_accent,
                warmth=voice_warmth
            )

            logger.debug(f"[GENERATOR MAYA] Voice description: {full_description}")

            # Format prompt for Maya with proper special tokens
            formatted_prompt = self._format_prompt(full_description, text, model.tokenizer)
            logger.debug(f"[GENERATOR MAYA] Formatted prompt: {repr(formatted_prompt[:100])}...")

            progress.step(1, 3, state="Generating speech tokens...", icon=Icon(name="cpu", effect="pulse"))

            # Generate audio tokens
            audio_tokens = self._generate_tokens(
                model=model,
                prompt=formatted_prompt,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                max_new_tokens=max_new_tokens,
                min_new_tokens=min_new_tokens,
                generation_outputs=progress.emit
            )

            progress.step(2, 3, state="Decoding audio...", icon=Icon(name="waveform", effect="pulse"))

            # Decode audio tokens to waveform
            audio_path = self._decode_audio(
                model=model,
                tokens=audio_tokens,
                sample_rate=sample_rate,
                generation_outputs=progress.emit
            )

            # Get audio duration
            duration = self._get_audio_duration(audio_path)

            # Create audio output
            audio_output = AudioGenerationOutput(
                audio_path=audio_path,
                temporary=False,
                track_type="speech",
                seed=seed,
                duration=duration,
                sample_rate=sample_rate,
                channels=1,  # Maya outputs mono
                temperature=temperature,
                top_p=top_p,
            )

            logger.info(f"[GENERATOR MAYA] Successfully generated speech: {audio_path}")

            return audio_output

        except Exception as e:
            logger.error(f"[GENERATOR MAYA] Error during generation: {str(e)}", exc_info=True)
            raise

    def emit_results(self, generation_outputs: callable, results: List[AudioGenerationOutput], used_seeds: List[int]) -> None:
        if not results:
            return

        audio_output = results[0]

        # Emit gallery with audio output
        generation_outputs(GalleryGenerationOutput(images=[], audios=[audio_output]))

        # Emit parameters
        generation_outputs(ParamGenerationOutput(name="seed", values=[audio_output.seed]))
        generation_outputs(ParamGenerationOutput(name="temperature", values=[audio_output.temperature]))
        generation_outputs(ParamGenerationOutput(name="duration", values=[audio_output.duration]))

        generation_outputs(ProgressGenerationOutput(
            state=f"Generated {audio_output.duration:.1f}s of speech",
            progress=Progress(current=3, max=3)
        ))

    def build_output(self, results: List[AudioGenerationOutput]) -> Dict[str, Any]:
        return {"audio": [r.audio_path for r in results]}

    def _build_voice_description(
        self,
        custom_description: str,
        age: str,
        gender: str,
        pitch: str,
        accent: str,
        warmth: float
    ) -> str:
        """
        Build a voice description string from parameters.

        Args:
            custom_description: User-provided custom description
            age: Voice age (e.g., "30s", "40s")
            gender: Voice gender ("male", "female")
            pitch: Voice pitch ("low", "medium", "high")
            accent: Voice accent (e.g., "american", "british")
            warmth: Voice warmth (0-1)

        Returns:
            Combined voice description string
        """
        if custom_description.strip():
            # Use custom description if provided
            return custom_description.strip()

        # Build description from components
        parts = []

        # Age
        if age:
            if age.endswith("s"):
                parts.append(f"{age}-year-old")
            else:
                parts.append(f"{age} year old")

        # Gender
        if gender:
            parts.append(gender)

        # Pitch
        if pitch:
            pitch_map = {
                "low": "low-pitch",
                "medium-low": "medium-low pitch",
                "medium": "medium pitch",
                "medium-high": "medium-high pitch",
                "high": "high-pitch"
            }
            parts.append(pitch_map.get(pitch, pitch))

        # Warmth
        if warmth is not None:
            if warmth < 0.3:
                parts.append("clinical")
            elif warmth < 0.5:
                parts.append("neutral")
            elif warmth < 0.7:
                parts.append("warm")
            else:
                parts.append("very warm and friendly")

        # Accent
        if accent:
            parts.append(f"with {accent} accent")

        return ", ".join(parts) if parts else "natural voice"

    def _format_prompt(self, voice_description: str, text: str, tokenizer) -> str:
        """
        Format the prompt for Maya model.

        Maya expects prompts in the format:
        SOH + BOS + <description="voice_desc"> text + EOT + EOH + SOA + SOS

        Args:
            voice_description: Voice description string
            text: Text to synthesize
            tokenizer: Model tokenizer for decoding special tokens

        Returns:
            Formatted prompt string
        """
        # Decode special tokens
        soh_token = tokenizer.decode([SOH_ID])
        eoh_token = tokenizer.decode([EOH_ID])
        soa_token = tokenizer.decode([SOA_ID])
        sos_token = tokenizer.decode([CODE_START_TOKEN_ID])
        eot_token = tokenizer.decode([TEXT_EOT_ID])
        bos_token = tokenizer.bos_token

        # Format the text with voice description
        formatted_text = f'<description="{voice_description}"> {text}'

        # Build full prompt with Maya's expected structure
        prompt = (
            soh_token + bos_token + formatted_text + eot_token +
            eoh_token + soa_token + sos_token
        )

        return prompt

    def _generate_tokens(
        self,
        model: MayaModel,
        prompt: str,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
        max_new_tokens: int,
        min_new_tokens: int,
        generation_outputs: callable
    ) -> torch.Tensor:
        """
        Generate audio tokens from text prompt.

        Args:
            model: Maya model wrapper
            prompt: Formatted text prompt
            temperature: Generation temperature
            top_p: Nucleus sampling parameter
            repetition_penalty: Repetition penalty
            max_new_tokens: Maximum tokens to generate
            min_new_tokens: Minimum tokens to generate
            generation_outputs: Callback for progress updates

        Returns:
            Generated audio tokens
        """
        logger.debug("[GENERATOR MAYA] Tokenizing prompt...")

        # Tokenize input
        inputs = model.tokenizer(prompt, return_tensors="pt")
        input_ids = inputs.input_ids.to(model.device)

        logger.debug(f"[GENERATOR MAYA] Input shape: {input_ids.shape}")

        # Generate with Maya-specific end token
        # Maya uses CODE_END_TOKEN_ID (128258) to signal end of audio generation
        with torch.no_grad():
            outputs = model.model.generate(
                input_ids=input_ids,
                max_new_tokens=max_new_tokens,
                min_new_tokens=min_new_tokens,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                do_sample=True,
                eos_token_id=CODE_END_TOKEN_ID,
                pad_token_id=model.tokenizer.pad_token_id,
            )

        # Extract generated tokens (remove input)
        generated_tokens = outputs[0, input_ids.shape[1]:]

        logger.debug(f"[GENERATOR MAYA] Generated {len(generated_tokens)} tokens")

        # Log token range for debugging
        if len(generated_tokens) > 0:
            min_tok = generated_tokens.min().item()
            max_tok = generated_tokens.max().item()
            logger.debug(f"[GENERATOR MAYA] Token range: {min_tok} - {max_tok}")

        return generated_tokens

    def _decode_audio(
        self,
        model: MayaModel,
        tokens: torch.Tensor,
        sample_rate: int,
        generation_outputs: callable
    ) -> Path:
        """
        Decode audio tokens to waveform using SNAC.

        Maya uses a specific SNAC decoding process:
        1. Extract SNAC codes from generated tokens
        2. Unpack 7-token frames to 3 hierarchical levels
        3. Use snac.quantizer.from_codes() to get quantized representation
        4. Use snac.decoder() to decode to waveform

        Args:
            model: Maya model wrapper
            tokens: Generated audio tokens
            sample_rate: Output sample rate
            generation_outputs: Callback for progress updates

        Returns:
            Path to saved audio file
        """
        logger.debug("[GENERATOR MAYA] Decoding audio tokens with SNAC...")

        # Extract audio codes from tokens
        # Maya generates 7 tokens per frame for SNAC
        audio_codes = self._extract_audio_codes(tokens, model.tokenizer)

        if audio_codes is None or len(audio_codes) == 0:
            raise ValueError("No audio codes found in generated tokens")

        logger.debug(f"[GENERATOR MAYA] Extracted {len(audio_codes)} hierarchical code levels")

        # Decode with SNAC using Maya's approach:
        # 1. Convert codes to quantized representation
        # 2. Decode with the SNAC decoder
        with torch.no_grad():
            # Stack codes into format expected by SNAC quantizer
            # The from_codes method expects a list of tensors for each level
            codes_tensor = audio_codes  # Already a list of [L1, L2, L3] tensors

            # Use SNAC quantizer to convert codes to latent representation
            # Then decode to audio waveform
            try:
                # Maya's approach: quantizer.from_codes() -> decoder()
                z_q = model.snac.quantizer.from_codes(codes_tensor)
                audio_tensor = model.snac.decoder(z_q)
                audio = audio_tensor[0, 0].cpu().numpy()

                # Remove warmup samples (SNAC has ~2048 sample warmup)
                warmup_samples = 2048
                if len(audio) > warmup_samples:
                    audio = audio[warmup_samples:]

                logger.debug(f"[GENERATOR MAYA] Decoded audio shape: {audio.shape}")

            except Exception as e:
                logger.warning(f"[GENERATOR MAYA] Maya-style decode failed: {e}, trying standard decode...")
                # Fallback to standard SNAC decode
                audio_result = model.snac.decode(codes_tensor)
                if isinstance(audio_result, torch.Tensor):
                    audio = audio_result.squeeze().cpu().numpy()
                else:
                    audio = audio_result

        # Ensure proper shape
        if audio.ndim > 1:
            audio = audio.squeeze()

        # Normalize audio
        max_val = np.abs(audio).max()
        if max_val > 0:
            audio = audio / max_val * 0.95  # Leave some headroom

        # Save to temporary file
        temp_dir = Path(tempfile.gettempdir()) / "maya_audio"
        temp_dir.mkdir(parents=True, exist_ok=True)

        audio_path = temp_dir / f"speech_{np.random.randint(0, 100000)}.wav"

        if sf is None:
            raise ImportError("soundfile is required for audio saving. Install with: pip install soundfile")
        sf.write(str(audio_path), audio, sample_rate)

        logger.debug(f"[GENERATOR MAYA] Saved audio to: {audio_path}")

        return audio_path

    def _extract_audio_codes(
        self,
        tokens: torch.Tensor,
        tokenizer
    ) -> Optional[List[torch.Tensor]]:
        """
        Extract audio codes from generated tokens.

        Maya generates audio codes in a specific format that SNAC can decode.
        Maya uses token IDs in range [128266, 156937] for SNAC audio codes.
        The codes are hierarchical with 3 levels (L1, L2, L3) at different rates.

        The 7 tokens per frame are NOT sequential - they follow a specific pattern:
        - L1: slot 0 (1 token per frame)
        - L2: slots 1, 4 (2 tokens per frame)
        - L3: slots 2, 3, 5, 6 (4 tokens per frame)

        Args:
            tokens: Generated tokens
            tokenizer: Tokenizer for decoding

        Returns:
            List of tensors for each hierarchical level, or None if extraction fails
        """
        # Convert tokens to list for processing
        token_list = tokens.cpu().tolist()

        logger.debug(f"[GENERATOR MAYA] Processing {len(token_list)} tokens for audio code extraction")

        # Find EOS token position if present
        try:
            eos_idx = token_list.index(CODE_END_TOKEN_ID)
            token_list = token_list[:eos_idx]
            logger.debug(f"[GENERATOR MAYA] Found EOS token at position {eos_idx}")
        except ValueError:
            logger.debug("[GENERATOR MAYA] No EOS token found, using all tokens")

        # Filter to audio tokens in Maya's SNAC range [SNAC_MIN_ID, SNAC_MAX_ID]
        snac_tokens = [t for t in token_list if SNAC_MIN_ID <= t <= SNAC_MAX_ID]

        logger.debug(f"[GENERATOR MAYA] Found {len(snac_tokens)} SNAC tokens in range [{SNAC_MIN_ID}, {SNAC_MAX_ID}]")

        if not snac_tokens:
            logger.warning("[GENERATOR MAYA] No audio tokens found in expected SNAC range")
            # Log token distribution for debugging
            if token_list:
                unique_tokens = set(token_list)
                logger.debug(f"[GENERATOR MAYA] Token distribution: min={min(token_list)}, max={max(token_list)}, unique={len(unique_tokens)}")
                # Log counts by range
                snac_count = sum(1 for t in token_list if SNAC_MIN_ID <= t <= SNAC_MAX_ID)
                other_count = len(token_list) - snac_count
                logger.debug(f"[GENERATOR MAYA] SNAC tokens: {snac_count}, Other tokens: {other_count}")
            return None

        # Calculate number of complete frames (7 tokens per frame)
        num_frames = len(snac_tokens) // SNAC_TOKENS_PER_FRAME
        if num_frames == 0:
            logger.warning(f"[GENERATOR MAYA] Not enough tokens for audio frame (got {len(snac_tokens)}, need {SNAC_TOKENS_PER_FRAME})")
            return None

        # Trim to complete frames
        snac_tokens = snac_tokens[:num_frames * SNAC_TOKENS_PER_FRAME]

        logger.debug(f"[GENERATOR MAYA] Unpacking {num_frames} audio frames")

        # Unpack 7-token SNAC frames to 3 hierarchical levels
        # Maya uses a specific slot ordering that is NOT sequential
        l1, l2, l3 = [], [], []

        for i in range(num_frames):
            slots = snac_tokens[i * 7:(i + 1) * 7]

            # L1: slot 0 (1 code per frame)
            # Apply modulo to fit in SNAC codebook (4096)
            l1.append((slots[0] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)

            # L2: slots 1 and 4 (2 codes per frame)
            l2.append((slots[1] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)
            l2.append((slots[4] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)

            # L3: slots 2, 3, 5, 6 (4 codes per frame)
            l3.append((slots[2] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)
            l3.append((slots[3] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)
            l3.append((slots[5] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)
            l3.append((slots[6] - CODE_TOKEN_OFFSET) % SNAC_CODEBOOK_SIZE)

        # Convert to tensors with correct shape for SNAC
        device = tokens.device
        l1_codes = torch.tensor(l1, dtype=torch.long, device=device).unsqueeze(0)
        l2_codes = torch.tensor(l2, dtype=torch.long, device=device).unsqueeze(0)
        l3_codes = torch.tensor(l3, dtype=torch.long, device=device).unsqueeze(0)

        logger.debug(f"[GENERATOR MAYA] Unpacked to {num_frames} frames")
        logger.debug(f"[GENERATOR MAYA] L1: {l1_codes.shape}, L2: {l2_codes.shape}, L3: {l3_codes.shape}")
        logger.debug(f"[GENERATOR MAYA] Code ranges - L1: [{l1_codes.min().item()}, {l1_codes.max().item()}], "
                   f"L2: [{l2_codes.min().item()}, {l2_codes.max().item()}], "
                   f"L3: [{l3_codes.min().item()}, {l3_codes.max().item()}]")

        return [l1_codes, l2_codes, l3_codes]

    def _get_audio_duration(self, audio_path: Path) -> float:
        """
        Get audio duration in seconds.

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds
        """
        try:
            if sf is None:
                logger.warning("[GENERATOR MAYA] soundfile not installed, cannot get duration")
                return 0.0
            info = sf.info(str(audio_path))
            return info.duration
        except Exception as e:
            logger.warning(f"[GENERATOR MAYA] Could not get audio duration: {e}")
            return 0.0
