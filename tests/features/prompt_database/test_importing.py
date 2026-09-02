"""`import_prompts` end to end against a real repository on a scratch DB.

Vector store, embedding provider, and the plugin registry are mocked - only
persistence needs to be real here (pairing is a repository-column concern),
matching the `test_repository.py` / `operations/test_mutations.py` split.

Plain pytest functions, not a `unittest.TestCase` - `PersistenceTestBase`'s
`setUp`/`tearDown` are driven directly through the `persistence` fixture
below rather than subclassed, so `async def test_*` here is genuinely
awaited (see `tests/architecture/test_async_unittest_testcase.py`).
"""
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.operations.importing import import_prompts
from src.features.prompt_database.operations.mutations import UnknownModelError
from src.features.prompt_database.repository import PromptRepository
from tests.fixtures.persistence_base import PersistenceTestBase


@pytest.fixture
def persistence():
    base = PersistenceTestBase()
    base.setUp()
    try:
        yield base
    finally:
        base.tearDown()


@pytest.fixture
def user_id(persistence):
    return persistence.create_test_user("import-user-1", "importuser1", "import1@example.com")


@pytest.fixture
def collaborators(persistence):
    embedding_provider = MagicMock()
    embedding_provider.embed = AsyncMock(return_value=[[0.1, 0.2]])
    embedding_provider.is_available = AsyncMock(return_value=False)
    return PromptDatabaseCollaborators(
        repository=PromptRepository(),
        vector_store=MagicMock(),
        embedding_provider=embedding_provider,
        plugin_registry=MagicMock(),
        model_repository=ModelRepository(),
    )


@pytest.fixture
def catalog_model(collaborators):
    """A real catalog row: `prompts.model_id` is a foreign key onto `models`,
    and the name the importer stores is the row's `display_name`."""
    return collaborators.model_repository.create(Model(
        filename="Caller Model.safetensors", file_path="/models/Caller Model.safetensors",
        file_size=1, sha256="a" * 64, model_type="checkpoint",
    ))


def _a1111_png(*, model_name: str = "myModel", seed: int = 1) -> bytes:
    image = Image.new("RGB", (4, 4))
    info = PngInfo()
    info.add_text(
        "parameters",
        "a fox\nNegative prompt: blurry\n"
        f"Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: {seed}, Size: 512x512, Model: {model_name}",
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def _plain_png() -> bytes:
    image = Image.new("RGB", (4, 4))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def test_styles_csv_pair_shares_source_group_id_on_persisted_rows(collaborators, user_id):
    csv_bytes = (
        b'name,prompt,negative_prompt\n'
        b'Cinematic,a hero on a cliff,"blurry, low quality"\n'
    )
    outcome = await import_prompts(collaborators, user_id, [("styles.csv", csv_bytes)])

    assert outcome.imported == 2
    assert outcome.skipped == 0
    assert outcome.total == 2
    positive = next(item for item in outcome.items if item["usage_hint"] == "positive")
    negative = next(item for item in outcome.items if item["usage_hint"] == "negative")
    assert positive["source_group_id"] == negative["source_group_id"]
    assert positive["source_group_id"] is not None
    assert positive["source_provider"] == "import"

    stored_positive = collaborators.repository.get_by_id(positive["id"], user_id)
    stored_negative = collaborators.repository.get_by_id(negative["id"], user_id)
    assert stored_positive.source_group_id == stored_negative.source_group_id


async def test_caller_model_id_lands_on_every_prompt_and_its_catalog_name_fills_the_gap(
    collaborators, user_id, catalog_model,
):
    csv_bytes = b'name,prompt,negative_prompt\nA,only positive,\n'
    outcome = await import_prompts(
        collaborators, user_id, [("styles.csv", csv_bytes)],
        model_id=catalog_model.id, base_model="SDXL",
    )

    assert outcome.imported == 1
    assert outcome.items[0]["model_id"] == catalog_model.id
    assert outcome.items[0]["model_name"] == "Caller Model"
    assert outcome.items[0]["base_model"] == "SDXL"


async def test_parser_reported_model_name_survives_a_caller_model_id(collaborators, user_id, catalog_model):
    outcome = await import_prompts(
        collaborators, user_id, [("shot.png", _a1111_png(model_name="fromImage"))],
        model_id=catalog_model.id,
    )

    positive = next(item for item in outcome.items if item["usage_hint"] == "positive")
    assert positive["model_name"] == "fromImage"
    assert positive["model_id"] == catalog_model.id


async def test_unknown_caller_model_id_fails_before_any_file_is_read(collaborators, user_id):
    with pytest.raises(UnknownModelError):
        await import_prompts(
            collaborators, user_id, [("styles.csv", b'name,prompt,negative_prompt\nA,x,\n')],
            model_id="missing",
        )
    assert collaborators.repository.count(user_id) == 0


async def test_one_bad_file_does_not_sink_the_batch(collaborators, user_id):
    good = b'name,prompt,negative_prompt\nA,ok prompt,\n'
    bad = b"{not valid json"
    outcome = await import_prompts(collaborators, user_id, [
        ("good.csv", good),
        ("bad.json", bad),
    ])

    assert outcome.imported == 1
    assert len(outcome.files) == 2
    bad_file = next(f for f in outcome.files if f["filename"] == "bad.json")
    good_file = next(f for f in outcome.files if f["filename"] == "good.csv")
    assert bad_file["imported"] == 0
    assert bad_file.get("reason")
    assert good_file["imported"] == 1


async def test_outcome_files_shape_reports_per_file_counts_and_format(collaborators, user_id):
    csv_bytes = b'name,prompt,negative_prompt\nA,pos one,neg one\nB,pos two,\n'
    outcome = await import_prompts(collaborators, user_id, [("mixed.csv", csv_bytes)])

    assert outcome.files == [
        {"filename": "mixed.csv", "format": "styles_csv", "imported": 3, "skipped": 0}
    ]


async def test_empty_file_is_reported_as_skipped_with_empty_reason(collaborators, user_id):
    outcome = await import_prompts(collaborators, user_id, [("empty.txt", b"")])

    assert outcome.imported == 0
    assert outcome.files[0]["reason"] == "empty"


async def test_image_with_no_metadata_is_reported_as_no_metadata(collaborators, user_id):
    outcome = await import_prompts(collaborators, user_id, [("plain.png", _plain_png())])

    assert outcome.imported == 0
    assert outcome.files[0]["reason"] == "no_metadata"


async def test_seed_is_folded_into_metadata_not_a_column(collaborators, user_id):
    style_json = json.dumps([{"name": "A", "prompt": "one"}]).encode()
    outcome = await import_prompts(collaborators, user_id, [("pack.json", style_json)])
    assert "seed" not in outcome.items[0]["metadata"]

    outcome2 = await import_prompts(collaborators, user_id, [("shot.png", _a1111_png(seed=4242))])
    positive = next(item for item in outcome2.items if item["usage_hint"] == "positive")
    assert positive["metadata"]["seed"] == 4242


async def test_explicit_format_override_beats_detection(collaborators, user_id):
    # Content sniffs as `lines`; the caller says it's a wildcard file with one
    # entry per line anyway - override wins over `format=None` autodetection.
    outcome = await import_prompts(
        collaborators, user_id, [("upload", b"a fox\nb bear\n")], format="lines",
    )
    assert outcome.imported == 2
