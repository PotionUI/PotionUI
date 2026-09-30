"""The model depot's directory layout: one first-level directory per model type.

`DIRECTORY_TO_MODEL_TYPE` is the single source of truth for how a model type
(`checkpoint`, `lora`, ...) maps onto its directory under the configured
models root (`checkpoints`, `loras`, ...). Every scanner, indexer, downloader
and picker that needs this mapping - or its inverse, or just the set of known
types/directories - imports it from here rather than keeping its own copy.
`llm` is directory-per-model (HF layout: `config.json` + sharded weights)
rather than flat-file-per-model; callers that walk the depot file-by-file may
need to special-case it (see `ModelScanner.DIRECTORY_MODEL_TYPES`).

`text_encoders` is the conditioning-encoder directory generally, not text only:
TRELLIS.2's DINOv3 ViT-L/16 image conditioner lives there too. Comfy-Org ships
that file under `clip_vision/`, which this map deliberately does NOT define -
there is one home for a conditioning encoder, and adding a second would split
the pickers that list them.
"""

import re
import unicodedata

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
    'refmods': 'refmod',
}

MODEL_TYPE_TO_DIRECTORY = {model_type: directory for directory, model_type in DIRECTORY_TO_MODEL_TYPE.items()}

MODEL_DIRECTORY_NAMES = tuple(DIRECTORY_TO_MODEL_TYPE.keys())
MODEL_TYPES = tuple(DIRECTORY_TO_MODEL_TYPE.values())

MODEL_DIRECTORY_ALIASES = {
    'checkpoints': ('Stable-diffusion',),
    'loras': ('Lora', 'LyCORIS'),
    'vae': ('VAE',),
    'upscalers': ('upscale_models', 'ESRGAN', 'RealESRGAN'),
    'controlnet': ('ControlNet',),
    'text_encoders': ('clip',),
}

# File extensions a depot scan recognizes as a model file, regardless of which
# scanner is walking the depot.
SUPPORTED_MODEL_EXTENSIONS = frozenset({
    '.safetensors', '.ckpt', '.pt', '.pth', '.bin', '.gguf', '.task', '.tflite', '.sft',
})

_FOLDER_NAME_TO_MODEL_TYPE = {name.lower(): model_type for name, model_type in DIRECTORY_TO_MODEL_TYPE.items()}
for _directory, _aliases in MODEL_DIRECTORY_ALIASES.items():
    _model_type = DIRECTORY_TO_MODEL_TYPE[_directory]
    for _alias in _aliases:
        _FOLDER_NAME_TO_MODEL_TYPE.setdefault(_alias.lower(), _model_type)


def type_for_folder_name(name):
    return _FOLDER_NAME_TO_MODEL_TYPE.get(name.lower())


UNDEFINED_MODEL_TYPE = 'undefined'
CHECKPOINT_MODEL_TYPE = 'checkpoint'
DIFFUSION_MODEL_TYPE = 'diffusion_model'
HEADER_CLASSIFIED_TYPES = frozenset({'checkpoint', 'diffusion_model', 'unet'})
HEADER_EXTENSIONS = frozenset({'.safetensors', '.sft', '.gguf'})
SCAN_HEADERS_FOLDER_NAMES = frozenset({'stable-diffusion', 'unet'})


def scan_headers_by_default(folder_name):
    segments = [part for part in re.split(r'[\\/]', folder_name) if part]
    if not segments:
        return False
    return unicodedata.normalize('NFC', segments[-1]).casefold() in SCAN_HEADERS_FOLDER_NAMES


def binding_scans_headers_by_default(model_type, subdir):
    return model_type in HEADER_CLASSIFIED_TYPES and scan_headers_by_default(subdir or '')
