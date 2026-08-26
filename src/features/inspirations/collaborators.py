"""Frozen collaborators bundle for the inspirations operations layer.

Publishing, saving, and commenting on an inspiration each touch some slice of
eleven infrastructure legs - the inspiration repository itself, the source
generation and its parameters, preset resolution, field-type shareability,
file storage/resolution, the upload repository a save writes into, and
notifications. Bundling them once here - built in the composition root and
passed to `operations` functions and `InspirationController` as a single
object - avoids threading eleven positional collaborators through every call
site. A plain, frozen data holder (no behavior beyond field access), matching
`PromptDatabaseCollaborators` (the reference shape for a wide-collaborator
dissolution).
"""
from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from src.features.inspirations.repository import InspirationRepository

if TYPE_CHECKING:
    from src.features.generation.parameter_repository import GenerationParameterRepository
    from src.features.generation.repository import GenerationRepository
    from src.features.media.file_resolver import FilePathResolver
    from src.features.media.upload_repository import UploadRepository
    from src.features.presets.loader import PresetTemplateLoader
    from src.features.presets.name_resolver import PresetNameResolver
    from src.platform.filesystem import FileStore
    from src.platform.filesystem.storage_driver import FileStorageDriver
    from src.platform.plugins.field_types import FieldTypeRegistry


@dataclass(frozen=True)
class InspirationCollaborators:
    repository: InspirationRepository
    generation_repository: "GenerationRepository"
    generation_parameter_repository: "GenerationParameterRepository"
    preset_name_resolver: "PresetNameResolver"
    preset_template_loader: "PresetTemplateLoader"
    field_type_registry: "FieldTypeRegistry"
    file_store: "FileStore"
    file_resolver: "FilePathResolver"
    storage_driver: "FileStorageDriver"
    upload_repository: "UploadRepository"
    # A bound notify callable (`functools.partial(operations.notify,
    # collaborators)`, see `src.bootstrap.container`), not a class instance.
    notification_manager: Callable[..., Any]
