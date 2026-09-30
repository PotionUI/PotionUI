import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock, patch

from src.features.backends.backend_registry import BackendRegistry
from src.features.cloud.catalog import CloudCatalog
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.testing.fake import FakeBehaviour, FakeClock, FakeCloudConfig, FakeCloudProvider
from src.features.generation.engine import GenerationEngine
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.output_processor import OutputProcessor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.models.backend_indexer import BackendModelIndexer
from src.features.models.repository import model_repo
from src.features.presets import PresetProcessor, PresetTemplateLoader
from src.platform.templating.processor import TemplateProcessor
from src.pipelines.catalog import PipeCatalog
from tests.features.cloud.conftest import ScriptedPluginRegistry

SECRET = "sk-live-SECRET-0123456789"
PRESET_ID = "01KCLOUDTESTPRESET0000000A"
USER_ID = "user-1"
BACKEND_ID = "cloud-1"
IMAGE = "fake/image-1"

PRESET_YML = textwrap.dedent(
    """
    schema: 1
    id: "{preset_id}"
    name: "Cloud Test"
    category: "image"
    version: "1.0.0"
    engine: "cloud"
    driver: "cloud.fake"
    modes:
      - txt2img
    """
)

FORM_YML = textwrap.dedent(
    """
    name: "custom"
    fields:
      - name: "model"
        type: "string"
        label: "Model"
      - name: "quantity"
        type: "number"
        label: "Quantity"
        default: 1
      - name: "aspect_ratio"
        type: "string"
        label: "Aspect ratio"
        default: "1:1"
    """
)

PIPELINE_YML = textwrap.dedent(
    """
    pipeline:
      - name: "seed_generator"
        id: "seed_generator"
        enabled: true
        configuration:
          seed: -1
          quantity: "{{ form.quantity }}"
      - name: "cloud_generate"
        id: "cloud_generate"
        enabled: true
        input:
          - ["seed", "seed_generator", "seed"]
        configuration:
          task: "txt2img"
          model: "{{ form.model }}"
          quantity: "{{ form.quantity }}"
          prompts: "{{ generation.prompts.pairs }}"
          params:
            aspect_ratio: "{{ form.aspect_ratio }}"
      - name: "gallery"
        id: "gallery"
        enabled: true
        input:
          - ["image", "cloud_generate", "image"]
          - ["video", "cloud_generate", "video"]
          - ["seed", "cloud_generate", "seed"]
    """
)


MODEL_EMITTER_YML = (
    '  - name: "param_emitter"\n'
    '    id: "param_emitter"\n'
    "    enabled: true\n"
    "    configuration:\n"
    "      quantity: 1\n"
    "      parameters:\n"
    '        - ["model", "{{ form.model }}"]\n'
)


def write_preset(root: Path, emit_model: bool = False) -> Path:
    preset = root / "cloud" / "Test" / "v1"
    (preset / "modes" / "txt2img").mkdir(parents=True)
    (preset / "preset.yml").write_text(PRESET_YML.format(preset_id=PRESET_ID))
    (preset / "modes" / "txt2img" / "form.yml").write_text(FORM_YML)
    pipeline = PIPELINE_YML
    if emit_model:
        pipeline = pipeline.replace("pipeline:\n", "pipeline:\n" + MODEL_EMITTER_YML, 1)
    (preset / "modes" / "txt2img" / "pipeline.yml").write_text(pipeline)
    return preset


class Collected:
    def __init__(self) -> None:
        self.outputs: List[Any] = []
        self.done = asyncio.Event()

    async def __call__(self, generation_id: str, output: Any) -> None:
        if output is None:
            self.done.set()
            return
        self.outputs.append(output)

    def of(self, cls: type) -> List[Any]:
        return [output for output in self.outputs if isinstance(output, cls)]


