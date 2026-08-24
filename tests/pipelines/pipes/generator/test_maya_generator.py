"""
Tests for Maya text-to-speech generator pipe.

This test suite covers:
- Pipe metadata and configuration
- Input/output specifications
- Voice description building
- Text-to-speech generation workflow
- Audio decoding and saving
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import torch
import numpy as np

from src.pipelines.pipes.generator.maya.main import GeneratorMayaPipe
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import (
    AudioGenerationOutput,
    GalleryGenerationOutput,
    ProgressGenerationOutput,
    ParamGenerationOutput,
)


class TestGeneratorMayaPipe:
    """Tests for Maya text-to-speech generator pipe."""

    def test_pipe_metadata(self):
        """Test pipe has correct metadata."""
        assert GeneratorMayaPipe.name == "generator"
        assert "Maya" in GeneratorMayaPipe.description
        assert "text-to-speech" in GeneratorMayaPipe.description

    def test_default_config(self):
        """Test default configuration."""
        config = GeneratorMayaPipe.get_default_config()

        assert config["text"] == "Hello, how are you today?"
        assert config["voice_description"] == ""
        assert config["voice_age"] == "30s"
        assert config["voice_gender"] == "male"
        assert config["voice_pitch"] == "medium"
        assert config["voice_accent"] == "american"
        assert config["voice_warmth"] == 0.5
        assert config["temperature"] == 0.4
        assert config["top_p"] == 0.9
        assert config["repetition_penalty"] == 1.1
        assert config["max_new_tokens"] == 2048
        assert config["min_new_tokens"] == 28
        assert config["seed"] == -1
        assert config["sample_rate"] == 24000

    def test_configuration_specs(self):
        """Test configuration specifications."""
        specs = GeneratorMayaPipe.configuration()

        spec_names = [spec.name for spec in specs]
        assert "text" in spec_names
        assert "voice_description" in spec_names
        assert "voice_age" in spec_names
        assert "voice_gender" in spec_names
        assert "voice_pitch" in spec_names
        assert "voice_accent" in spec_names
        assert "voice_warmth" in spec_names
        assert "temperature" in spec_names
        assert "top_p" in spec_names
        assert "repetition_penalty" in spec_names
        assert "max_new_tokens" in spec_names
        assert "seed" in spec_names
        assert "sample_rate" in spec_names

        # Check temperature constraints
        temp_spec = next(s for s in specs if s.name == "temperature")
        assert temp_spec.min_value == 0.1
        assert temp_spec.max_value == 1.0

        # Check top_p constraints
        top_p_spec = next(s for s in specs if s.name == "top_p")
        assert top_p_spec.min_value == 0.5
        assert top_p_spec.max_value == 1.0

        # Check voice_warmth constraints
        warmth_spec = next(s for s in specs if s.name == "voice_warmth")
        assert warmth_spec.min_value == 0.0
        assert warmth_spec.max_value == 1.0

        # Check gender choices
        gender_spec = next(s for s in specs if s.name == "voice_gender")
        assert gender_spec.choices == ["male", "female"]

    def test_inputs_specs(self):
        """Test input specifications."""
        inputs = GeneratorMayaPipe.inputs()

        input_names = [inp.name for inp in inputs]
        assert "model" in input_names

        model_input = next(inp for inp in inputs if inp.name == "model")
        assert model_input.io_type == IOType.MODEL
        assert model_input.required is True
        assert model_input.is_array is False

    def test_outputs_specs(self):
        """Test output specifications."""
        outputs = GeneratorMayaPipe.outputs()

        output_names = [out.name for out in outputs]
        assert "audio" in output_names

        audio_output = next(out for out in outputs if out.name == "audio")
        assert audio_output.io_type == IOType.AUDIO
        assert audio_output.is_array is True

    def test_build_voice_description_custom(self):
        """Test building voice description with custom input."""
        pipe = GeneratorMayaPipe(config={})

        # Custom description takes precedence
        description = pipe._build_voice_description(
            custom_description="40-year-old, deep, warm",
            age="30s",
            gender="male",
            pitch="high",
            accent="british",
            warmth=0.9
        )

        assert description == "40-year-old, deep, warm"

    def test_build_voice_description_from_components(self):
        """Test building voice description from components."""
        pipe = GeneratorMayaPipe(config={})

        # No custom description - build from components
        description = pipe._build_voice_description(
            custom_description="",
            age="30s",
            gender="male",
            pitch="low",
            accent="american",
            warmth=0.8
        )

        assert "30s-year-old" in description
        assert "male" in description
        assert "low-pitch" in description
        assert "american accent" in description
        assert "warm" in description.lower()

    def test_build_voice_description_warmth_levels(self):
        """Test warmth descriptions at different levels."""
        pipe = GeneratorMayaPipe(config={})

        # Cold (< 0.3)
        desc = pipe._build_voice_description("", "30s", "male", "medium", "neutral", 0.1)
        assert "clinical" in desc

        # Neutral (0.3-0.5)
        desc = pipe._build_voice_description("", "30s", "male", "medium", "neutral", 0.4)
        assert "neutral" in desc

        # Warm (0.5-0.7)
        desc = pipe._build_voice_description("", "30s", "male", "medium", "neutral", 0.6)
        assert "warm" in desc

        # Very warm (> 0.7)
        desc = pipe._build_voice_description("", "30s", "male", "medium", "neutral", 0.9)
        assert "warm" in desc.lower() and "friendly" in desc.lower()

    def test_format_prompt(self):
        """Test prompt formatting for Maya model."""
        from src.pipelines.pipes.generator.maya.main import (
            SOH_ID, EOH_ID, SOA_ID, CODE_START_TOKEN_ID, TEXT_EOT_ID
        )

        pipe = GeneratorMayaPipe(config={})

        # Create mock tokenizer that returns expected special tokens
        mock_tokenizer = Mock()
        mock_tokenizer.decode = Mock(side_effect=lambda ids: {
            (SOH_ID,): "<|soh|>",
            (EOH_ID,): "<|eoh|>",
            (SOA_ID,): "<|soa|>",
            (CODE_START_TOKEN_ID,): "<|sos|>",
            (TEXT_EOT_ID,): "<|eot|>",
        }.get(tuple(ids), ""))
        mock_tokenizer.bos_token = "<|bos|>"

        prompt = pipe._format_prompt(
            voice_description="40-year-old, warm",
            text="Hello, how are you?",
            tokenizer=mock_tokenizer
        )

        # Check that the prompt contains the voice description and text
        assert '<description="40-year-old, warm">' in prompt
        assert "Hello, how are you?" in prompt
        # Check special tokens are present
        assert "<|soh|>" in prompt
        assert "<|eoh|>" in prompt
        assert "<|soa|>" in prompt
        assert "<|sos|>" in prompt

    def test_format_prompt_with_emotion_tags(self):
        """Test prompt formatting preserves emotion tags."""
        from src.pipelines.pipes.generator.maya.main import (
            SOH_ID, EOH_ID, SOA_ID, CODE_START_TOKEN_ID, TEXT_EOT_ID
        )

        pipe = GeneratorMayaPipe(config={})

        # Create mock tokenizer
        mock_tokenizer = Mock()
        mock_tokenizer.decode = Mock(side_effect=lambda ids: {
            (SOH_ID,): "<|soh|>",
            (EOH_ID,): "<|eoh|>",
            (SOA_ID,): "<|soa|>",
            (CODE_START_TOKEN_ID,): "<|sos|>",
            (TEXT_EOT_ID,): "<|eot|>",
        }.get(tuple(ids), ""))
        mock_tokenizer.bos_token = "<|bos|>"

        prompt = pipe._format_prompt(
            voice_description="young female",
            text="Hi! <laugh> That's funny. <sigh> Oh well.",
            tokenizer=mock_tokenizer
        )

        assert '<description="young female">' in prompt
        assert "<laugh>" in prompt
        assert "<sigh>" in prompt
        assert "That's funny" in prompt

    @patch.object(GeneratorMayaPipe, '_generate_tokens')
    @patch.object(GeneratorMayaPipe, '_decode_audio')
    @patch.object(GeneratorMayaPipe, '_get_audio_duration')
    def test_process_basic(
        self,
        mock_get_duration,
        mock_decode,
        mock_generate_tokens
    ):
        """Test basic TTS generation process."""
        from src.pipelines.pipes.generator.maya.main import (
            SOH_ID, EOH_ID, SOA_ID, CODE_START_TOKEN_ID, TEXT_EOT_ID
        )

        # Setup mocks
        mock_generate_tokens.return_value = torch.randint(0, 1000, (100,), dtype=torch.long)
        mock_decode.return_value = Path("/tmp/maya_audio/speech_12345.wav")
        mock_get_duration.return_value = 5.5

        # Create mock model with properly mocked tokenizer
        mock_model = Mock()
        mock_model.device = "cuda"

        # Mock tokenizer with decode method that returns strings
        mock_tokenizer = Mock()
        mock_tokenizer.decode = Mock(side_effect=lambda ids: {
            (SOH_ID,): "<|soh|>",
            (EOH_ID,): "<|eoh|>",
            (SOA_ID,): "<|soa|>",
            (CODE_START_TOKEN_ID,): "<|sos|>",
            (TEXT_EOT_ID,): "<|eot|>",
        }.get(tuple(ids), ""))
        mock_tokenizer.bos_token = "<|bos|>"
        mock_model.tokenizer = mock_tokenizer

        # Create pipe input
        pipe_input = PipeInput(input={"model": mock_model})

        # Create pipe with config
        config = GeneratorMayaPipe.get_default_config()
        config["text"] = "Hello world!"
        config["voice_gender"] = "female"
        config["temperature"] = 0.5
        pipe = GeneratorMayaPipe(config=config)

        # Track emitted outputs
        emitted_outputs = []

        def mock_callback(output):
            emitted_outputs.append(output)

        # Process
        with patch('torch.manual_seed'), \
             patch('torch.cuda.is_available', return_value=True), \
             patch('torch.cuda.manual_seed'), \
             patch('torch.randint', return_value=torch.tensor([12345])):
            result = pipe.process(pipe_input, mock_callback)

        # Verify token generation was called
        mock_generate_tokens.assert_called_once()

        # Verify decode was called
        mock_decode.assert_called_once()

        # Verify output contains audio path
        assert "audio" in result.output
        assert len(result.output["audio"]) == 1

        # Verify progress outputs were emitted
        progress_outputs = [o for o in emitted_outputs if isinstance(o, ProgressGenerationOutput)]
        assert len(progress_outputs) >= 3

        # Verify gallery was emitted
        gallery_outputs = [o for o in emitted_outputs if isinstance(o, GalleryGenerationOutput)]
        assert len(gallery_outputs) == 1
        assert len(gallery_outputs[0].audios) == 1

        # Verify audio output properties
        audio = gallery_outputs[0].audios[0]
        assert audio.track_type == "speech"
        assert audio.duration == 5.5
        assert audio.sample_rate == 24000
        assert audio.channels == 1
        assert audio.temperature == 0.5

        # Verify parameters were emitted
        param_outputs = [o for o in emitted_outputs if isinstance(o, ParamGenerationOutput)]
        assert len(param_outputs) >= 2

    def test_generate_tokens(self):
        """Test token generation."""
        # Create mock model
        mock_model = Mock()
        mock_model.device = "cpu"

        # Mock tokenizer - return an object with input_ids attribute
        mock_tokenizer_output = Mock()
        mock_tokenizer_output.input_ids = torch.tensor([[1, 2, 3]])
        mock_tokenizer = Mock()
        mock_tokenizer.return_value = mock_tokenizer_output
        mock_model.tokenizer = mock_tokenizer

        # Mock model generate
        generated_output = torch.tensor([[1, 2, 3, 100, 101, 102, 103]])
        mock_model.model = Mock()
        mock_model.model.generate.return_value = generated_output

        mock_model.get_code_end_token_id.return_value = 50256

        # Create pipe
        config = GeneratorMayaPipe.get_default_config()
        pipe = GeneratorMayaPipe(config=config)

        # Call generate_tokens
        tokens = pipe._generate_tokens(
            model=mock_model,
            prompt='<description="test"> Hello',
            temperature=0.4,
            top_p=0.9,
            repetition_penalty=1.1,
            max_new_tokens=2048,
            min_new_tokens=28,
            generation_outputs=Mock()
        )

        # Verify tokenizer was called
        mock_tokenizer.assert_called_once()
        call_args = mock_tokenizer.call_args
        assert "test" in call_args[0][0]
        assert "Hello" in call_args[0][0]

        # Verify model.generate was called
        mock_model.model.generate.assert_called_once()
        call_kwargs = mock_model.model.generate.call_args[1]
        assert call_kwargs["max_new_tokens"] == 2048
        assert call_kwargs["temperature"] == 0.4
        assert call_kwargs["top_p"] == 0.9

        # Verify output is correct (generated tokens minus input)
        assert len(tokens) == 4  # 7 total - 3 input = 4 generated

    def test_extract_audio_codes(self):
        """Test audio code extraction from tokens."""
        from src.pipelines.pipes.generator.maya.main import (
            CODE_TOKEN_OFFSET, SNAC_MIN_ID, SNAC_CODEBOOK_SIZE
        )

        pipe = GeneratorMayaPipe(config={})

        # Create mock tokenizer
        mock_tokenizer = Mock()

        # Create tokens in Maya's SNAC range [128266, 156937]
        # Maya encodes SNAC codes as: snac_code + CODE_TOKEN_OFFSET (128266)
        # 3 frames * 7 tokens = 21 tokens
        # The slot ordering is: L1=[0], L2=[1,4], L3=[2,3,5,6]
        tokens = torch.tensor([
            # Frame 1: slots [0, 1, 2, 3, 4, 5, 6] with Maya token IDs
            SNAC_MIN_ID + 100,  # slot 0 -> L1
            SNAC_MIN_ID + 200,  # slot 1 -> L2
            SNAC_MIN_ID + 300,  # slot 2 -> L3
            SNAC_MIN_ID + 400,  # slot 3 -> L3
            SNAC_MIN_ID + 500,  # slot 4 -> L2
            SNAC_MIN_ID + 600,  # slot 5 -> L3
            SNAC_MIN_ID + 700,  # slot 6 -> L3
            # Frame 2
            SNAC_MIN_ID + 110,
            SNAC_MIN_ID + 210,
            SNAC_MIN_ID + 310,
            SNAC_MIN_ID + 410,
            SNAC_MIN_ID + 510,
            SNAC_MIN_ID + 610,
            SNAC_MIN_ID + 710,
            # Frame 3
            SNAC_MIN_ID + 120,
            SNAC_MIN_ID + 220,
            SNAC_MIN_ID + 320,
            SNAC_MIN_ID + 420,
            SNAC_MIN_ID + 520,
            SNAC_MIN_ID + 620,
            SNAC_MIN_ID + 720,
        ], dtype=torch.long)

        codes = pipe._extract_audio_codes(tokens, mock_tokenizer)

        # Should return 3 levels for SNAC
        assert codes is not None
        assert len(codes) == 3

        # L1: 1 code per frame, shape [1, 3] (slot 0)
        assert codes[0].shape == (1, 3)

        # L2: 2 codes per frame, shape [1, 6] (slots 1, 4)
        assert codes[1].shape == (1, 6)

        # L3: 4 codes per frame, shape [1, 12] (slots 2, 3, 5, 6)
        assert codes[2].shape == (1, 12)

        # Verify codes are properly extracted with correct slot ordering
        # L1 should contain [100, 110, 120] (slot 0 from each frame)
        assert codes[0][0, 0].item() == 100
        assert codes[0][0, 1].item() == 110
        assert codes[0][0, 2].item() == 120

        # L2 should contain [200, 500, 210, 510, 220, 520] (slots 1, 4 from each frame)
        assert codes[1][0, 0].item() == 200  # frame 1, slot 1
        assert codes[1][0, 1].item() == 500  # frame 1, slot 4
        assert codes[1][0, 2].item() == 210  # frame 2, slot 1
        assert codes[1][0, 3].item() == 510  # frame 2, slot 4

        # L3 codes are slots 2, 3, 5, 6
        assert codes[2][0, 0].item() == 300  # frame 1, slot 2
        assert codes[2][0, 1].item() == 400  # frame 1, slot 3
        assert codes[2][0, 2].item() == 600  # frame 1, slot 5
        assert codes[2][0, 3].item() == 700  # frame 1, slot 6

    def test_get_audio_duration(self):
        """Test getting audio duration."""
        import sys

        # Create mock soundfile module
        mock_sf = Mock()
        mock_info = Mock()
        mock_info.duration = 3.5
        mock_sf.info.return_value = mock_info

        pipe = GeneratorMayaPipe(config={})

        # Patch sf module in the maya generator
        import src.pipelines.pipes.generator.maya.main as maya_main
        original_sf = maya_main.sf
        maya_main.sf = mock_sf

        try:
            duration = pipe._get_audio_duration(Path("/tmp/test.wav"))

            assert duration == 3.5
            mock_sf.info.assert_called_once()
        finally:
            maya_main.sf = original_sf

    def test_get_audio_duration_error(self):
        """Test getting audio duration with error."""
        import src.pipelines.pipes.generator.maya.main as maya_main

        # Create mock soundfile that raises exception
        mock_sf = Mock()
        mock_sf.info.side_effect = Exception("File not found")

        pipe = GeneratorMayaPipe(config={})

        original_sf = maya_main.sf
        maya_main.sf = mock_sf

        try:
            duration = pipe._get_audio_duration(Path("/tmp/missing.wav"))

            # Should return 0.0 on error
            assert duration == 0.0
        finally:
            maya_main.sf = original_sf

    def test_seed_reproducibility(self):
        """Test that seed parameter affects generation."""
        config = GeneratorMayaPipe.get_default_config()

        # With specific seed
        config["seed"] = 42
        pipe = GeneratorMayaPipe(config=config)
        assert pipe.config["seed"] == 42

        # With random seed
        config["seed"] = -1
        pipe = GeneratorMayaPipe(config=config)
        assert pipe.config["seed"] == -1

    def test_voice_pitch_mapping(self):
        """Test voice pitch description mapping."""
        pipe = GeneratorMayaPipe(config={})

        pitches = ["low", "medium-low", "medium", "medium-high", "high"]
        expected = ["low-pitch", "medium-low pitch", "medium pitch", "medium-high pitch", "high-pitch"]

        for pitch, expected_desc in zip(pitches, expected):
            desc = pipe._build_voice_description("", "30s", "male", pitch, "neutral", 0.5)
            assert expected_desc in desc
