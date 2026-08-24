"""Reconciling a backend's listing into models + model_availability."""

import pytest
from unittest.mock import Mock

from src.features.backends.model_listing import BackendModel, ModelListingNotSupported
from src.features.models.backend_indexer import BackendModelIndexer


class FakeModel:
    def __init__(self, id, model_type, filename, file_size=None, sha256=None, file_path=None,
                 is_directory=False):
        self.id = id
        self.model_type = model_type
        self.filename = filename
        self.file_size = file_size
        self.sha256 = sha256
        self.file_path = file_path
        self.is_directory = is_directory


class FakeModelRepo:
    def __init__(self, existing=None):
        self.models = list(existing or [])
        self._next = 0

    def get_all(self, **kwargs):
        return list(self.models)

    def create(self, model):
        self._next += 1
        model.id = model.id or f"m{self._next}"
        self.models.append(model)
        return model


class FakeAvailabilityRepo:
    def __init__(self):
        self.rows = {}
        self.deleted_with = None

    def upsert(self, availability):
        self.rows[(availability.model_id, availability.backend_id)] = availability
        return availability

    def delete_for_backend(self, backend_id, keep_model_ids=None):
        stale = [
            key for key in self.rows
            if key[1] == backend_id and key[0] not in (keep_model_ids or set())
        ]
        for key in stale:
            del self.rows[key]
        self.deleted_with = keep_model_ids
        return len(stale)


def make_backend(entries, backend_id="be1", supports=True):
    backend = Mock()
    backend.backend_id = backend_id
    backend.name = "Test Backend"
    backend.engine = "comfyui"
    backend.supports_model_listing = Mock(return_value=supports)

    async def _list():
        return entries
    backend.list_models = _list
    return backend


def indexer(existing=None):
    models = FakeModelRepo(existing)
    avail = FakeAvailabilityRepo()
    return BackendModelIndexer(models, avail), models, avail


@pytest.mark.asyncio
async def test_unsupported_backend_raises_rather_than_reporting_zero_models():
    """Silently returning [] would delete every availability row for that backend."""
    idx, _, _ = indexer()
    with pytest.raises(ModelListingNotSupported):
        await idx.index_backend(make_backend([], supports=False))


@pytest.mark.asyncio
async def test_new_remote_model_gets_a_row_with_null_file_path():
    """A model only a remote server has never existed on this host."""
    idx, models, avail = indexer()
    entry = BackendModel("lora", "detail.safetensors", "style/detail.safetensors", size=100)

    result = await idx.index_backend(make_backend([entry]))

    assert result.created == 1 and result.matched == 0
    created = models.models[0]
    assert created.file_path is None
    assert created.sha256 is None
    row = avail.rows[(created.id, "be1")]
    assert row.ref == "style/detail.safetensors"
    assert row.confidence == "reported"


@pytest.mark.asyncio
async def test_same_filename_on_two_backends_merges_into_one_model():
    """The whole point: identity is (model_type, filename), not a path or a hash."""
    existing = FakeModel("m1", "lora", "detail.safetensors",
                         file_size=100, sha256="abc", file_path="models/loras/detail.safetensors")
    idx, models, avail = indexer([existing])

    entry = BackendModel("lora", "detail.safetensors", "style/detail.safetensors", size=100)
    result = await idx.index_backend(make_backend([entry], backend_id="comfy1"))

    assert result.created == 0 and result.matched == 1
    assert len(models.models) == 1
    assert avail.rows[("m1", "comfy1")].ref == "style/detail.safetensors"


@pytest.mark.asyncio
async def test_same_filename_different_size_merges_but_reports_a_conflict():
    """A quantised copy keeping its filename must not merge silently."""
    existing = FakeModel("m1", "checkpoint", "flux.safetensors", file_size=23_000_000_000)
    idx, _, _ = indexer([existing])

    entry = BackendModel("checkpoint", "flux.safetensors", "flux.safetensors", size=11_000_000_000)
    result = await idx.index_backend(make_backend([entry], backend_id="comfy1"))

    assert result.matched == 1
    assert len(result.size_conflicts) == 1
    conflict = result.size_conflicts[0]
    assert conflict.known_size == 23_000_000_000
    assert conflict.reported_size == 11_000_000_000
    assert conflict.backend_id == "comfy1"


@pytest.mark.asyncio
async def test_missing_size_on_either_side_is_not_a_conflict():
    """`/models/{folder}` reports no size; absence is not disagreement."""
    existing = FakeModel("m1", "lora", "detail.safetensors", file_size=100)
    idx, _, _ = indexer([existing])

    entry = BackendModel("lora", "detail.safetensors", "detail.safetensors", size=None)
    result = await idx.index_backend(make_backend([entry]))

    assert result.size_conflicts == []
    assert result.matched == 1


