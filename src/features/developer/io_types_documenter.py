"""IO Types documentation generator."""
from typing import List, Dict, Any


class IoTypesDocumenter:
    """Generates documentation for IOType enums used in pipes."""

    # Static descriptions mapping
    IO_TYPE_DESCRIPTIONS = {
        'INT': 'Integer number',
        'FLOAT': 'Floating point number',
        'IMAGE': 'PIL Image object',
        'MASK': 'Image mask for inpainting',
        'LATENT': 'Latent representation of an image',
        'VIDEO': 'Video data',
        'AUDIO': 'Audio data',
        'MESH': '3D mesh file (glTF-binary)',
        'MODEL': 'AI model (checkpoint)',
        'CLIP': 'CLIP text encoder model',
        'VAE': 'Variational Autoencoder model',
        'NUMPY': 'NumPy array',
        'IMAGE_TYPE': 'Image type indicator (PIL/LATENT/TENSOR)',
        'P_PROMPT': 'Positive text prompt',
        'N_PROMPT': 'Negative text prompt',
        'P_PROMPT_EMBED': 'Positive prompt embeddings',
        'N_PROMPT_EMBED': 'Negative prompt embeddings',
        'CONDITIONING': 'Conditioning data for generation',
        'EMBEDDING': 'Text embeddings',
        'SEED': 'Random seed for generation',
        'RESOLUTION': 'Image resolution (width, height)',
        'SAMPLER': 'Sampling algorithm name',
        'SCHEDULER': 'Scheduler algorithm name',
        'CLIP_SKIP': 'CLIP layers to skip',
        'CFG': 'Classifier-free guidance scale',
        'DENOISE': 'Denoising strength',
        'STEP': 'Number of sampling steps',
        'MODE': 'Generation mode (txt2img, img2img, etc.)',
        'TEXT': 'Plain text string',
        'DEVICE': 'Compute device (cuda, cpu)',
        'ANNOTATION': 'Text annotation with optional bounding box',
        'PIPE': 'Pipeline configuration',
        'FORM': 'Form data',
        'DICT': 'Dictionary/object data',
        'LORA': 'LoRA model and weight pairs'
    }

    def get_description(self, io_type) -> str:
        """Get description for an IOType.

        Args:
            io_type: IOType enum value

        Returns:
            Description string for the IO type
        """
        return self.IO_TYPE_DESCRIPTIONS.get(io_type.value, 'No description available')

    def generate_documentation(self) -> Dict[str, Any]:
        """Generate documentation for all IO types.

        Returns:
            Dict with 'io_types' list and 'total' count

        Raises:
            ValueError: If IOType enum cannot be loaded
        """
        try:
            from src.pipelines.contracts import IOType
        except ImportError as e:
            raise ValueError(f"Failed to import IOType: {e}")

        io_types = []
        for io_type in IOType:
            io_types.append({
                'name': io_type.name,
                'value': io_type.value,
                'description': self.get_description(io_type)
            })

        return {
            'io_types': io_types,
            'total': len(io_types)
        }
