import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.history_facade import GenerationHistoryFacade
from src.platform.filesystem.file_store import FileStore


def safety(restricted, safe_ids):
    return SimpleNamespace(
        is_restricted=lambda user_id: restricted,
        viewable_generation_ids=lambda user_id, ids: {i for i in ids if i in safe_ids},
    )


def facade(tmp_path, content_safety):
    repo = Mock()
    repo.get_by_id.return_value = Mock()
    for name in ("gen1", "gen2"):
        path = tmp_path / f"generations/2025-01-01/{name}/0.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 4)).save(path)
    file_for = lambda gid: Mock(file_path=f"generations/2025-01-01/{gid}/0.png", file_type="IMAGE")
    repo.get_files.side_effect = lambda gid, user_id=None, is_final=None: [file_for(gid)]
    return GenerationHistoryFacade(
        generation_repo=repo,
        file_service=FileStore(str(tmp_path)),
        plugin_registry=Mock(),
        run_report_repository=Mock(),
        content_safety=content_safety,
    )


def test_zip_export_for_a_restricted_user_skips_generations_that_are_not_entirely_safe(tmp_path):
    zip_file, _ = facade(tmp_path, safety(True, {"gen1"})).export_zip(["gen1", "gen2"], "kid")

    assert zipfile.ZipFile(zip_file).namelist() == ["gen1/0.png"]


def test_zip_export_for_an_unrestricted_user_keeps_everything(tmp_path):
    zip_file, _ = facade(tmp_path, safety(False, set())).export_zip(["gen1", "gen2"], "adult")

    assert sorted(zipfile.ZipFile(zip_file).namelist()) == ["gen1/0.png", "gen2/0.png"]


def test_bundle_export_for_a_restricted_user_is_refused_for_a_flagged_generation(tmp_path):
    with pytest.raises(GenerationNotFoundException):
        facade(tmp_path, safety(True, {"gen1"})).export_bundle("gen2", "kid")
