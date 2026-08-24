"""The model depot's directory layout: one first-level directory per model type.

`DIRECTORY_TO_MODEL_TYPE` is the single source of truth for how a model type
(`checkpoint`, `lora`, ...) maps onto its directory under the configured
models root (`checkpoints`, `loras`, ...). Every scanner, indexer, downloader
and picker that needs this mapping - or its inverse, or just the set of known
types/directories - imports it from here rather than keeping its own copy.
`llm` is directory-per-model (HF layout: `config.json` + sharded weights)
rather than flat-file-per-model; callers that walk the depot file-by-file may
need to special-case it (see `ModelScanner.DIRECTORY_MODEL_TYPES`).
"""

DIRECTORY_TO_MODEL_TYPE = {
    'checkpoints': 'checkpoint',
    'diffusion_models': 'diffusion_model',
    'loras': 'lora',
    'embeddings': 'embedding',
    'upscalers': 'upscaler',
    'vae': 'vae',
    'controlnet': 'controlnet',
    'adetailer': 'adetailer',
    'text_encoders': 'text_encoder',
    'unet': 'unet',
    'insightface': 'insightface',
    'facerestore': 'facerestore',
    'instantid': 'instantid',
    'detection_segm': 'detection_segm',
    'detection_bbox': 'detection_bbox',
    'mediapipe': 'mediapipe',
    'llm': 'llm',
    'vfi': 'vfi',
}

MODEL_TYPE_TO_DIRECTORY = {model_type: directory for directory, model_type in DIRECTORY_TO_MODEL_TYPE.items()}

MODEL_DIRECTORY_NAMES = tuple(DIRECTORY_TO_MODEL_TYPE.keys())
MODEL_TYPES = tuple(DIRECTORY_TO_MODEL_TYPE.values())

# File extensions a depot scan recognizes as a model file, regardless of which
# scanner is walking the depot.
SUPPORTED_MODEL_EXTENSIONS = frozenset({
    '.safetensors', '.ckpt', '.pt', '.pth', '.bin', '.gguf', '.task', '.tflite', '.sft',
})
