import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from src.platform.plugins.sourced_registry import DuplicateRegistrationError, SourcedRegistry

logger = logging.getLogger(__name__)

CORE_SOURCE = 'core'

DEFAULT_MODEL_EXTENSIONS = frozenset({
    '.safetensors', '.ckpt', '.pt', '.pth', '.bin', '.gguf', '.task', '.tflite', '.sft',
})

LAYOUT_FILE = 'file'
LAYOUT_DIRECTORY = 'directory'
LAYOUTS = (LAYOUT_FILE, LAYOUT_DIRECTORY)

CLOUD_MODEL_TYPE = 'cloud'
VIRTUAL_MODEL_TYPES = (CLOUD_MODEL_TYPE,)
UNDEFINED_MODEL_TYPE = 'undefined'
CHECKPOINT_MODEL_TYPE = 'checkpoint'
DIFFUSION_MODEL_TYPE = 'diffusion_model'
HEADER_EXTENSIONS = frozenset({'.safetensors', '.sft', '.gguf'})
SCAN_HEADERS_FOLDER_NAMES = frozenset({'stable-diffusion', 'unet'})

_TYPE_ID = re.compile(r'^[a-z][a-z0-9_]*$')
_FOLDER = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?$')
_WINDOWS_RESERVED = frozenset({'con', 'prn', 'aux', 'nul', *(f'com{n}' for n in range(1, 10)), *(f'lpt{n}' for n in range(1, 10))})
_EXTENSION = re.compile(r'^\.[a-z0-9]+$')
_RESERVED_IDS = frozenset({*VIRTUAL_MODEL_TYPES, UNDEFINED_MODEL_TYPE})


class DuplicateModelTypeError(DuplicateRegistrationError):
    pass


class InvalidModelTypeError(ValueError):
    pass


@dataclass(frozen=True)
class ModelTypeDefinition:
    id: str
    label: str
    folder: str
    purpose: str = ''
    aliases: Tuple[str, ...] = ()
    extensions: FrozenSet[str] = DEFAULT_MODEL_EXTENSIONS
    layout: str = LAYOUT_FILE
    header_classified: bool = False
    assignable: bool = True
    source: str = CORE_SOURCE

    def to_dict(self) -> Dict[str, object]:
        return {
            'id': self.id,
            'label': self.label,
            'purpose': self.purpose,
            'folder': self.folder,
            'aliases': list(self.aliases),
            'extensions': sorted(self.extensions),
            'layout': self.layout,
            'header_classified': self.header_classified,
            'assignable': self.assignable,
            'virtual': False,
            'source': self.source,
        }


def _core(type_id, label, folder, purpose='', **extra) -> ModelTypeDefinition:
    return ModelTypeDefinition(id=type_id, label=label, folder=folder, purpose=purpose, **extra)


CORE_MODEL_TYPE_DEFINITIONS: Tuple[ModelTypeDefinition, ...] = (
    _core('checkpoint', 'Base model', 'checkpoints', 'Main generation model',
          aliases=('Stable-diffusion',), header_classified=True),
    _core('diffusion_model', 'Diffusion model', 'diffusion_models', 'Main generation model', header_classified=True),
    _core('lora', 'LoRA', 'loras', 'Style or concept adapter', aliases=('Lora', 'LyCORIS')),
    _core('embedding', 'Embedding', 'embeddings', 'Prompt concept or style'),
    _core('upscaler', 'Upscaler', 'upscalers', 'Resolution and detail enhancement',
          aliases=('upscale_models', 'latent_upscale_models', 'ESRGAN', 'RealESRGAN')),
    _core('vae', 'VAE', 'vae', 'Image encoder and decoder', aliases=('VAE',)),
    _core('controlnet', 'ControlNet', 'controlnet', 'Structure and composition guidance', aliases=('ControlNet',)),
    _core('adetailer', 'Detailer', 'adetailer', 'Automatic detail refinement'),
    _core('text_encoder', 'Text encoder', 'text_encoders', 'Converts prompts into guidance', aliases=('clip',)),
    _core('unet', 'UNet', 'unet', 'Main generation model', header_classified=True),
    _core('insightface', 'InsightFace', 'insightface', 'Face analysis'),
    _core('facerestore', 'Face restore', 'facerestore', 'Restores faces'),
    _core('instantid', 'InstantID', 'instantid', 'Identity adapter'),
    _core('detection_segm', 'Segmentation detector', 'detection_segm', 'Finds and masks regions'),
    _core('detection_bbox', 'Box detector', 'detection_bbox', 'Finds regions'),
    _core('mediapipe', 'MediaPipe', 'mediapipe', 'Face and body landmarks'),
    _core('llm', 'LLM', 'llm', 'Language model', layout=LAYOUT_DIRECTORY, assignable=False),
    _core('vfi', 'VFI', 'vfi', 'Adds in-between video frames'),
    _core('refmod', 'RefMod', 'refmods', 'Pre-encoded reference latents'),
    _core('model_patch', 'Model patch', 'model_patches', 'Add-on branch loaded with a diffusion model'),
    _core('geometry_estimation', 'Geometry estimator', 'geometry_estimation',
          'Estimates depth and camera from an image'),
    _core('preview_decoder', 'Preview decoder', 'vae_approx', 'Tiny decoder for live generation previews'),
    _core('lut', 'LUT', 'luts', 'Colour grade for the LUT video effect', extensions=frozenset({'.cube'})),
)

