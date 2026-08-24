import unittest
from unittest.mock import Mock, patch, MagicMock
from PIL import Image
import base64
import io

from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.media_utils import create_base64_image
from src.features.generation.output_types import output_type_registry
from src.pipelines.outputs import (
    ImageGenerationOutput, ProgressGenerationOutput, GalleryGenerationOutput,
    TimerGenerationOutput, CompareImagesGenerationOutput, SeedGenerationOutput,
    DiffTextGenerationOutput, ModelsGenerationOutput, ModelGenerationOutput, GenerationOutput,
    AudioGenerationOutput, VideoGenerationOutput, ErrorGenerationOutput
)


class TestGenerationOutputSerializer(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.generation_id = "test_gen_123"
        self.preset_id = "test_preset"
        self.mapper = GenerationOutputSerializer(
            generation_id=self.generation_id,
            preset_id=self.preset_id
        )
        
    def test_init(self):
        """Test mapper initialization"""
        mapper = GenerationOutputSerializer()
        self.assertIsNotNone(mapper.generation_id)

        mapper_with_ids = GenerationOutputSerializer(
            generation_id="custom_id",
            preset_id="custom_preset"
        )
        self.assertEqual(mapper_with_ids.generation_id, "custom_id")
        self.assertEqual(mapper_with_ids.preset_id, "custom_preset")

    def test_get_message_type(self):
        """Test message type resolution via the output_type_registry"""
        def message_type_for(output):
            spec = output_type_registry.spec_for(output)
            return spec.resolve_message_type(output) if spec else "generation_update"

        # Test known output types
        self.assertEqual(
            message_type_for(ImageGenerationOutput(image=None)),
            "workbench_update"
        )
        self.assertEqual(
            message_type_for(ProgressGenerationOutput(state="running")),
            "generation_status"
        )
        self.assertEqual(
            message_type_for(GalleryGenerationOutput(images=[])),
            "gallery_update"
        )
        self.assertEqual(
            message_type_for(SeedGenerationOutput(index=0, seed=123)),
            "pipe_artifact"
        )

        # Test unknown output type
        class UnknownOutput(GenerationOutput):
            pass
        self.assertEqual(
            message_type_for(UnknownOutput()),
            "generation_update"
        )

    def test_serialize_image_output(self):
        """Test image output serialization"""
        # Create a test image
        test_image = Image.new('RGB', (100, 100), color='red')
        
        # Test with temporary image
        output = ImageGenerationOutput(
            image=test_image,
            temporary=True,
            seed=12345,
            resolution="512x512",
            sampler="euler",
            cfg=7.5,
            denoise=0.75,
            step=20
        )
        output.pipe_id = 3
        output.pipe_name = "generator"
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'workbench_update')
        self.assertEqual(result['generation_id'], self.generation_id)
        self.assertEqual(result['pipe_id'], 3)
        self.assertEqual(result['pipe_name'], 'generator')
        self.assertTrue(result['temporary'])
        self.assertEqual(result['seed'], 12345)
        self.assertEqual(result['resolution'], "512x512")
        self.assertIn('image', result)
        self.assertIsNotNone(result['image'])
        
        # Test with non-temporary image and saved path
        output.temporary = False
        output._saved_path = "outputs/2025-01-26/test_gen_123/0.png"

        result = self.mapper.serialize_output(output)

        self.assertFalse(result['temporary'])
        self.assertEqual(result['path'], "/api/media/generations/test_gen_123/0.png")
        
    def test_serialize_progress_output(self):
        """Test progress output serialization"""
        # Mock Progress object
        mock_progress = Mock()
        mock_progress.current = 50
        mock_progress.max = 100
        
        output = ProgressGenerationOutput(
            state="Generating image",
            title="Step 1 of 3",
            progress=mock_progress
        )
        output.pipe_id = 3
        output.pipe_name = "generator"
        output.current_step_num = 1
        output.total_steps = 3
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'generation_status')
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['current_step'], 'Generating image')
        self.assertEqual(result['message'], 'Step 1 of 3')
        self.assertEqual(result['progress'], 0.5)
        self.assertEqual(result['current_step_num'], 1)
        self.assertEqual(result['total_steps'], 3)

    def test_serialize_progress_output_without_a_fraction_stays_none(self):
        """A stage with no progress fraction yet (e.g. a cold model load with
        no per-component breakdown) must serialize to `progress: None`, not
        0.0 - the frontend needs to tell "unknown, still running" apart from
        a real 0% to render an indeterminate bar instead of a stuck one."""
        output = ProgressGenerationOutput(state="Loading Maya model <<MODEL:maya1>>")

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'generation_status')
        self.assertEqual(result['status'], 'running')
        self.assertIsNone(result['progress'])

    def test_serialize_error_output_with_detail(self):
        """ErrorGenerationOutput serializes to generation_error with the detail body."""
        output = ErrorGenerationOutput(
            error="KSampler: CUDA out of memory",
            detail="Node 12 (KSampler)\nRuntimeError: CUDA out of memory\n  File ...",
        )
        output.pipe_id = 4
        output.pipe_name = "generator"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'generation_error')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], "KSampler: CUDA out of memory")
        self.assertEqual(
            result['detail'],
            "Node 12 (KSampler)\nRuntimeError: CUDA out of memory\n  File ...",
        )
        self.assertEqual(result['pipe_id'], 4)

    def test_serialize_error_output_without_detail(self):
        """Detail is omitted from the message when the output carries none."""
        output = ErrorGenerationOutput(error="Something went wrong")

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'generation_error')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], "Something went wrong")
        self.assertNotIn('detail', result)

    def test_serialize_gallery_output(self):
        """Test gallery output serialization"""
        # Create test images
        img1 = Image.new('RGB', (100, 100), color='red')
        img2 = Image.new('RGB', (100, 100), color='blue')
        
        img_output1 = ImageGenerationOutput(image=img1, temporary=False)
        img_output1._saved_path = "outputs/2025-01-26/test_gen_123/0.png"
        
        img_output2 = ImageGenerationOutput(image=img2, temporary=False)
        img_output2._saved_path = "outputs/2025-01-26/test_gen_123/1.png"
        
        output = GalleryGenerationOutput(images=[img_output1, img_output2])
        output.pipe_id = 7
        output.pipe_name = "gallery"
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'gallery_update')
        self.assertEqual(len(result['images']), 2)
        self.assertEqual(len(result['image_urls_list']), 2)
        self.assertEqual(result['image_urls_list'][0]['original'], "/api/media/generations/test_gen_123/0.png")
        self.assertEqual(result['image_urls_list'][1]['original'], "/api/media/generations/test_gen_123/1.png")
        self.assertIs(result['image_urls_list'][0]['derived'], False)
        self.assertIs(result['image_urls_list'][1]['derived'], False)

    def test_serialize_gallery_output_carries_derived_flag(self):
        """A derived gallery emit (e.g. an enhance pass) flags every item."""
        img = Image.new('RGB', (100, 100), color='green')
        img_output = ImageGenerationOutput(image=img, temporary=False, derived=True)
        img_output._saved_path = "outputs/2025-01-26/test_gen_123/1.png"

        video_output = VideoGenerationOutput(
            video_path="/tmp/out.mp4", temporary=False, derived=True
        )
        video_output._saved_path = "outputs/2025-01-26/test_gen_123/2.mp4"

        output = GalleryGenerationOutput(images=[img_output], videos=[video_output])
        output.pipe_id = 9
        output.pipe_name = "gallery"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'gallery_update')
        self.assertIs(result['image_urls_list'][0]['derived'], True)
        self.assertIs(result['videos'][0]['derived'], True)
        self.assertIs(result['video_urls_list'][0]['derived'], True)

    def test_serialize_seed_output(self):
        """Test seed output serialization"""
        output = SeedGenerationOutput(index=0, seed=123456789)
        output.pipe_id = 2
        output.pipe_name = "seed_generator"
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'pipe_artifact')
        self.assertEqual(result['artifact_type'], 'seed')
        self.assertEqual(result['artifact_data']['seed'], 123456789)
        
    def test_serialize_models_output(self):
        """Test models output serialization"""
        model1 = ModelGenerationOutput(name="model1.safetensors", type="checkpoint", weight=1.0)
        model2 = ModelGenerationOutput(name="lora2.safetensors", type="lora", weight=0.5)
        
        output = ModelsGenerationOutput(models=[model1, model2])
        output.pipe_id = 1
        output.pipe_name = "checkpoint_loader"
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'pipe_artifact')
        self.assertEqual(result['artifact_type'], 'models')
        self.assertEqual(len(result['artifact_data']['models']), 2)
        self.assertEqual(result['artifact_data']['models'][0]['name'], 'model1.safetensors')
        self.assertEqual(result['artifact_data']['models'][0]['type'], 'checkpoint')
        self.assertEqual(result['artifact_data']['models'][0]['weight'], 1.0)
        
    def test_serialize_compare_images_output(self):
        """Test compare images output serialization"""
        img1 = Image.new('RGB', (100, 100), color='red')
        img2 = Image.new('RGB', (100, 100), color='blue')
        
        output = CompareImagesGenerationOutput(
            index=0,
            compare=("Original", img1),
            to=("Upscaled", img2)
        )
        output.pipe_id = 5
        output.pipe_name = "upscaler"
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'pipe_artifact')
        self.assertEqual(result['artifact_type'], 'compare_images')
        self.assertIn('compare_image', result['artifact_data'])
        self.assertIn('compare_label', result['artifact_data'])
        self.assertEqual(result['artifact_data']['compare_label'], 'Original')
        self.assertIn('to_image', result['artifact_data'])
        self.assertIn('to_label', result['artifact_data'])
        self.assertEqual(result['artifact_data']['to_label'], 'Upscaled')
        
    def test_create_base64_image(self):
        """Test base64 image creation with resizing"""
        # Test small image (no resize needed)
        small_img = Image.new('RGB', (100, 100), color='red')
        base64_str = create_base64_image(small_img)
        self.assertIsNotNone(base64_str)
        self.assertIsInstance(base64_str, str)

        # Test large image (should be resized)
        large_img = Image.new('RGB', (2000, 2000), color='blue')
        base64_str = create_base64_image(large_img, max_dimension=768)
        self.assertIsNotNone(base64_str)

        # Decode and check dimensions
        img_data = base64.b64decode(base64_str)
        img = Image.open(io.BytesIO(img_data))
        self.assertLessEqual(img.width, 768)
        self.assertLessEqual(img.height, 768)

        # Test RGBA image conversion
        rgba_img = Image.new('RGBA', (100, 100), color=(255, 0, 0, 128))
        base64_str = create_base64_image(rgba_img)
        self.assertIsNotNone(base64_str)

    def test_serialize_output_error_handling(self):
        """Test error handling in serialize_output"""
        # Create an output that will cause an error
        output = ImageGenerationOutput(image=None)
        output.image = "not_an_image"  # Invalid image type
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'workbench_update')
        self.assertIsNone(result.get('image'))
        
    def test_timer_output_serialization(self):
        """Test timer output serialization"""
        output = TimerGenerationOutput(
            name="generation_time",
            value=5.23,
            unit="s"
        )
        output.pipe_id = 8
        output.pipe_name = "timer"
        
        result = self.mapper.serialize_output(output)
        
        self.assertEqual(result['type'], 'timer_update')
        self.assertEqual(result['timer_name'], 'generation_time')
        self.assertEqual(result['timer_value'], 5.23)
        self.assertEqual(result['timer_unit'], 's')
        self.assertEqual(result['formatted_time'], '5.23s')
        
    def test_diff_text_output_serialization(self):
        """Test diff text output serialization"""
        output = DiffTextGenerationOutput(
            index=0,
            name="prompt_diff",
            diff="+ Added text\n- Removed text"
        )
        output.pipe_id = 4
        output.pipe_name = "prompt_processor"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'pipe_artifact')
        self.assertEqual(result['artifact_type'], 'diff_text')
        self.assertEqual(result['artifact_data']['name'], 'prompt_diff')
        self.assertEqual(result['artifact_data']['diff'], '+ Added text\n- Removed text')
        self.assertTrue(result['artifact_data']['negative_applied'])

    def test_diff_text_output_marks_inert_negative(self):
        """An inert negative diff carries negative_applied False."""
        output = DiffTextGenerationOutput(
            index=0,
            name="Negative Prompt",
            diff="- blurry",
            negative_applied=False,
        )
        output.pipe_id = 4
        output.pipe_name = "prompt_encoder"

        result = self.mapper.serialize_output(output)

        self.assertFalse(result['artifact_data']['negative_applied'])

    def test_audio_output_serialization_temporary(self):
        """Test temporary audio output serialization"""
        from pathlib import Path

        output = AudioGenerationOutput(
            audio_path=Path("/tmp/audio.wav"),
            temporary=True,
            track_type="mixed",
            sample_rate=16000,
            channels=2,
            duration=30.5
        )
        output.pipe_id = 5
        output.pipe_name = "generator_maya"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'workbench_update')
        self.assertEqual(result['generation_id'], self.generation_id)
        self.assertEqual(result['pipe_id'], 5)
        self.assertEqual(result['pipe_name'], 'generator_maya')
        self.assertTrue(result['temporary'])
        self.assertEqual(result['track_type'], 'mixed')
        self.assertEqual(result['sample_rate'], 16000)
        self.assertEqual(result['channels'], 2)
        self.assertEqual(result['duration'], 30.5)
        # Temporary audio has path to tmp directory
        self.assertIn('path', result)
        self.assertTrue(result['path'].startswith('/api/media/tmp/'))

    def test_audio_output_serialization_permanent(self):
        """Test permanent audio output serialization with saved path"""
        from pathlib import Path

        output = AudioGenerationOutput(
            audio_path=Path("/tmp/audio.wav"),
            temporary=False,
            track_type="vocal",
            sample_rate=16000,
            channels=2,
            duration=45.2,
            temperature=1.0,
            top_p=0.93,
            guidance_scale=1.5,
            segment=0
        )
        output.pipe_id = 5
        output.pipe_name = "generator_maya"
        output._saved_path = "generations/2025-01-26/test_gen_123/0_vocal.wav"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'workbench_update')
        self.assertFalse(result['temporary'])
        self.assertEqual(result['track_type'], 'vocal')
        self.assertEqual(result['path'], "/api/media/generations/test_gen_123/0_vocal.wav")
        self.assertEqual(result['sample_rate'], 16000)
        self.assertEqual(result['channels'], 2)
        self.assertEqual(result['duration'], 45.2)
        self.assertEqual(result['temperature'], 1.0)
        self.assertEqual(result['top_p'], 0.93)
        self.assertEqual(result['guidance_scale'], 1.5)
        self.assertEqual(result['segment'], 0)

    def test_audio_output_all_track_types(self):
        """Test serialization of all audio track types"""
        from pathlib import Path

        track_types = ["vocal", "instrumental", "mixed"]

        for track_type in track_types:
            output = AudioGenerationOutput(
                audio_path=Path(f"/tmp/{track_type}.wav"),
                temporary=False,
                track_type=track_type
            )
            output.pipe_id = 5
            output.pipe_name = "generator_maya"
            output._saved_path = f"generations/test/{track_type}.wav"

            result = self.mapper.serialize_output(output)

            self.assertEqual(result['track_type'], track_type)
            self.assertEqual(result['path'], f"/api/media/generations/test_gen_123/{track_type}.wav")

    def test_gallery_output_with_audio(self):
        """Test gallery output serialization with audio files"""
        from pathlib import Path

        # Create test image
        img = Image.new('RGB', (100, 100), color='red')
        img_output = ImageGenerationOutput(image=img, temporary=False)
        img_output._saved_path = "outputs/2025-01-26/test_gen_123/0.png"

        # Create test audio outputs
        audio_output1 = AudioGenerationOutput(
            audio_path=Path("/tmp/vocal.wav"),
            temporary=False,
            track_type="vocal"
        )
        audio_output1._saved_path = "generations/2025-01-26/test_gen_123/1_vocal.wav"

        audio_output2 = AudioGenerationOutput(
            audio_path=Path("/tmp/mixed.wav"),
            temporary=False,
            track_type="mixed"
        )
        audio_output2._saved_path = "generations/2025-01-26/test_gen_123/2_mixed.wav"

        output = GalleryGenerationOutput(
            images=[img_output],
            audios=[audio_output1, audio_output2]
        )
        output.pipe_id = 7
        output.pipe_name = "gallery"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'gallery_update')
        self.assertEqual(len(result['images']), 1)
        self.assertEqual(len(result['audio_urls_list']), 2)
        self.assertEqual(result['audio_urls_list'][0]['path'], "/api/media/generations/test_gen_123/1_vocal.wav")
        self.assertEqual(result['audio_urls_list'][0]['track_type'], "vocal")
        self.assertEqual(result['audio_urls_list'][1]['path'], "/api/media/generations/test_gen_123/2_mixed.wav")
        self.assertEqual(result['audio_urls_list'][1]['track_type'], "mixed")

    def test_gallery_output_with_mixed_media(self):
        """Test gallery with images, videos, and audio"""
        from pathlib import Path

        # Create test image
        img = Image.new('RGB', (100, 100), color='red')
        img_output = ImageGenerationOutput(image=img, temporary=False)
        img_output._saved_path = "outputs/2025-01-26/test_gen_123/0.png"

        # Create test video
        video_output = VideoGenerationOutput(
            video_path=Path("/tmp/video.mp4"),
            temporary=False
        )
        video_output._saved_path = "generations/test/1.mp4"

        # Create test audio
        audio_output = AudioGenerationOutput(
            audio_path=Path("/tmp/audio.wav"),
            temporary=False,
            track_type="mixed"
        )
        audio_output._saved_path = "generations/test/2_mixed.wav"

        output = GalleryGenerationOutput(
            images=[img_output],
            videos=[video_output],
            audios=[audio_output]
        )
        output.pipe_id = 7
        output.pipe_name = "gallery"

        result = self.mapper.serialize_output(output)

        self.assertEqual(result['type'], 'gallery_update')
        self.assertEqual(len(result['images']), 1)
        self.assertEqual(len(result['video_urls_list']), 1)
        self.assertEqual(len(result['audio_urls_list']), 1)
        # Images use 'original' key, videos and audio use 'path' key
        self.assertEqual(result['image_urls_list'][0]['original'], "/api/media/generations/test_gen_123/0.png")
        self.assertEqual(result['video_urls_list'][0]['path'], "/api/media/generations/test_gen_123/1.mp4")
        self.assertEqual(result['audio_urls_list'][0]['path'], "/api/media/generations/test_gen_123/2_mixed.wav")

    def test_audio_output_metadata_preservation(self):
        """Test that audio metadata is preserved in serialization"""
        from pathlib import Path

        output = AudioGenerationOutput(
            audio_path=Path("/tmp/test.wav"),
            temporary=False,
            track_type="instrumental",
            sample_rate=44100,
            channels=2,
            duration=120.5,
            temperature=1.2,
            top_p=0.95,
            guidance_scale=2.0,
            segment=3
        )
        output.pipe_id = 5
        output.pipe_name = "generator_maya"
        output._saved_path = "generations/test/audio.wav"

        result = self.mapper.serialize_output(output)

        # Verify all metadata fields
        self.assertEqual(result['sample_rate'], 44100)
        self.assertEqual(result['channels'], 2)
        self.assertEqual(result['duration'], 120.5)
        self.assertEqual(result['temperature'], 1.2)
        self.assertEqual(result['top_p'], 0.95)
        self.assertEqual(result['guidance_scale'], 2.0)
        self.assertEqual(result['segment'], 3)


if __name__ == '__main__':
    unittest.main()