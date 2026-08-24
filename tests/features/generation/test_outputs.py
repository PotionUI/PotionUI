import io
import pytest
from PIL import Image
from dataclasses import dataclass, fields, MISSING

from src.pipelines.outputs import (
    GenerationOutput, ImageGenerationOutput, ProgressGenerationOutput,
    CompareImagesGenerationOutput, TimerGenerationOutput, GalleryGenerationOutput,
    SeedGenerationOutput, DiffTextGenerationOutput, ModelGenerationOutput,
    ModelsGenerationOutput
)
from src.pipelines.outputs import Icon, Progress


class TestGenerationOutput:
    def test_base_class_attributes(self):
        """Test that GenerationOutput has correct base attributes."""
        output = GenerationOutput()
        
        assert hasattr(output, 'type')
        assert output.type == "generation_output"
        assert output.pipe_id is None
        assert output.pipe_name is None

    def test_pipe_tracking_attributes(self):
        """Test that pipe tracking attributes can be set."""
        output = GenerationOutput()
        
        output.pipe_id = 5
        output.pipe_name = "test_pipe"
        
        assert output.pipe_id == 5
        assert output.pipe_name == "test_pipe"

    def test_dataclass_functionality(self):
        """Test that GenerationOutput works as a dataclass."""
        output = GenerationOutput(pipe_id=3, pipe_name="generator")
        
        assert output.pipe_id == 3
        assert output.pipe_name == "generator"

    def test_inheritance_structure(self):
        """Test that other outputs inherit from GenerationOutput."""
        test_image = Image.new('RGB', (100, 100), color='red')
        image_output = ImageGenerationOutput(image=test_image)
        
        assert isinstance(image_output, GenerationOutput)
        assert hasattr(image_output, 'pipe_id')
        assert hasattr(image_output, 'pipe_name')


class TestImageGenerationOutput:
    def setup_method(self):
        self.test_image = Image.new('RGB', (100, 100), color='red')

    def test_required_attributes(self):
        """Test that ImageGenerationOutput requires an image."""
        output = ImageGenerationOutput(image=self.test_image)
        
        assert output.image == self.test_image
        assert isinstance(output, GenerationOutput)

    def test_default_values(self):
        """Test default values for optional attributes."""
        output = ImageGenerationOutput(image=self.test_image)
        
        assert output.temporary is True
        assert output.seed is None
        assert output.resolution is None
        assert output.sampler is None
        assert output.clip_skip is None
        assert output.cfg is None
        assert output.denoise is None
        assert output.step is None

    def test_custom_values(self):
        """Test setting custom values for all attributes."""
        output = ImageGenerationOutput(
            image=self.test_image,
            temporary=False,
            seed=12345,
            resolution=(512, 768),
            sampler="DPM++ 2M",
            clip_skip=2,
            cfg=7.5,
            denoise=0.8,
            step=25
        )
        
        assert output.image == self.test_image
        assert output.temporary is False
        assert output.seed == 12345
        assert output.resolution == (512, 768)
        assert output.sampler == "DPM++ 2M"
        assert output.clip_skip == 2
        assert output.cfg == 7.5
        assert output.denoise == 0.8
        assert output.step == 25

    def test_resolution_tuple(self):
        """Test that resolution is properly stored as tuple."""
        output = ImageGenerationOutput(
            image=self.test_image,
            resolution=(1024, 1024)
        )
        
        assert isinstance(output.resolution, tuple)
        assert len(output.resolution) == 2
        assert output.resolution[0] == 1024
        assert output.resolution[1] == 1024

    def test_numeric_types(self):
        """Test that numeric attributes accept appropriate types."""
        output = ImageGenerationOutput(
            image=self.test_image,
            seed=42,
            clip_skip=1,
            cfg=7.0,
            denoise=1.0,
            step=20
        )
        
        assert isinstance(output.seed, int)
        assert isinstance(output.clip_skip, int)
        assert isinstance(output.cfg, (int, float))
        assert isinstance(output.denoise, (int, float))
        assert isinstance(output.step, int)

    def test_lazy_file_backed_image_is_eagerly_decoded(self):
        """
        A freshly Image.open()-ed image is lazy (only the header is read; .fp
        stays set until .load() runs). Pipes run on a worker thread while the
        output handlers that touch .image run on the event-loop thread, so a
        lazy image would be decoded by two threads at once. Construction must
        force the decode so no lazy state escapes the producing thread.
        """
        buf = io.BytesIO()
        Image.new('RGB', (64, 64), color='green').save(buf, format='PNG')
        buf.seek(0)
        lazy_image = Image.open(buf)
        assert lazy_image.fp is not None  # sanity: still lazy before construction

        output = ImageGenerationOutput(image=lazy_image)

        assert output.image.fp is None
        assert output.image.im is not None

    def test_in_memory_image_passes_through_untouched(self):
        """An already-decoded, non-file-backed image (Image.new) must not raise
        and must be left as-is (no fp to worry about)."""
        in_memory_image = Image.new('RGB', (32, 32), color='blue')

        output = ImageGenerationOutput(image=in_memory_image)

        assert output.image is in_memory_image
        assert getattr(output.image, 'fp', None) is None

    def test_none_image_does_not_raise(self):
        """image=None must be a no-op, not an error."""
        output = ImageGenerationOutput(image=None)

        assert output.image is None


