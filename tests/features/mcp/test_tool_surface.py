import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.collections.repository import CollectionRepository
from src.features.library.repository import LibraryRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.tags.repository import TagRepository
from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.governance_repository import ToolGovernanceRepository
from src.features.llm.tools.registry import ToolRegistry
from src.features.llm_memory.records import LLMMemoryNote
from src.features.llm_memory.repository import LLMMemoryRepository
from src.features.mcp.protocol import McpToolCollaborators, handle_method
from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.catalog import ModelCatalog
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookValue
from src.features.phrasebook.repository import PhrasebookCategoryRepository, PhrasebookValueRepository
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import PromptRepository
from src.features.segments.dto import RichSegment, SavedSegment, SegmentCategory
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)

EXPECTED_TOOLS = {
    "list_segment_categories", "get_saved_segments", "get_segment_templates",
    "create_segment_category", "update_segment_category", "delete_segment_category",
    "create_saved_segment", "update_saved_segment", "delete_saved_segment",
    "create_segment_template", "update_segment_template", "delete_segment_template",
    "get_model_info", "get_preset_info", "list_presets",
    "list_phrasebook_categories", "get_phrasebook_values", "list_phrasebook_values",
    "create_phrasebook_category", "update_phrasebook_category", "delete_phrasebook_category",
    "create_phrasebook_values", "remove_phrasebook_values", "update_phrasebook_values",
    "enhance_prompt", "search_model_prompts", "search_gallery",
    "get_prompt", "list_prompts", "add_prompt", "edit_prompt", "delete_prompt",
    "search_models", "list_library_items",
    "write_memory", "read_memory", "update_memory", "delete_memory",
    "manage_collections", "organize_gallery", "start_generation",
}


class _Registry:
    def execute_hook(self, hook, initial_data=None):
        return SimpleNamespace(data=dict(initial_data or {})), None


class _PathLeakTool(BaseTool):
    modes = ["generation"]

    @property
    def name(self):
        return "leaky"

    @property
    def description(self):
        return "stub"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context, **kwargs):
        return ToolResult(success=False, data="", error="cannot open /srv/models/x.safetensors for reading")


class _FakeEmbeddingProvider:
    def __init__(self, available):
        self._available = available

    async def is_available(self):
        return self._available


class _FakeVisionEmbedder:
    def __init__(self, available):
        self._available = available

    def is_available(self):
        return self._available


class _FakeIndexer:
    def __init__(self, vision_available):
        self.vision_embedder = _FakeVisionEmbedder(vision_available)
        self.search_calls = []

    def search_gallery(self, user_id, query):
        self.search_calls.append((user_id, query))
        return [{"file_id": "file-1", "generation_id": "gen-visual", "similarity": 0.91234}]

    def describe_files(self, file_ids):
        return {fid: {"file_type": "image", "file_path": "2026/10/visual.png"} for fid in file_ids}


class _FakeHistory:
    def __init__(self):
        self.async_calls = []

    def _page(self):
        return {
            "generations": [{
                "id": "gen-text",
                "status": "completed",
                "created_at": "2026-10-01T10:00:00",
                "form_data": {"prompt": "a red fox"},
                "preset_name": "Fox preset",
                "tags": [{"name": "fox"}],
                "files": [{"id": "file-t", "file_path": "2026/10/text.png", "file_type": "image", "is_final": True}],
            }],
            "total": 1,
        }

    def get_history(self, **kwargs):
        return self._page()

    async def get_history_async(self, **kwargs):
        self.async_calls.append(kwargs)
        return self._page()

    def get_by_id(self, generation_id, user_id):
        return self._page()["generations"][0]


class _FakePresetFiles:
    def __init__(self):
        self.preset = SimpleNamespace(
            id="preset-a", name="Preset A", description="d", engine="native",
            modes={"txt2img": SimpleNamespace(forms=[])}, llm={}, tags=[],
        )

    def list_all_presets(self):
        return [{"id": "preset-a", "name": "Preset A", "engine": "native", "description": "d", "tags": []}]

    def find_preset_by_id(self, preset_id):
        return self.preset if preset_id == "preset-a" else None


class _FakePresetDb:
    def get_available_preset_ids_for_user(self, user_id):
        return ["preset-a"] if user_id == "user-1" else []


