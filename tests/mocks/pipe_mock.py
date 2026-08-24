"""
Generation pipe mocks for testing without actual model inference.

These mocks replace compute-intensive pipe operations with lightweight fakes
that return valid outputs suitable for testing pipeline logic.
"""

import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
from src.pipelines.outputs import (
    ImageGenerationOutput,
    SeedGenerationOutput,
    GalleryGenerationOutput
)
from src.pipelines.contracts import PipeOutput, IOType


@pytest.fixture
def fake_image():
    """
    Create a fake PIL Image for testing.

    Returns a simple 512x512 RGB image with red color.
    This is much faster than generating real images and suitable
    for testing image processing logic.

    Usage:
        def test_image_processing(fake_image):
            # Use fake_image in tests
            assert fake_image.size == (512, 512)
    """
    return Image.new('RGB', (512, 512), color='red')


@pytest.fixture
def mock_generator_pipe(fake_image):
    """
    Mock the image generator pipe to return fake images.

    This fixture patches the GeneratorPipe to return fake images
    instead of performing actual model inference. Each "generation"
    returns a simple colored image instantly.

    The mock properly emits ImageGenerationOutput objects through
    the generation_outputs callback, maintaining compatibility with
    the rest of the pipeline.

    Usage:
        def test_generation_pipeline(mock_generator_pipe):
            # Generator pipe returns fake images
            output = generator.process(pipe_input, generation_outputs)
    """
    def fake_generate(pipe_input, generation_outputs, **kwargs):
        """
        Fake generation function that returns a colored image.

        This simulates the real generator pipe by:
        1. Creating a fake image
        2. Emitting ImageGenerationOutput through callback
        3. Returning PipeOutput with the image
        """
        # Create fake image (color varies to distinguish generations)
        colors = ['red', 'green', 'blue', 'yellow', 'purple']
        color_index = kwargs.get('index', 0) % len(colors)
        fake_img = Image.new('RGB', (512, 512), color=colors[color_index])

        # Emit image output through callback
        output = ImageGenerationOutput(
            image=fake_img,
            temporary=False
        )
        generation_outputs(output)

        # Return pipe output
        return PipeOutput(output={'image': fake_img})

    # Try to patch a generator pipe - one per model family
    patch_paths = [
        'src.pipelines.pipes.generator.sdxl.main.GeneratorSDXLPipe.process',
        'src.pipelines.pipes.generator.flux.main.GeneratorFluxPipe.process',
        'src.pipelines.pipes.generator.qwen.main.GeneratorQwenPipe.process',
    ]

    # Apply first successful patch
    for path in patch_paths:
        try:
            with patch(path, side_effect=fake_generate):
                yield
                return
        except (AttributeError, ModuleNotFoundError):
            continue

    # If no patch worked, just yield without patching
    yield


@pytest.fixture
def mock_upscaler_pipe():
    """
    Mock the upscaler pipe to return input image unchanged.

    This fixture patches the UpscalerPipe to bypass actual upscaling.
    The mock returns the input image at a larger size without performing
    real upscaling inference.

    Usage:
        def test_upscaling_pipeline(mock_upscaler_pipe):
            # Upscaler returns image immediately
            output = upscaler.process(pipe_input, generation_outputs)
    """
    def fake_upscale(pipe_input, generation_outputs, **kwargs):
        """
        Fake upscaling that just resizes the input image.

        This is much faster than real upscaling while still
        producing a larger image for testing purposes.
        """
        # Get input image or create a fake one
        input_image = pipe_input.input.get('image')
        if input_image is None:
            input_image = Image.new('RGB', (512, 512), color='green')

        # "Upscale" by simple resize (no ML inference)
        upscale_factor = kwargs.get('scale', 2)
        new_size = (
            int(input_image.width * upscale_factor),
            int(input_image.height * upscale_factor)
        )
        upscaled = input_image.resize(new_size, Image.LANCZOS)

        # Emit upscaled image
        output = ImageGenerationOutput(
            image=upscaled,
            temporary=False
        )
        generation_outputs(output)

        return PipeOutput(output={'image': upscaled})

    with patch('src.pipelines.pipes.upscaler.main.Upscaler.process', side_effect=fake_upscale):
        yield