class TestProgressGenerationOutput:
    def test_required_attributes(self):
        """Test that ProgressGenerationOutput requires state."""
        output = ProgressGenerationOutput(state="Processing...")
        
        assert output.state == "Processing..."
        assert isinstance(output, GenerationOutput)

    def test_default_values(self):
        """Test default values for optional attributes."""
        output = ProgressGenerationOutput(state="Test state")
        
        assert output.icon is None
        assert output.title is None
        assert output.progress is None

    def test_with_icon(self):
        """Test setting icon attribute."""
        icon = Icon("gear", "spin")
        output = ProgressGenerationOutput(
            state="Loading model...",
            icon=icon
        )
        
        assert output.icon == icon
        assert output.icon.name == "gear"
        assert output.icon.effect == "spin"

    def test_with_title(self):
        """Test setting title attribute."""
        output = ProgressGenerationOutput(
            state="Generating image",
            title="<<PIPE:generator>>"
        )
        
        assert output.title == "<<PIPE:generator>>"

    def test_with_progress(self):
        """Test setting progress attribute."""
        progress = Progress(current=5, max=10)
        output = ProgressGenerationOutput(
            state="Step 5 of 10",
            progress=progress
        )
        
        assert output.progress == progress
        assert output.progress.current == 5
        assert output.progress.max == 10

    def test_complete_output(self):
        """Test ProgressGenerationOutput with all attributes."""
        icon = Icon("check", "beat")
        progress = Progress(current=10, max=10)
        
        output = ProgressGenerationOutput(
            state="Generation completed",
            icon=icon,
            title="<<PIPE:generator>>",
            progress=progress
        )
        
        assert output.state == "Generation completed"
        assert output.icon == icon
        assert output.title == "<<PIPE:generator>>"
        assert output.progress == progress