@pytest.fixture
def world(mcp_db):
    with mcp_db.get_cursor() as cursor:
        for user_id in ("user-1", "user-2"):
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
                (user_id, user_id, f"{user_id}@example.com", "x"),
            )

    categories = SegmentCategoryRepository()
    segments = SavedSegmentRepository()
    category = categories.create(SegmentCategory(id="cat-1", name="Moods", user_id="user-1"))
    segment = segments.create(SavedSegment(
        id="seg-1", name="Golden hour", category_id=category.id, content="warm light", user_id="user-1",
    ))

    phrase_categories = PhrasebookCategoryRepository()
    phrase_values = PhrasebookValueRepository()
    phrase_categories.create(PhrasebookCategory(
        id="pb-1", name="camera", path="camera", user_id="user-1", description="lenses",
    ))
    phrase_values.create(PhrasebookValue(id="pbv-1", category_id="pb-1", label="wide", value="wide angle", user_id="user-1"))

    memory = LLMMemoryRepository()
    for key, scope, scope_ref in (
        ("g-note", "global", None),
        ("p-note", "preset", "preset-a"),
        ("m-note", "model", "model-a"),
        ("s-note", "session", "session-a"),
    ):
        memory.upsert(LLMMemoryNote(user_id="user-1", key=key, content=f"{key} content", scope=scope, scope_ref=scope_ref))

    upload = UploadRepository().create(Upload(
        user_id="user-1", filename="u1.png", original_filename="fox.png", media_type="image",
    ))

    prompts = PromptRepository()
    prompt = prompts.create(Prompt(user_id="user-1", name="Study", segments=[RichSegment(content="a red fox")]))

    model_repo = ModelRepository()
    model = model_repo.create(Model(filename="m.safetensors", file_size=1, sha256="a" * 64, model_type="checkpoint"))
    model_repo.assign_model_to_user(model.id, "user-1")
    access = ModelAccessPolicy(model_repo)

    history = _FakeHistory()
    indexer = _FakeIndexer(vision_available=False)
    prompt_database = SimpleNamespace(
        repository=prompts,
        embedding_provider=_FakeEmbeddingProvider(False),
        vector_store=None,
        content_safety=None,
    )

    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    collaborators = McpToolCollaborators(
        tool_registry=registry,
        tool_governance_repository=ToolGovernanceRepository(),
        llm_repository=llm_repository,
        segment_category_repository=categories,
        saved_segment_repository=segments,
        segment_template_repository=SegmentTemplateRepository(),
        model_index_manager=SimpleNamespace(
            model_repo=model_repo, access=access,
            catalog=ModelCatalog(model_repo, access, scanner=None),
        ),
        preset_collaborators=SimpleNamespace(file_repo=_FakePresetFiles(), db_repo=_FakePresetDb()),
        phrasebook_category_repository=phrase_categories,
        phrasebook_value_repository=phrase_values,
        prompt_database=prompt_database,
        llm_memory_repository=memory,
        media_indexer=indexer,
        collection_repository=CollectionRepository(),
        tag_repository=TagRepository(),
        library_collaborators=SimpleNamespace(repository=LibraryRepository(), tag_repository=TagRepository()),
        generation_history_facade=history,
        plugin_registry=_Registry(),
    )
    return SimpleNamespace(
        collaborators=collaborators, registry=registry, segment=segment, category=category,
        prompt=prompt, model=model, history=history, indexer=indexer,
        segments=segments, phrase_categories=phrase_categories, memory=memory, upload=upload,
    )


async def _call(world, name, arguments, user_id="user-1"):
    return await handle_method(
        world.collaborators, "tools/call", {"name": name, "arguments": arguments}, user_id,
    )


def _payload(result):
    return json.loads(result["content"][0]["text"])


class TestToolsList:
    @pytest.mark.asyncio
    async def test_exposes_exactly_the_expected_builtin_tools(self, world):
        listed = await handle_method(world.collaborators, "tools/list", {}, "user-1")

        assert {t["name"] for t in listed["tools"]} == EXPECTED_TOOLS

    @pytest.mark.asyncio
    async def test_every_tool_has_a_description_an_object_schema_and_no_community_prompt_wording(self, world):
        listed = await handle_method(world.collaborators, "tools/list", {}, "user-1")

        for tool in listed["tools"]:
            assert tool["description"].strip(), tool["name"]
            assert tool["inputSchema"]["type"] == "object", tool["name"]
            assert "community prompts" not in tool["description"].lower(), tool["name"]


READ_CALLS = [
    ("list_segment_categories", {}),
    ("get_saved_segments", {}),
    ("get_segment_templates", {}),
    ("list_presets", {}),
    ("get_preset_info", {"preset_id": "preset-a"}),
    ("list_library_items", {}),
    ("search_models", {"query": "m"}),
    ("list_phrasebook_categories", {}),
    ("get_phrasebook_values", {"category_id": "pb-1"}),
    ("list_phrasebook_values", {"category": "camera"}),
    ("search_model_prompts", {"queries": ["fox"]}),
    ("search_gallery", {"queries": ["fox"]}),
    ("list_prompts", {}),
    ("read_memory", {"scope": "all"}),
    ("manage_collections", {"operation": "list", "scope": "history"}),
    ("organize_gallery", {"operation": "list_recent"}),
    ("organize_gallery", {"operation": "list_tags"}),
]


