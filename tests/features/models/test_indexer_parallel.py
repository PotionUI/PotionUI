import threading

import pytest

from src.features.models.indexer import ModelScanner
from src.features.models.repository import model_repo
from tests.features.models.test_indexer_roots import _binding, _models, _resolver, _root, _write

ITERATIONS = 20


@pytest.mark.parametrize("iteration", range(ITERATIONS))
def test_two_new_same_bytes_files_hashed_in_parallel_yield_one_model_and_one_duplicate(
    iteration, tmp_path, threadsafe_db, monkeypatch
):
    meet = threading.Barrier(2)
    original = model_repo.get_by_sha256

    def meeting_point(*args, **kwargs):
        found = original(*args, **kwargs)
        try:
            meet.wait(timeout=0.05)
        except threading.BrokenBarrierError:
            pass
        return found

    monkeypatch.setattr(model_repo, "get_by_sha256", meeting_point)
    home = tmp_path / "home"
    _write(home / "checkpoints" / "first.safetensors", b"identical weights")
    _write(home / "checkpoints" / "second.safetensors", b"identical weights")
    resolver = _resolver([_root("r_home", home)], [_binding("r_home", "checkpoint", home / "checkpoints", 0)])
    scanner = ModelScanner(resolver)

    result = scanner.index_models(max_workers=4)

    assert result["failed"] == 0
    assert result["failed_files"] == []
    assert result["indexed"] == 1
    assert len(result["skipped_duplicates"]) == 1
    models = _models()
    assert len(models) == 1
    duplicate = result["skipped_duplicates"][0]
    assert duplicate["same_as"]["model_id"] == models[0].id
    assert duplicate["path"].endswith(("first.safetensors", "second.safetensors"))
    assert models[0].filename != duplicate["path"].rsplit("/", 1)[-1]
    assert len(scanner.locations.list_for_model(models[0].id)) == 1