class TestCompareImagesGenerationOutput:
    def setup_method(self):
        self.image1 = Image.new('RGB', (100, 100), color='red')
        self.image2 = Image.new('RGB', (100, 100), color='blue')

    def test_required_attributes(self):
        """Test that CompareImagesGenerationOutput requires all attributes."""
        output = CompareImagesGenerationOutput(
            index=0,
            compare=("Original", self.image1),
            to=("Generated", self.image2)
        )
        
        assert output.index == 0
        assert output.compare == ("Original", self.image1)
        assert output.to == ("Generated", self.image2)
        assert isinstance(output, GenerationOutput)

    def test_type_attribute(self):
        """Test that type is set to artifact_output."""
        output = CompareImagesGenerationOutput(
            index=1,
            compare=("A", self.image1),
            to=("B", self.image2)
        )
        
        assert output.type == "artifact_output"

    def test_tuple_structure(self):
        """Test that compare and to are proper tuples."""
        output = CompareImagesGenerationOutput(
            index=0,
            compare=("Label1", self.image1),
            to=("Label2", self.image2)
        )
        
        # Test compare tuple
        assert isinstance(output.compare, tuple)
        assert len(output.compare) == 2
        assert isinstance(output.compare[0], str)
        assert isinstance(output.compare[1], Image.Image)
        
        # Test to tuple
        assert isinstance(output.to, tuple)
        assert len(output.to) == 2
        assert isinstance(output.to[0], str)
        assert isinstance(output.to[1], Image.Image)

    def test_different_indices(self):
        """Test with different index values."""
        for i in range(5):
            output = CompareImagesGenerationOutput(
                index=i,
                compare=(f"Compare {i}", self.image1),
                to=(f"To {i}", self.image2)
            )
            assert output.index == i


class TestTimerGenerationOutput:
    def test_required_attributes(self):
        """Test that TimerGenerationOutput requires name and value."""
        output = TimerGenerationOutput(name="test_timer", value=1.5)
        
        assert output.name == "test_timer"
        assert output.value == 1.5
        assert isinstance(output, GenerationOutput)

    def test_default_unit(self):
        """Test that unit defaults to seconds."""
        output = TimerGenerationOutput(name="timer", value=2.0)
        
        assert output.unit == "s"

    def test_custom_units(self):
        """Test setting different time units."""
        units = ["s", "ms", "m", "h"]
        
        for unit in units:
            output = TimerGenerationOutput(
                name="test_timer",
                value=1.0,
                unit=unit
            )
            assert output.unit == unit

    def test_numeric_values(self):
        """Test with different numeric value types."""
        # Integer value
        output1 = TimerGenerationOutput(name="timer1", value=5)
        assert output1.value == 5
        assert isinstance(output1.value, int)
        
        # Float value
        output2 = TimerGenerationOutput(name="timer2", value=3.14159)
        assert output2.value == 3.14159
        assert isinstance(output2.value, float)

    def test_timer_names(self):
        """Test with different timer names."""
        names = [
            "generation.total",
            "pipe.generator.process",
            "model.load",
            "wb.timers.pipes.upscaler"
        ]
        
        for name in names:
            output = TimerGenerationOutput(name=name, value=1.0)
            assert output.name == name


class TestGalleryGenerationOutput:
    def setup_method(self):
        self.image1 = Image.new('RGB', (100, 100), color='red')
        self.image2 = Image.new('RGB', (100, 100), color='blue')

    def test_required_attributes(self):
        """Test that GalleryGenerationOutput requires images list."""
        images = [
            ImageGenerationOutput(image=self.image1),
            ImageGenerationOutput(image=self.image2)
        ]
        output = GalleryGenerationOutput(images=images)
        
        assert output.images == images
        assert isinstance(output, GenerationOutput)

    def test_empty_gallery(self):
        """Test with empty images list."""
        output = GalleryGenerationOutput(images=[])
        
        assert output.images == []
        assert len(output.images) == 0

    def test_single_image_gallery(self):
        """Test with single image in gallery."""
        image_output = ImageGenerationOutput(image=self.image1, temporary=False)
        output = GalleryGenerationOutput(images=[image_output])
        
        assert len(output.images) == 1
        assert output.images[0] == image_output

    def test_multiple_images_gallery(self):
        """Test with multiple images in gallery."""
        images = [
            ImageGenerationOutput(image=self.image1, temporary=False),
            ImageGenerationOutput(image=self.image2, temporary=True),
            ImageGenerationOutput(image=self.image1, seed=12345)
        ]
        output = GalleryGenerationOutput(images=images)
        
        assert len(output.images) == 3
        assert all(isinstance(img, ImageGenerationOutput) for img in output.images)

    def test_images_list_type(self):
        """Test that images attribute is a list."""
        output = GalleryGenerationOutput(images=[])
        
        assert isinstance(output.images, list)


