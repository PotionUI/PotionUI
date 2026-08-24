"""
Unit tests for audio generation outputs.

Tests AudioGenerationOutput class and GalleryGenerationOutput with audio support.
"""

import pytest
from pathlib import Path
from dataclasses import fields, MISSING

from src.pipelines.outputs import (
    GenerationOutput, AudioGenerationOutput, GalleryGenerationOutput,
    ImageGenerationOutput, VideoGenerationOutput
)
from PIL import Image


class TestAudioGenerationOutput:
    """Tests for AudioGenerationOutput dataclass."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_audio_path = Path("/tmp/test_audio.wav")

    def test_required_attributes(self):
        """Test that AudioGenerationOutput requires audio_path."""
        output = AudioGenerationOutput(audio_path=self.test_audio_path)

        assert output.audio_path == self.test_audio_path
        assert isinstance(output, GenerationOutput)

    def test_default_values(self):
        """Test default values for optional attributes."""
        output = AudioGenerationOutput(audio_path=self.test_audio_path)

        assert output.temporary is True
        assert output.track_type == "mixed"
        assert output.seed is None
        assert output.duration is None
        assert output.sample_rate is None
        assert output.channels is None
        assert output.temperature is None
        assert output.top_p is None
        assert output.guidance_scale is None
        assert output.segment is None

    def test_track_type_vocal(self):
        """Test creating vocal track output."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            track_type="vocal",
            temporary=False
        )

        assert output.track_type == "vocal"
        assert output.temporary is False

    def test_track_type_instrumental(self):
        """Test creating instrumental track output."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            track_type="instrumental"
        )

        assert output.track_type == "instrumental"

    def test_track_type_mixed(self):
        """Test creating mixed track output."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            track_type="mixed"
        )

        assert output.track_type == "mixed"

    def test_custom_values(self):
        """Test setting custom values for all attributes."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            temporary=False,
            track_type="vocal",
            seed=12345,
            duration=30.5,
            sample_rate=16000,
            channels=2,
            temperature=1.0,
            top_p=0.93,
            guidance_scale=1.5,
            segment=0
        )

        assert output.audio_path == self.test_audio_path
        assert output.temporary is False
        assert output.track_type == "vocal"
        assert output.seed == 12345
        assert output.duration == 30.5
        assert output.sample_rate == 16000
        assert output.channels == 2
        assert output.temperature == 1.0
        assert output.top_p == 0.93
        assert output.guidance_scale == 1.5
        assert output.segment == 0

    def test_path_types(self):
        """Test that audio_path accepts different path types."""
        # String path
        output1 = AudioGenerationOutput(audio_path="/tmp/audio.wav")
        assert output1.audio_path == "/tmp/audio.wav"

        # Path object
        output2 = AudioGenerationOutput(audio_path=Path("/tmp/audio.wav"))
        assert output2.audio_path == Path("/tmp/audio.wav")

    def test_numeric_types(self):
        """Test that numeric attributes accept appropriate types."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            seed=42,
            duration=30.0,
            sample_rate=16000,
            channels=2,
            temperature=1.0,
            top_p=0.93,
            guidance_scale=1.5,
            segment=0
        )

        assert isinstance(output.seed, int)
        assert isinstance(output.duration, (int, float))
        assert isinstance(output.sample_rate, int)
        assert isinstance(output.channels, int)
        assert isinstance(output.temperature, (int, float))
        assert isinstance(output.top_p, (int, float))
        assert isinstance(output.guidance_scale, (int, float))
        assert isinstance(output.segment, int)

    def test_common_sample_rates(self):
        """Test with common audio sample rates."""
        sample_rates = [16000, 22050, 44100, 48000]

        for rate in sample_rates:
            output = AudioGenerationOutput(
                audio_path=self.test_audio_path,
                sample_rate=rate
            )
            assert output.sample_rate == rate

    def test_mono_and_stereo(self):
        """Test mono and stereo channel configurations."""
        # Mono
        output_mono = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            channels=1
        )
        assert output_mono.channels == 1

        # Stereo
        output_stereo = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            channels=2
        )
        assert output_stereo.channels == 2

    def test_multi_segment_generation(self):
        """Test segment tracking for multi-segment generation."""
        segments = []
        for i in range(3):
            output = AudioGenerationOutput(
                audio_path=Path(f"/tmp/segment_{i}.wav"),
                segment=i
            )
            segments.append(output)

        assert len(segments) == 3
        assert segments[0].segment == 0
        assert segments[1].segment == 1
        assert segments[2].segment == 2

    def test_generation_parameters(self):
        """Test that generation parameters are stored correctly."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            temperature=1.2,
            top_p=0.95,
            guidance_scale=2.0
        )

        assert output.temperature == 1.2
        assert output.top_p == 0.95
        assert output.guidance_scale == 2.0

    def test_pipe_tracking(self):
        """Test that pipe tracking attributes work with audio outputs."""
        output = AudioGenerationOutput(audio_path=self.test_audio_path)

        output.pipe_id = 5
        output.pipe_name = "generator_maya"

        assert output.pipe_id == 5
        assert output.pipe_name == "generator_maya"

    def test_dataclass_functionality(self):
        """Test that AudioGenerationOutput works as a dataclass."""
        output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            track_type="vocal",
            temporary=False
        )

        assert hasattr(output, '__dataclass_fields__')
        assert output.track_type == "vocal"
        assert output.temporary is False

    def test_field_definitions(self):
        """Test that critical fields are properly defined."""
        audio_fields = fields(AudioGenerationOutput)
        field_names = [f.name for f in audio_fields]

        # Check required field
        assert 'audio_path' in field_names

        # Check audio-specific fields
        assert 'track_type' in field_names
        assert 'duration' in field_names
        assert 'sample_rate' in field_names
        assert 'channels' in field_names

        # Find audio_path field and check it's required
        audio_path_field = next(f for f in audio_fields if f.name == 'audio_path')
        assert audio_path_field.default == MISSING
        assert audio_path_field.default_factory == MISSING


class TestGalleryGenerationOutputWithAudio:
    """Tests for GalleryGenerationOutput with audio support."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_image = Image.new('RGB', (100, 100), color='red')
        self.test_audio_path = Path("/tmp/test_audio.wav")
        self.test_video_path = Path("/tmp/test_video.mp4")

    def test_default_empty_audio_list(self):
        """Test that audios list defaults to empty."""
        output = GalleryGenerationOutput(images=[])

        assert hasattr(output, 'audios')
        assert output.audios == []
        assert len(output.audios) == 0

    def test_gallery_with_audio_only(self):
        """Test gallery with only audio files."""
        audio_outputs = [
            AudioGenerationOutput(
                audio_path=Path("/tmp/vocal.wav"),
                track_type="vocal",
                temporary=False
            ),
            AudioGenerationOutput(
                audio_path=Path("/tmp/instrumental.wav"),
                track_type="instrumental",
                temporary=False
            ),
            AudioGenerationOutput(
                audio_path=Path("/tmp/mixed.wav"),
                track_type="mixed",
                temporary=False
            )
        ]

        output = GalleryGenerationOutput(images=[], audios=audio_outputs)

        assert len(output.images) == 0
        assert len(output.audios) == 3
        assert all(isinstance(a, AudioGenerationOutput) for a in output.audios)

    def test_gallery_with_images_only(self):
        """Test gallery with only images (backwards compatibility)."""
        image_outputs = [
            ImageGenerationOutput(image=self.test_image, temporary=False),
            ImageGenerationOutput(image=self.test_image, temporary=False)
        ]

        output = GalleryGenerationOutput(images=image_outputs)

        assert len(output.images) == 2
        assert len(output.audios) == 0
        assert len(output.videos) == 0

    def test_gallery_with_mixed_media(self):
        """Test gallery with images, videos, and audio."""
        image_outputs = [
            ImageGenerationOutput(image=self.test_image, temporary=False)
        ]
        video_outputs = [
            VideoGenerationOutput(video_path=self.test_video_path, temporary=False)
        ]
        audio_outputs = [
            AudioGenerationOutput(
                audio_path=self.test_audio_path,
                track_type="mixed",
                temporary=False
            )
        ]

        output = GalleryGenerationOutput(
            images=image_outputs,
            videos=video_outputs,
            audios=audio_outputs
        )

        assert len(output.images) == 1
        assert len(output.videos) == 1
        assert len(output.audios) == 1
        assert isinstance(output.images[0], ImageGenerationOutput)
        assert isinstance(output.videos[0], VideoGenerationOutput)
        assert isinstance(output.audios[0], AudioGenerationOutput)

    def test_multiple_audio_tracks(self):
        """Test gallery with multiple audio tracks per segment."""
        audio_outputs = []

        # Simulate 2 segments with 3 tracks each
        for segment in range(2):
            for track_type in ["vocal", "instrumental", "mixed"]:
                audio_outputs.append(AudioGenerationOutput(
                    audio_path=Path(f"/tmp/seg{segment}_{track_type}.wav"),
                    track_type=track_type,
                    segment=segment,
                    temporary=False
                ))

        output = GalleryGenerationOutput(images=[], audios=audio_outputs)

        assert len(output.audios) == 6  # 2 segments * 3 tracks

        # Check track types
        vocal_tracks = [a for a in output.audios if a.track_type == "vocal"]
        instrumental_tracks = [a for a in output.audios if a.track_type == "instrumental"]
        mixed_tracks = [a for a in output.audios if a.track_type == "mixed"]

        assert len(vocal_tracks) == 2
        assert len(instrumental_tracks) == 2
        assert len(mixed_tracks) == 2

    def test_audio_with_metadata(self):
        """Test that audio metadata is preserved in gallery."""
        audio_output = AudioGenerationOutput(
            audio_path=self.test_audio_path,
            track_type="vocal",
            temporary=False,
            duration=30.5,
            sample_rate=16000,
            channels=2,
            temperature=1.0,
            top_p=0.93,
            guidance_scale=1.5,
            segment=0
        )

        output = GalleryGenerationOutput(images=[], audios=[audio_output])

        stored_audio = output.audios[0]
        assert stored_audio.duration == 30.5
        assert stored_audio.sample_rate == 16000
        assert stored_audio.channels == 2
        assert stored_audio.temperature == 1.0
        assert stored_audio.top_p == 0.93
        assert stored_audio.guidance_scale == 1.5
        assert stored_audio.segment == 0

    def test_gallery_audios_list_type(self):
        """Test that audios attribute is a list."""
        output = GalleryGenerationOutput(images=[])

        assert isinstance(output.audios, list)

    def test_empty_gallery_all_types(self):
        """Test that all media type lists can be empty."""
        output = GalleryGenerationOutput(images=[], videos=[], audios=[])

        assert len(output.images) == 0
        assert len(output.videos) == 0
        assert len(output.audios) == 0

    def test_gallery_with_temporary_and_final_audio(self):
        """Test mixing temporary and final audio outputs."""
        audio_outputs = [
            AudioGenerationOutput(
                audio_path=Path("/tmp/preview.wav"),
                track_type="mixed",
                temporary=True
            ),
            AudioGenerationOutput(
                audio_path=Path("/tmp/final_vocal.wav"),
                track_type="vocal",
                temporary=False
            ),
            AudioGenerationOutput(
                audio_path=Path("/tmp/final_mixed.wav"),
                track_type="mixed",
                temporary=False
            )
        ]

        output = GalleryGenerationOutput(images=[], audios=audio_outputs)

        assert len(output.audios) == 3
        temporary_count = sum(1 for a in output.audios if a.temporary)
        final_count = sum(1 for a in output.audios if not a.temporary)

        assert temporary_count == 1
        assert final_count == 2