@pytest.fixture
def mock_seed_generator_pipe():
    """
    Mock the seed generator pipe with predictable seeds.

    This fixture patches the SeedGeneratorPipe to return
    predictable seed values instead of random ones.
    This makes tests deterministic and reproducible.

    Usage:
        def test_seed_generation(mock_seed_generator_pipe):
            # Seeds are predictable: 1000, 1001, 1002, ...
            output = seed_gen.process(pipe_input, generation_outputs)
    """
    def fake_seed_generation(pipe_input, generation_outputs, **kwargs):
        """
        Generate predictable seeds for testing.

        Returns seeds starting at 1000 and incrementing.
        This allows tests to verify seed handling without randomness.
        """
        seed = kwargs.get('seed', 1000)
        quantity = kwargs.get('quantity', 1)

        seeds = []
        for i in range(quantity):
            generated_seed = seed + i

            # Emit seed output
            generation_outputs(SeedGenerationOutput(
                index=i,
                seed=generated_seed
            ))

            seeds.append(generated_seed)

        return PipeOutput(output={'seed': seeds})

    with patch('src.pipelines.pipes.seed_generator.main.SeedGeneratorPipe.process',
               side_effect=fake_seed_generation):
        yield


@pytest.fixture
def mock_prompt_encoder_pipe():
    """
    Mock the prompt encoder pipe to skip CLIP encoding.

    This fixture patches the PromptEncoderPipe to return
    fake embeddings instead of actually running CLIP models.

    Usage:
        def test_prompt_encoding(mock_prompt_encoder_pipe):
            # Prompt encoding is mocked
            output = encoder.process(pipe_input, generation_outputs)
    """
    def fake_encode(pipe_input, generation_outputs, **kwargs):
        """
        Fake prompt encoding that returns dummy embeddings.

        Returns tensors of the right shape but without actual CLIP inference.
        """
        import torch

        # Create fake embeddings (768-dim is typical for CLIP)
        fake_embeddings = torch.randn(1, 77, 768)

        return PipeOutput(output={
            'prompt_embeds': fake_embeddings,
            'negative_prompt_embeds': fake_embeddings
        })

    with patch('src.pipelines.pipes.prompt_encoder.main.PromptEncoderPipe.process',
               side_effect=fake_encode):
        yield


@pytest.fixture
def mock_gallery_pipe():
    """
    Mock the gallery pipe to skip image saving and collection.

    This fixture patches the GalleryPipe to accept images
    without actually saving them to disk or managing galleries.

    Usage:
        def test_gallery_collection(mock_gallery_pipe):
            # Gallery operations are mocked
            output = gallery.process(pipe_input, generation_outputs)
    """
    def fake_gallery(pipe_input, generation_outputs, **kwargs):
        """
        Fake gallery that collects images without saving.

        Emits gallery update outputs without file I/O.
        """
        images = pipe_input.input.get('images', [])

        # Convert raw images to ImageGenerationOutput if needed
        image_outputs = []
        for img in images:
            if isinstance(img, ImageGenerationOutput):
                image_outputs.append(img)
            else:
                image_outputs.append(ImageGenerationOutput(
                    image=img,
                    temporary=False
                ))

        # Emit gallery update
        generation_outputs(GalleryGenerationOutput(
            images=image_outputs,
            videos=[]
        ))

        return PipeOutput(output={'images': images})

    with patch('src.pipelines.pipes.gallery.main.GalleryPipe.process',
               side_effect=fake_gallery):
        yield


@pytest.fixture
def mock_all_pipes(mock_generator_pipe, mock_upscaler_pipe, mock_seed_generator_pipe,
                  mock_prompt_encoder_pipe, mock_gallery_pipe):
    """
    Convenience fixture that mocks all common pipes.

    This fixture applies all pipe mocks at once, which is useful
    for integration tests that run full pipelines.

    Usage:
        def test_full_pipeline(mock_all_pipes):
            # All pipes are mocked
            result = run_generation_pipeline()
    """
    yield