class CloudGeneration:
    def __init__(
        self,
        tmp_path: Path,
        behaviour: FakeBehaviour,
        content_safety: Any = None,
        timeout_seconds: int = 1800,
        max_parallel: int = 4,
        emit_model: bool = False,
    ) -> None:
        self.max_parallel = max_parallel
        self.timeout_seconds = timeout_seconds
        self.prepared: List[List[Dict[str, Any]]] = []
        self.tmp_path = tmp_path
        self.storage = tmp_path / "storage"
        (self.storage / "generations").mkdir(parents=True)
        (self.storage / "tmp").mkdir()
        self.presets = tmp_path / "presets"
        write_preset(self.presets, emit_model)
        self.behaviour = behaviour
        self.content_safety = content_safety
        self.clock = FakeClock()
        self.settings = Mock()
        self.settings.get_file_storage_directory = Mock(return_value=str(self.storage))
        self.settings.get_setting = Mock(return_value=None)
        self.executors: List[GenerationEngine] = []
        self.registry: Optional[BackendRegistry] = None
        self.orchestrator: Optional[GenerationOrchestrator] = None
        self.collected = Collected()

    def engine(self) -> GenerationEngine:
        catalog = PipeCatalog("src/pipelines/pipes", str(self.tmp_path / "custom-pipes"))
        engine = GenerationEngine(
            gpu=Mock(),
            pipe_catalog=catalog,
            settings=self.settings,
            system_monitor=Mock(),
            memory_advisor=Mock(),
            llm_service=Mock(),
        )
        self.executors.append(engine)
        return engine

    async def start(self) -> "CloudGeneration":
        self.registry = BackendRegistry(
            generation_engine_factory=self.engine,
            plugin_registry=ScriptedPluginRegistry(FakeCloudProvider),
        )
        await self.registry.add_backend(FakeCloudConfig(id=BACKEND_ID, name="Fake cloud", api_key=SECRET, timeout_seconds=self.timeout_seconds, max_parallel=self.max_parallel))
        self.backend = self.registry.get_backend(BACKEND_ID)
        self.backend.clock = self.clock
        self.backend.provider.clock = self.clock
        self.backend.provider.behaviour = self.behaviour
        prepare = self.backend.prepare_pipes

        def capture(pipes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            prepared = prepare(pipes)
            self.prepared.append(prepared)
            return prepared

        self.backend.prepare_pipes = capture
        repository = CloudCatalogRepository()
        self.catalog = CloudCatalog(
            backend_registry=self.registry,
            repository=repository,
            model_repository=model_repo,
            backend_indexer=BackendModelIndexer(),
        )
        await self.catalog.refresh(BACKEND_ID)
        self.slug = next(slug for slug, known in repository.provider_ids(BACKEND_ID).items() if known == IMAGE)
        await self.catalog.set_enabled(BACKEND_ID, [self.slug], True)

        loader = PresetTemplateLoader([str(self.presets)])
        loader.load_presets()
        processor = PresetProcessor(
            template_processor=TemplateProcessor(settings=Mock()),
            settings=self.settings,
            preset_template_loader=loader,
        )
        self.orchestrator = GenerationOrchestrator(
            pipeline_builder=PipelineBuilder(loader, processor),
            backend_registry=self.registry,
            connection_hub=Mock(),
            settings=self.settings,
            output_processor=OutputProcessor(settings=self.settings),
            preset_template_loader=loader,
            content_safety=self.content_safety,
        )
        self.baseline = len(self.executors)
        return self

    def request(self, prompt: str = "a cat", quantity: int = 1, **form: Any) -> Any:
        return SimpleNamespace(
            preset_id=PRESET_ID,
            form_data={"model": self.slug, "quantity": quantity, **form},
            prompt=prompt,
            negative_prompt="",
            prompts=None,
            prompt_state=None,
            mode="txt2img",
            form_name=None,
            backend_id=None,
            tag_ids=None,
            segments=None,
            tab_id="tab-1",
            variables=None,
            source_prompt_id=None,
            collection_ids=None,
        )

    async def submit(self, generation_id: str = "gen-1", **kwargs: Any) -> Dict[str, Any]:
        with patch("src.features.generation.orchestrator.generate_ulid", return_value=generation_id):
            return await self.orchestrator.start_generation(self.request(**kwargs), USER_ID, output_callback=self.collected)

    async def finished(self, timeout: float = 20.0) -> Any:
        await asyncio.wait_for(self.collected.done.wait(), timeout)
        return self.orchestrator.status_tracker.get(self.generation_id)

    async def run(self, **kwargs: Any) -> Any:
        await self.submit(**kwargs)
        return await self.finished()

    generation_id = "gen-1"

    async def drained(self) -> None:
        while self.backend._runs:
            await asyncio.sleep(0)