@pytest.mark.asyncio
async def test_repeated_identity_with_conflicting_sizes_is_flagged_not_guessed():
    """deduplicate() collapses identical (name,size); what survives is real ambiguity."""
    idx, models, _ = indexer()
    entries = [
        BackendModel("lora", "model.safetensors", "variant_a/model.safetensors", size=100),
        BackendModel("lora", "model.safetensors", "variant_b/model.safetensors", size=200),
    ]

    result = await idx.index_backend(make_backend(entries))

    assert len(models.models) == 1, "one identity -> one model row"
    assert len(result.ambiguous) == 1
    assert "variant_a/model.safetensors" in result.ambiguous[0]


@pytest.mark.asyncio
async def test_model_removed_from_backend_stops_being_offered():
    """Stale availability would let the picker offer a model the engine cannot load."""
    idx, _, avail = indexer()
    first = BackendModel("lora", "a.safetensors", "a.safetensors", size=1)
    second = BackendModel("lora", "b.safetensors", "b.safetensors", size=2)

    await idx.index_backend(make_backend([first, second]))
    assert len(avail.rows) == 2

    result = await idx.index_backend(make_backend([first]))

    assert result.removed == 1
    assert len(avail.rows) == 1
    assert all(ref.ref == "a.safetensors" for ref in avail.rows.values())


@pytest.mark.asyncio
async def test_reindexing_does_not_duplicate_availability_rows():
    idx, models, avail = indexer()
    entry = BackendModel("lora", "detail.safetensors", "style/detail.safetensors", size=100)
    backend = make_backend([entry])

    await idx.index_backend(backend)
    await idx.index_backend(backend)

    assert len(models.models) == 1
    assert len(avail.rows) == 1


@pytest.mark.asyncio
async def test_confidence_tracks_what_the_backend_proved():
    idx, _, avail = indexer()
    entries = [
        BackendModel("lora", "hashed.safetensors", "hashed.safetensors", size=1, sha256="deadbeef"),
        BackendModel("lora", "sized.safetensors", "sized.safetensors", size=2),
        BackendModel("lora", "bare.safetensors", "bare.safetensors"),
    ]

    await idx.index_backend(make_backend(entries))

    by_ref = {a.ref: a.confidence for a in avail.rows.values()}
    assert by_ref["hashed.safetensors"] == "verified"
    assert by_ref["sized.safetensors"] == "reported"
    assert by_ref["bare.safetensors"] == "name_only"


# --- digest conflict: recorded AND blocks, not just warned ------------------------

@pytest.mark.asyncio
async def test_digest_mismatch_on_a_matched_model_is_flagged_and_marks_the_row_conflict():
    """A remote worker one rsync behind reports the right name/size, wrong bytes."""
    existing = FakeModel("m1", "checkpoint", "flux.safetensors", file_size=100, sha256="a" * 64)
    idx, _, avail = indexer([existing])

    entry = BackendModel("checkpoint", "flux.safetensors", "flux.safetensors", size=100, sha256="b" * 64)
    result = await idx.index_backend(make_backend([entry], backend_id="remote1"))

    assert len(result.digest_conflicts) == 1
    conflict = result.digest_conflicts[0]
    assert conflict.known_digest == "a" * 64
    assert conflict.reported_digest == "b" * 64
    assert conflict.backend_id == "remote1"

    row = avail.rows[("m1", "remote1")]
    assert row.confidence == "conflict"
    assert row.digest == "b" * 64


@pytest.mark.asyncio
async def test_matching_digest_on_a_matched_model_stays_verified():
    """The common case: re-indexing the same, unchanged file must not flag a conflict."""
    existing = FakeModel("m1", "checkpoint", "flux.safetensors", file_size=100, sha256="a" * 64)
    idx, _, avail = indexer([existing])

    entry = BackendModel("checkpoint", "flux.safetensors", "flux.safetensors", size=100, sha256="a" * 64)
    result = await idx.index_backend(make_backend([entry], backend_id="remote1"))

    assert result.digest_conflicts == []
    assert avail.rows[("m1", "remote1")].confidence == "verified"


@pytest.mark.asyncio
async def test_no_digest_from_either_side_is_not_a_conflict():
    """ComfyUI never hashes; absence must not be manufactured into a conflict."""
    existing = FakeModel("m1", "lora", "detail.safetensors", file_size=100)
    idx, _, avail = indexer([existing])

    entry = BackendModel("lora", "detail.safetensors", "detail.safetensors", size=100)
    result = await idx.index_backend(make_backend([entry], backend_id="comfy1"))

    assert result.digest_conflicts == []
    assert avail.rows[("m1", "comfy1")].confidence == "reported"


@pytest.mark.asyncio
async def test_directory_model_fingerprint_is_never_compared_as_a_digest():
    """101_add_model_is_directory.py: `sha256` on a directory row is a cheap
    fingerprint, not a content hash - comparing it as one would manufacture
    false conflicts for every HF-layout checkpoint on every re-index."""
    existing = FakeModel("m1", "llm", "qwen3", file_size=100, sha256="a" * 64, is_directory=True)
    idx, _, avail = indexer([existing])

    entry = BackendModel("llm", "qwen3", "qwen3", size=100, sha256="b" * 64)
    result = await idx.index_backend(make_backend([entry], backend_id="remote1"))

    assert result.digest_conflicts == []
    assert avail.rows[("m1", "remote1")].confidence == "verified"
