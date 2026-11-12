from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.forms.binding import BoundForm
from src.features.generation.dto import GenerationRequest
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.repository import GenerationRepository
from src.features.generation.status_tracker import GenerationStatusTracker
from src.features.kits import operations
from src.features.kits.expansion import KitExpander
from src.features.kits.repository import KitRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from tests.features.kits.test_expansion import ALL_KINDS, native
from tests.features.video_director.test_normalize import H3_REFS_CAPS
from tests.fixtures.seed import seed_user

SEEN = []


@pytest.fixture(autouse=True)
def _bind_rejects_kit_rows():
    def bind(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None, **_):
        for value in (raw_form_data or {}).values():
            for item in value if isinstance(value, list) else []:
                assert not (isinstance(item, dict) and "kit" in item and "path" not in item), "a kit row reached bind_form"
        SEEN.append(raw_form_data)
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=bind):
        yield


class Harness:
    def __init__(self, db, expander=True):
        for user_id in ("u1", "u2"):
            seed_user(db, user_id)
        self.kits = KitRepository()
        self.uploads = UploadRepository()
        self.generations = GenerationRepository()
        self.backend = Mock(backend_id="b1", engine="native", name="Local")
        self.backend.start_generation = AsyncMock()
        registry = Mock()
        registry.select_backend_for_generation = Mock(return_value=self.backend)
        registry.get_backend = Mock(return_value=self.backend)
        self.preset = Mock(engine="native", version="1.0.0", prompt_resources={"refs": ALL_KINDS})
        self.preset.vars = {}
        self.preset.modes = {}
        self.builder = Mock()
        self.builder.build_pipeline = Mock(return_value=Mock(pipes=[{"name": "generator", "config": {}}], preset_template=self.preset))
        self.orchestrator = GenerationOrchestrator(
            pipeline_builder=self.builder,
            backend_registry=registry,
            connection_hub=Mock(),
            settings=Mock(get_setting=Mock(return_value="/outputs"), get_file_storage_directory=Mock(return_value="/storage")),
            output_processor=Mock(process_output=AsyncMock(return_value={"handler": "H", "processed": True})),
            preset_template_loader=Mock(load_preset_by_id=Mock(return_value=self.preset)),
            status_tracker=GenerationStatusTracker(),
            kit_expander=KitExpander(self.kits) if expander else None,
        )
        self.orchestrator._notifier.notify_warning = Mock()
        self.kit = operations.create_kit(self.kits, "u1", "Mara")

    def add(self, name, kind="image", filename=None):
        upload = self.uploads.create(Upload(
            user_id="u1", filename=filename or f"{name}.png", media_type=kind, original_filename=f"{name}.png",
        ))
        return operations.add_media_item(self.kits, "u1", self.kit.id, upload.id, name=name), upload

    def request(self, prompt, form_data):
        return GenerationRequest(preset_id="preset-1", prompt=prompt, form_data=form_data, mode="refs")

    async def submit(self, prompt, form_data, user_id="u1"):
        return await self.orchestrator.start_generation(self.request(prompt, form_data), user_id)

    def stored_form(self, result):
        return self.generations.get_by_id(result["generation_id"]).form_data


@pytest.fixture
def harness(mock_db):
    SEEN.clear()
    return Harness(mock_db)


@pytest.mark.asyncio
async def test_loaded_kit_rows_never_reach_bind_and_only_the_cited_item_is_stored(harness):
    face, _ = harness.add("face")
    harness.add("spare")
    cite = f"@[references:kit:{harness.kit.id}/{face.id}]"

    result = await harness.submit(
        f"{cite} at dawn", {"references": [native("uploads/own.png"), {"kit": harness.kit.id}]},
    )

    stored = harness.stored_form(result)["references"]
    assert [item["relative_path"] for item in stored] == ["uploads/own.png", "uploads/face.png"]
    assert stored[1]["kit_item"]["name"] == "face"
    assert "spare" not in str(harness.stored_form(result))
    assert SEEN[0]["references"] == [native("uploads/own.png")]
    assert result["kits"]["used"][0]["name"] == "face"
    assert result["kits"]["notices"] == []
    harness.orchestrator._notifier.notify_warning.assert_not_called()


@pytest.mark.asyncio
async def test_the_prompt_the_model_gets_has_the_models_own_token(harness):
    face, _ = harness.add("face")
    request = harness.request(f"@[references:kit:{harness.kit.id}/{face.id}] smiles", {"references": [{"kit": harness.kit.id}]})

    await harness.orchestrator.start_generation(request, "u1")

    assert request.prompts[0].positive == "<Picture 1> smiles"


@pytest.mark.asyncio
async def test_skipped_items_are_reported_in_the_result_and_as_a_warning(harness):
    voice, _ = harness.add("voice", "audio", "voice.wav")
    harness.preset.prompt_resources = {"refs": [ALL_KINDS[0]]}

    result = await harness.submit(
        f"hear @[references:kit:{harness.kit.id}/{voice.id}]", {"references": [{"kit": harness.kit.id}]},
    )

    assert result["generation_id"]
    assert [(n["code"], n["item"]) for n in result["kits"]["notices"]] == [("unsupported_kind", "voice")]
    harness.orchestrator._notifier.notify_warning.assert_called_once()
    assert "voice" in harness.orchestrator._notifier.notify_warning.call_args.args[2]


@pytest.mark.asyncio
async def test_a_deleted_library_file_never_blocks_the_submission(harness):
    face, upload = harness.add("face")
    harness.uploads.delete(upload.filename, "u1")

    result = await harness.submit(
        f"@[references:kit:{harness.kit.id}/{face.id}]", {"references": [{"kit": harness.kit.id}]},
    )

    assert [n["code"] for n in result["kits"]["notices"]] == ["file_missing"]
    assert harness.stored_form(result)["references"] == []


@pytest.mark.asyncio
async def test_another_users_kit_cannot_be_loaded(harness):
    face, _ = harness.add("face")

    result = await harness.submit(
        f"@[references:kit:{harness.kit.id}/{face.id}]", {"references": [{"kit": harness.kit.id}]}, user_id="u2",
    )

    assert {n["code"] for n in result["kits"]["notices"]} == {"kit_deleted"}
    assert harness.stored_form(result)["references"] == []


@pytest.mark.asyncio
async def test_a_submission_with_no_kits_answers_exactly_as_before(harness):
    form = {"references": [native("uploads/own.png")]}

    result = await harness.submit("@[references:uploads/own.png] walks", form)

    assert "kits" not in result
    assert harness.stored_form(result) == form


@pytest.mark.asyncio
async def test_a_director_shot_citation_adds_only_what_a_shot_cites(harness):
    face, _ = harness.add("face")
    harness.add("never")
    harness.preset.vars = {"video_director": {**H3_REFS_CAPS, "reference_fields": ["references"]}}
    document = {
        "schema_version": 1, "mode": "director", "settings": {"fps": 24, "duration": None, "resolution": "", "seed": 7},
        "segments": [{"id": "s0", "prompt": f"@[references:kit:{harness.kit.id}/{face.id}] walks", "frames": 81}],
        "media": [], "audio": [], "ic_lora": [],
    }

    result = await harness.submit("", {"references": [{"kit": harness.kit.id}], "video_director": document})

    stored = harness.stored_form(result)
    assert [item["kit_item"]["name"] for item in stored["references"]] == ["face"]
    assert "never" not in str(stored)
    assert stored["video_director"]["segments"][0]["reference_indices"] == [0]
    assert stored["video_director"]["segments"][0]["prompt"] == "<Picture 1> walks"