CORE_MODEL_TYPES: Tuple[str, ...] = tuple(d.id for d in CORE_MODEL_TYPE_DEFINITIONS)
CORE_MODEL_TYPE_TO_DIRECTORY: Mapping[str, str] = MappingProxyType(
    {d.id: d.folder for d in CORE_MODEL_TYPE_DEFINITIONS}
)

VIRTUAL_MODEL_TYPE_DEFINITIONS: Tuple[Dict[str, object], ...] = (
    {
        'id': CLOUD_MODEL_TYPE,
        'label': 'Cloud',
        'purpose': 'Runs on a cloud service',
        'folder': None,
        'aliases': [],
        'extensions': [],
        'layout': None,
        'header_classified': False,
        'assignable': False,
        'virtual': True,
        'source': CORE_SOURCE,
    },
)


def _fold(name: str) -> str:
    return unicodedata.normalize('NFC', name).casefold()


@dataclass(frozen=True)
class _View:
    types: Tuple[str, ...] = ()
    type_to_directory: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    directory_to_type: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    aliases: Mapping[str, Tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    folder_lookup: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    extensions: FrozenSet[str] = frozenset()
    extensions_by_type: Mapping[str, FrozenSet[str]] = field(default_factory=lambda: MappingProxyType({}))
    header_classified: FrozenSet[str] = frozenset()
    directory_layout: FrozenSet[str] = frozenset()
    assignable: Tuple[str, ...] = ()


def _build_view(definitions: Iterable[ModelTypeDefinition]) -> _View:
    definitions = list(definitions)
    folder_lookup: Dict[str, str] = {}
    for d in definitions:
        folder_lookup[_fold(d.folder)] = d.id
    for d in definitions:
        for alias in d.aliases:
            folder_lookup.setdefault(_fold(alias), d.id)
    return _View(
        types=tuple(d.id for d in definitions),
        type_to_directory=MappingProxyType({d.id: d.folder for d in definitions}),
        directory_to_type=MappingProxyType({d.folder: d.id for d in definitions}),
        aliases=MappingProxyType({d.folder: d.aliases for d in definitions if d.aliases}),
        folder_lookup=MappingProxyType(folder_lookup),
        extensions=frozenset().union(*(d.extensions for d in definitions)) if definitions else frozenset(),
        extensions_by_type=MappingProxyType({d.id: d.extensions for d in definitions}),
        header_classified=frozenset(d.id for d in definitions if d.header_classified),
        directory_layout=frozenset(d.id for d in definitions if d.layout == LAYOUT_DIRECTORY),
        assignable=tuple(d.id for d in definitions if d.assignable),
    )


def _validate(definition: ModelTypeDefinition) -> None:
    owner = f"Model type '{definition.id}' from '{definition.source}'"
    if not isinstance(definition.id, str) or not _TYPE_ID.match(definition.id):
        raise InvalidModelTypeError(f"{owner}: id must match {_TYPE_ID.pattern}")
    if definition.id in _RESERVED_IDS:
        raise InvalidModelTypeError(f"{owner}: '{definition.id}' is reserved")
    if not isinstance(definition.label, str) or not definition.label.strip():
        raise InvalidModelTypeError(f"{owner}: label is required")
    for name in (definition.folder, *definition.aliases):
        if not isinstance(name, str) or not _FOLDER.match(name) or name.split('.')[0].lower() in _WINDOWS_RESERVED:
            raise InvalidModelTypeError(f"{owner}: '{name}' is not a single folder name")
    if not definition.extensions:
        raise InvalidModelTypeError(f"{owner}: at least one file extension is required")
    for extension in definition.extensions:
        if not isinstance(extension, str) or not _EXTENSION.match(extension):
            raise InvalidModelTypeError(f"{owner}: '{extension}' is not a file extension like '.safetensors'")
    if definition.layout not in LAYOUTS:
        raise InvalidModelTypeError(f"{owner}: layout must be one of {list(LAYOUTS)}")


def normalize_extensions(values: Optional[Iterable[str]]) -> FrozenSet[str]:
    if not values:
        return DEFAULT_MODEL_EXTENSIONS
    normalized = set()
    for value in values:
        text = str(value).strip().lower()
        normalized.add(text if text.startswith('.') else f'.{text}')
    return frozenset(normalized)


class ModelTypeRegistry(SourcedRegistry[ModelTypeDefinition]):
    key_attr = 'id'
    label = 'Model type'
    duplicate_error = DuplicateModelTypeError

    def __init__(self, definitions: Iterable[ModelTypeDefinition] = ()) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._listeners: Dict[str, Callable[[], None]] = {}
        self._view = _View()
        for definition in definitions:
            self.register(definition)

    @staticmethod
    def _conflict(entries: Dict[str, ModelTypeDefinition], definition: ModelTypeDefinition) -> Optional[str]:
        existing = entries.get(definition.id)
        if existing is not None:
            return (
                f"Model type '{definition.id}' from '{definition.source}' is already registered by "
                f"'{existing.source}'"
            )
        wanted = {_fold(name): name for name in (definition.folder, *definition.aliases)}
        for other in entries.values():
            for taken in (other.folder, *other.aliases):
                if _fold(taken) in wanted:
                    return (
                        f"Model type '{definition.id}' from '{definition.source}' uses folder "
                        f"'{wanted[_fold(taken)]}', already used by model type '{other.id}' from '{other.source}'"
                    )
        return None

    def register(self, entry: ModelTypeDefinition) -> None:
        self.register_all([entry])

    def register_all(self, entries: Iterable[ModelTypeDefinition]) -> None:
        entries = list(entries)
        for entry in entries:
            _validate(entry)
        with self._lock:
            staged = dict(self._entries)
            for entry in entries:
                conflict = self._conflict(staged, entry)
                if conflict:
                    raise DuplicateModelTypeError(conflict)
                staged[entry.id] = entry
            self._entries = staged
            self._view = _build_view(staged.values())
        if entries:
            self._notify()

    def unregister_source(self, source: str) -> int:
        with self._lock:
            removed = super().unregister_source(source)
            if removed:
                self._view = _build_view(self._entries.values())
        if removed:
            self._notify()
        return removed

    def set_listener(self, name: str, listener: Callable[[], None]) -> None:
        with self._lock:
            self._listeners[name] = listener

    def remove_listener(self, name: str) -> None:
        with self._lock:
            self._listeners.pop(name, None)

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners.values())
        for listener in listeners:
            try:
                listener()
            except Exception:
                logger.exception("A model type listener failed")

    @property
    def view(self) -> _View:
        return self._view

    def definitions(self) -> List[ModelTypeDefinition]:
        with self._lock:
            return list(self._entries.values())

    def catalog(self) -> List[Dict[str, object]]:
        return [d.to_dict() for d in self.definitions()] + [dict(v) for v in VIRTUAL_MODEL_TYPE_DEFINITIONS]