class TestSeedGenerationOutput:
    def test_required_attributes(self):
        """Test that SeedGenerationOutput requires index and seed."""
        output = SeedGenerationOutput(index=0, seed=12345)
        
        assert output.index == 0
        assert output.seed == 12345
        assert isinstance(output, GenerationOutput)

    def test_type_attribute(self):
        """Test that type is set to artifact_output."""
        output = SeedGenerationOutput(index=1, seed=67890)
        
        assert output.type == "artifact_output"

    def test_different_values(self):
        """Test with different index and seed values."""
        test_cases = [
            (0, 123456789),
            (1, 987654321),
            (5, 0),
            (10, -1)  # Test negative seeds
        ]
        
        for index, seed in test_cases:
            output = SeedGenerationOutput(index=index, seed=seed)
            assert output.index == index
            assert output.seed == seed


class TestDiffTextGenerationOutput:
    def test_required_attributes(self):
        """Test that DiffTextGenerationOutput requires all attributes."""
        diff_text = "- old line\n+ new line"
        output = DiffTextGenerationOutput(
            index=0,
            name="prompt_diff",
            diff=diff_text
        )
        
        assert output.index == 0
        assert output.name == "prompt_diff"
        assert output.diff == diff_text
        assert isinstance(output, GenerationOutput)

    def test_type_attribute(self):
        """Test that type is set to artifact_output."""
        output = DiffTextGenerationOutput(
            index=1,
            name="test_diff",
            diff="test diff content"
        )
        
        assert output.type == "artifact_output"

    def test_different_diff_types(self):
        """Test with different types of diff content."""
        diff_examples = [
            "",  # Empty diff
            "No changes",  # Simple text
            "- removed line\n+ added line\n  unchanged line",  # Multi-line diff
            "@@changed@@content@@with@@special@@chars@@"  # Special characters
        ]
        
        for i, diff in enumerate(diff_examples):
            output = DiffTextGenerationOutput(
                index=i,
                name=f"diff_{i}",
                diff=diff
            )
            assert output.diff == diff

    def test_name_variations(self):
        """Test with different name values."""
        names = [
            "prompt_diff",
            "negative_prompt_diff",
            "config_diff",
            "parameters_diff"
        ]
        
        for i, name in enumerate(names):
            output = DiffTextGenerationOutput(
                index=i,
                name=name,
                diff="test diff"
            )
            assert output.name == name


class TestModelGenerationOutput:
    def test_required_attributes(self):
        """Test that ModelGenerationOutput requires name and type."""
        output = ModelGenerationOutput(name="test_model", type="checkpoint")
        
        assert output.name == "test_model"
        assert output.type == "checkpoint"
        assert isinstance(output, GenerationOutput)

    def test_type_attribute_literal(self):
        """Test that type is set to artifact_output."""
        output = ModelGenerationOutput(name="test", type="lora")
        
        # Note: This tests the class type attribute, not the model type
        assert hasattr(output, 'type')

    def test_default_weight(self):
        """Test that weight defaults to None."""
        output = ModelGenerationOutput(name="test_model", type="checkpoint")
        
        assert output.weight is None

    def test_custom_weight(self):
        """Test setting custom weight value."""
        output = ModelGenerationOutput(
            name="test_model",
            type="lora",
            weight=0.8
        )
        
        assert output.weight == 0.8

    def test_model_types(self):
        """Test with different model types."""
        model_types = ["checkpoint", "upscaler", "lora", "text_encoder", "vae", "other", "embedding"]
        
        for model_type in model_types:
            output = ModelGenerationOutput(
                name=f"test_{model_type}",
                type=model_type,
                weight=1.0
            )
            assert output.type == model_type

    def test_weight_values(self):
        """Test with different weight values."""
        weight_values = [0.0, 0.5, 1.0, 1.5, -0.5, None]
        
        for weight in weight_values:
            output = ModelGenerationOutput(
                name="test_model",
                type="lora",
                weight=weight
            )
            assert output.weight == weight


