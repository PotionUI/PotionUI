"""Frozen collaborators bundle for the presets operations layer.

Preset operations (query, install/uninstall, user assignment, admin-set
configuration, admin-set form overrides) share the same eleven infrastructure
legs - the two file/database repositories, the loader/processor/template
pipeline, the canonical pipeline builder + pipe catalog (so a pipeline
*preview* can never diverge from a real generation), the user/group
repositories assignment needs, the plugin registry for hook execution, and
settings (for `bind_form`'s storage-root lookup). Bundling them once here -
built in the composition root and passed to `operations` functions and to
every wide external consumer (chat, LLM tools, resource providers, setup) as
a single object - avoids threading eleven positional collaborators through
every call site. A plain, frozen data holder (no behavior beyond field
access, plus one derived field built once at construction), matching
`PromptDatabaseCollaborators` (see `src.features.prompt_database.collaborators`
- the reference shape for a wide-collaborator dissolution).
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from src.features.backends.backend_registry import BackendRegistry
from src.features.models.collaborators import ModelIndexCollaborators
from src.features.presets import PresetTemplateLoader, PresetProcessor
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.form_serializer import PresetFormSerializer
from src.features.presets.repository import DatabasePresetRepository
from src.features.presets.requirements.evaluator import RequirementsCache
from src.features.user_groups.repository import UserGroupRepository
from src.features.users.repository import UserRepository
from src.pipelines.catalog import PipeCatalog
from src.platform.plugins import PluginRegistry
from src.platform.runtime.gpu import GpuMonitor
from src.platform.settings.settings import Settings
from src.platform.templating import TemplateProcessor

if TYPE_CHECKING:
    from src.features.presets.pipeline_assembler import PipelineAssembler


@dataclass(frozen=True)
class PresetCollaborators:
    preset_loader: PresetTemplateLoader
    preset_processor: PresetProcessor
    template_processor: TemplateProcessor
    file_repo: FilePresetRepository
    db_repo: DatabasePresetRepository
    user_repo: UserRepository
    group_repo: UserGroupRepository
    # THE canonical pipeline builder (shared with generation execution) and
    # the catalog used to project the pipeline graph, so `get_pipeline`'s
    # preview can never diverge from a real generation.
    pipeline_builder: "PipelineAssembler"
    pipe_catalog: PipeCatalog
    plugins: PluginRegistry
    # Resolves the user's storage root, so `get_pipeline`'s `bind_form`
    # preview call can run the same media containment check a real
    # generation does (spec follow-up #1).
    settings: Settings
    # Requirements checking (see docs/presets.md "Requirements") - all four
    # optional so existing callers/tests that build this bundle without them
    # keep working; without them, `get_preset_requirements` still runs but
    # every `model`/`vram_min_gb` check resolves to "unknown" and the list/
    # detail endpoints' `requirements_summary` stays `None`.
    model_index: Optional[ModelIndexCollaborators] = None
    gpu_monitor: Optional[GpuMonitor] = None
    backend_registry: Optional[BackendRegistry] = None
    requirements_cache: Optional[RequirementsCache] = None
    # Derived, built once here rather than per-call.
    form_serializer: PresetFormSerializer = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "form_serializer",
            PresetFormSerializer(self.preset_loader, self.template_processor),
        )