class TestReadToolsWithoutSessionContext:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,arguments", READ_CALLS, ids=[f"{c[0]}-{c[1].get('operation', '')}" for c in READ_CALLS])
    async def test_read_tool_succeeds(self, world, name, arguments):
        result = await _call(world, name, arguments)

        assert result["isError"] is False, result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_get_model_info_succeeds_for_an_assigned_model(self, world):
        result = await _call(world, "get_model_info", {"model_id": world.model.id})

        assert result["isError"] is False, result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_get_prompt_returns_the_saved_prompt(self, world):
        result = await _call(world, "get_prompt", {"prompt_id": world.prompt.id})

        assert result["isError"] is False
        assert _payload(result)["segments"][0]["content"] == "a red fox"

    @pytest.mark.asyncio
    async def test_read_tools_return_the_seeded_rows(self, world):
        segments = _payload(await _call(world, "get_saved_segments", {}))
        phrase_values = _payload(await _call(world, "get_phrasebook_values", {"category_id": "pb-1"}))
        presets = _payload(await _call(world, "list_presets", {}))
        searched = _payload(await _call(world, "search_model_prompts", {"queries": ["fox"]}))
        recent = _payload(await _call(world, "organize_gallery", {"operation": "list_recent"}))
        library = _payload(await _call(world, "list_library_items", {}))
        foreign_library = _payload(await _call(world, "list_library_items", {}, user_id="user-2"))

        assert [s["id"] for s in segments["segments"]] == [world.segment.id]
        assert [v["id"] for v in phrase_values["values"]] == ["pbv-1"]
        assert [p["id"] for p in presets["presets"]] == ["preset-a"]
        assert [p["name"] for p in searched["results"][0]["prompts"]] == ["Study"]
        assert [g["id"] for g in recent["generations"]] == ["gen-text"]
        assert [i["id"] for i in library["items"]] == [world.upload.id]
        assert foreign_library["items"] == []


class TestReadMemory:
    @pytest.mark.asyncio
    async def test_scope_all_returns_every_note_across_scopes_with_a_context_note(self, world):
        payload = _payload(await _call(world, "read_memory", {"scope": "all"}))

        assert {n["key"] for n in payload["notes"]} == {"g-note", "p-note", "m-note", "s-note"}
        assert {n["scope"] for n in payload["notes"]} == {"global", "preset", "model", "session"}
        assert payload["count"] == 4
        assert "No open Generate tab" in payload["context"]


class TestSearchGalleryFallback:
    @pytest.mark.asyncio
    async def test_without_a_vision_embedder_it_falls_back_to_text_search_over_history(self, world):
        payload = _payload(await _call(world, "search_gallery", {"queries": ["fox"]}))

        assert payload["search_mode"] == "text"
        assert payload["note"]
        assert [m["generation_id"] for m in payload["results"][0]["matches"]] == ["gen-text"]
        assert world.indexer.search_calls == []
        assert world.history.async_calls[0]["search"] == "fox"

    @pytest.mark.asyncio
    async def test_with_a_vision_embedder_it_uses_the_visual_path(self, world):
        world.indexer.vision_embedder = _FakeVisionEmbedder(True)

        payload = _payload(await _call(world, "search_gallery", {"queries": ["fox"]}))

        assert payload["search_mode"] == "visual"
        assert "note" not in payload
        assert [m["generation_id"] for m in payload["results"][0]["matches"]] == ["gen-visual"]
        assert world.indexer.search_calls == [("user-1", "fox")]
        assert world.history.async_calls == []


class TestOwnerScoping:
    @pytest.mark.asyncio
    async def test_a_second_user_sees_none_of_the_first_users_data(self, world):
        owner_view = _payload(await _call(world, "list_phrasebook_categories", {}, "user-1"))
        segments = _payload(await _call(world, "get_saved_segments", {}, "user-2"))
        phrase_categories = _payload(await _call(world, "list_phrasebook_categories", {}, "user-2"))
        memory = _payload(await _call(world, "read_memory", {"scope": "all"}, "user-2"))
        prompt = await _call(world, "get_prompt", {"prompt_id": world.prompt.id}, "user-2")

        assert "camera" in json.dumps(owner_view)
        assert segments["segments"] == []
        assert "camera" not in json.dumps(phrase_categories)
        assert memory["notes"] == []
        assert prompt["isError"] is True

    @pytest.mark.asyncio
    async def test_a_second_user_cannot_delete_the_first_users_saved_segment(self, world):
        result = await _call(world, "delete_saved_segment", {"segment_id": world.segment.id}, "user-2")

        assert result["isError"] is True
        assert world.segments.get_by_id(world.segment.id, "user-1") is not None

    @pytest.mark.asyncio
    async def test_a_second_user_cannot_delete_the_first_users_phrasebook_category(self, world):
        result = await _call(world, "delete_phrasebook_category", {"category": "pb-1"}, "user-2")

        assert result["isError"] is True
        assert world.phrase_categories.get_by_id("pb-1", "user-1") is not None

    @pytest.mark.asyncio
    async def test_the_owner_can_delete_their_own_saved_segment(self, world):
        result = await _call(world, "delete_saved_segment", {"segment_id": world.segment.id})

        assert result["isError"] is False
        assert world.segments.get_by_id(world.segment.id, "user-1") is None


class TestErrorScrubbing:
    @pytest.mark.asyncio
    async def test_absolute_paths_in_tool_errors_do_not_reach_the_client(self, world):
        world.registry.register(_PathLeakTool())

        result = await _call(world, "leaky", {})

        assert result["isError"] is True
        assert "/srv/" not in result["content"][0]["text"]
        assert "cannot open" in result["content"][0]["text"]