class TestModelsGenerationOutput:
    def test_required_attributes(self):
        """Test that ModelsGenerationOutput requires models list."""
        models = [
            ModelGenerationOutput(name="model1", type="checkpoint"),
            ModelGenerationOutput(name="model2", type="lora", weight=0.8)
        ]
        output = ModelsGenerationOutput(models=models)
        
        assert output.models == models
        assert isinstance(output, GenerationOutput)

    def test_type_attribute(self):
        """Test that type is set to artifact_output."""
        output = ModelsGenerationOutput(models=[])
        
        assert output.type == "artifact_output"

    def test_empty_models_list(self):
        """Test with empty models list."""
        output = ModelsGenerationOutput(models=[])
        
        assert output.models == []
        assert len(output.models) == 0

    def test_single_model(self):
        """Test with single model in list."""
        model = ModelGenerationOutput(name="single_model", type="checkpoint")
        output = ModelsGenerationOutput(models=[model])
        
        assert len(output.models) == 1
        assert output.models[0] == model

    def test_multiple_models(self):
        """Test with multiple models in list."""
        models = [
            ModelGenerationOutput(name="checkpoint1", type="checkpoint", weight=1.0),
            ModelGenerationOutput(name="lora1", type="lora", weight=0.8),
            ModelGenerationOutput(name="upscaler1", type="upscaler", weight=1.0),
            ModelGenerationOutput(name="embedding1", type="embedding")
        ]
        output = ModelsGenerationOutput(models=models)
        
        assert len(output.models) == 4
        assert all(isinstance(model, ModelGenerationOutput) for model in output.models)

    def test_models_list_type(self):
        """Test that models attribute is a list."""
        output = ModelsGenerationOutput(models=[])
        
        assert isinstance(output.models, list)


class TestDataclassStructure:
    """Test that all output classes are properly structured as dataclasses."""
    
    def test_all_outputs_are_dataclasses(self):
        """Test that all output classes use dataclass decorator."""
        output_classes = [
            GenerationOutput,
            ImageGenerationOutput,
            ProgressGenerationOutput,
            CompareImagesGenerationOutput,
            TimerGenerationOutput,
            GalleryGenerationOutput,
            SeedGenerationOutput,
            DiffTextGenerationOutput,
            ModelGenerationOutput,
            ModelsGenerationOutput
        ]
        
        for cls in output_classes:
            # Check if class has dataclass fields
            assert hasattr(cls, '__dataclass_fields__'), f"{cls.__name__} is not a dataclass"

    def test_field_definitions(self):
        """Test that critical fields are properly defined."""
        # Test ImageGenerationOutput has required image field
        image_fields = fields(ImageGenerationOutput)
        image_field_names = [f.name for f in image_fields]
        assert 'image' in image_field_names
        
        # Test that image field doesn't have default value (required)
        image_field = next(f for f in image_fields if f.name == 'image')
        assert image_field.default == MISSING
        assert image_field.default_factory == MISSING

    def test_inheritance_fields(self):
        """Test that inherited fields are accessible."""
        test_image = Image.new('RGB', (100, 100), color='red')
        output = ImageGenerationOutput(image=test_image)
        
        # Should have both own fields and inherited fields
        assert hasattr(output, 'image')  # Own field
        assert hasattr(output, 'pipe_id')  # Inherited field
        assert hasattr(output, 'pipe_name')  # Inherited field
        assert hasattr(output, 'type')  # Inherited field

    def test_kw_only_behavior(self):
        """Test that GenerationOutput uses kw_only=True for its fields."""
        # This should work (using keyword arguments)
        output = GenerationOutput(pipe_id=1, pipe_name="test")
        assert output.pipe_id == 1
        assert output.pipe_name == "test"
        
        # This should also work (no arguments, all have defaults)
        output2 = GenerationOutput()
        assert output2.pipe_id is None
        assert output2.pipe_name is None