class TestAudioOutputInheritance:
    """Tests for AudioGenerationOutput inheritance structure."""

    def test_inherits_from_generation_output(self):
        """Test that AudioGenerationOutput inherits from GenerationOutput."""
        audio_path = Path("/tmp/test.wav")
        output = AudioGenerationOutput(audio_path=audio_path)

        assert isinstance(output, GenerationOutput)
        assert isinstance(output, AudioGenerationOutput)

    def test_has_base_attributes(self):
        """Test that inherited attributes are accessible."""
        audio_path = Path("/tmp/test.wav")
        output = AudioGenerationOutput(audio_path=audio_path)

        # Should have inherited fields
        assert hasattr(output, 'type')
        assert hasattr(output, 'pipe_id')
        assert hasattr(output, 'pipe_name')

        # Should have own fields
        assert hasattr(output, 'audio_path')
        assert hasattr(output, 'track_type')

    def test_type_attribute(self):
        """Test that type attribute is inherited."""
        audio_path = Path("/tmp/test.wav")
        output = AudioGenerationOutput(audio_path=audio_path)

        assert hasattr(output, 'type')
        assert output.type == "generation_output"


class TestAudioOutputEdgeCases:
    """Tests for edge cases and error handling."""

    def test_very_long_duration(self):
        """Test with very long audio duration."""
        output = AudioGenerationOutput(
            audio_path=Path("/tmp/long.wav"),
            duration=3600.0  # 1 hour
        )

        assert output.duration == 3600.0

    def test_zero_duration(self):
        """Test with zero duration."""
        output = AudioGenerationOutput(
            audio_path=Path("/tmp/empty.wav"),
            duration=0.0
        )

        assert output.duration == 0.0

    def test_high_sample_rate(self):
        """Test with high sample rate."""
        output = AudioGenerationOutput(
            audio_path=Path("/tmp/hires.wav"),
            sample_rate=192000  # 192kHz
        )

        assert output.sample_rate == 192000

    def test_multichannel_audio(self):
        """Test with multichannel audio (5.1, 7.1, etc.)."""
        output = AudioGenerationOutput(
            audio_path=Path("/tmp/surround.wav"),
            channels=6  # 5.1 surround
        )

        assert output.channels == 6

    def test_extreme_temperature(self):
        """Test with extreme temperature values."""
        # Low temperature (more deterministic)
        output_low = AudioGenerationOutput(
            audio_path=Path("/tmp/low_temp.wav"),
            temperature=0.1
        )
        assert output_low.temperature == 0.1

        # High temperature (more random)
        output_high = AudioGenerationOutput(
            audio_path=Path("/tmp/high_temp.wav"),
            temperature=2.0
        )
        assert output_high.temperature == 2.0

    def test_guidance_scale_range(self):
        """Test with various guidance scale values."""
        values = [1.0, 1.5, 2.0, 3.0]

        for scale in values:
            output = AudioGenerationOutput(
                audio_path=Path(f"/tmp/scale_{scale}.wav"),
                guidance_scale=scale
            )
            assert output.guidance_scale == scale

    def test_many_segments(self):
        """Test with many audio segments."""
        outputs = []
        for i in range(10):
            output = AudioGenerationOutput(
                audio_path=Path(f"/tmp/segment_{i}.wav"),
                segment=i
            )
            outputs.append(output)

        assert len(outputs) == 10
        assert all(outputs[i].segment == i for i in range(10))