model_type_registry = ModelTypeRegistry(CORE_MODEL_TYPE_DEFINITIONS)


def model_types() -> Tuple[str, ...]:
    return model_type_registry.view.types


def pickable_model_types() -> Tuple[str, ...]:
    return (*model_type_registry.view.types, *VIRTUAL_MODEL_TYPES)


def assignable_model_types() -> Tuple[str, ...]:
    return model_type_registry.view.assignable


def type_to_directory() -> Mapping[str, str]:
    return model_type_registry.view.type_to_directory


def directory_to_type() -> Mapping[str, str]:
    return model_type_registry.view.directory_to_type


def directory_names() -> Tuple[str, ...]:
    return tuple(model_type_registry.view.directory_to_type.keys())


def directory_aliases() -> Mapping[str, Tuple[str, ...]]:
    return model_type_registry.view.aliases


def supported_extensions() -> FrozenSet[str]:
    return model_type_registry.view.extensions


def extensions_for(model_type: str) -> FrozenSet[str]:
    return model_type_registry.view.extensions_by_type.get(model_type, DEFAULT_MODEL_EXTENSIONS)


def header_classified_types() -> FrozenSet[str]:
    return model_type_registry.view.header_classified


def directory_layout_types() -> FrozenSet[str]:
    return model_type_registry.view.directory_layout


def is_model_type(model_type: str) -> bool:
    return model_type in model_type_registry.view.type_to_directory


def type_for_folder_name(name: str) -> Optional[str]:
    return model_type_registry.view.folder_lookup.get(_fold(name))


def scan_headers_by_default(folder_name):
    segments = [part for part in re.split(r'[\\/]', folder_name) if part]
    if not segments:
        return False
    return _fold(segments[-1]) in SCAN_HEADERS_FOLDER_NAMES


def binding_scans_headers_by_default(model_type, subdir):
    return model_type in header_classified_types() and scan_headers_by_default(subdir or '')
