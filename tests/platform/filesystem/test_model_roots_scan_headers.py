import pytest

from src.platform.filesystem.model_roots import HOME_ROOT_ID, ModelRootResolver, RootProbe, ensure_home_bindings
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import root_path_key
from src.platform.filesystem.model_types import binding_scans_headers_by_default


def _add_root(repo, root_id):
    repo.insert_root(root_id, root_id, f"/x/{root_id}", root_path_key(f"/x/{root_id}"), "library", False, False, now_iso())


def _flag(repo, root_id, model_type):
    return {(b["root_id"], b["model_type"]): b["scan_headers"] for b in repo.list_bindings()}[(root_id, model_type)]


@pytest.mark.parametrize(
    ("model_type", "subdir", "expected"),
    [
        ("checkpoint", "Stable-diffusion", True),
        ("unet", "unet", True),
        ("diffusion_model", "UNET", True),
        ("checkpoint", "checkpoints", False),
        ("lora", "Stable-diffusion", False),
        ("vae", "unet", False),
    ],
)
def test_the_default_needs_a_header_classified_type_and_a_matching_folder(model_type, subdir, expected):
    assert binding_scans_headers_by_default(model_type, subdir) is expected


def test_new_bindings_take_the_default_from_their_folder_name(mock_db):
    repo = ModelRootRepository()
    _add_root(repo, "r1")

    repo.insert_binding("r1", "checkpoint", "Stable-diffusion", 10, False)
    repo.upsert_binding("r1", "diffusion_model", "diffusion_models", 10)
    repo.upsert_binding("r1", "unet", "unet", 10)

    assert _flag(repo, "r1", "checkpoint") == 1
    assert _flag(repo, "r1", "diffusion_model") == 0
    assert _flag(repo, "r1", "unet") == 1


def test_an_explicit_flag_overrides_the_default(mock_db):
    repo = ModelRootRepository()
    _add_root(repo, "r1")

    repo.insert_binding("r1", "checkpoint", "Stable-diffusion", 10, False, scan_headers=False)
    repo.upsert_binding("r1", "diffusion_model", "diffusion_models", 10, scan_headers=True)

    assert _flag(repo, "r1", "checkpoint") == 0
    assert _flag(repo, "r1", "diffusion_model") == 1


def test_renaming_a_binding_folder_keeps_the_admins_choice(mock_db):
    repo = ModelRootRepository()
    _add_root(repo, "r1")
    repo.insert_binding("r1", "checkpoint", "checkpoints", 10, False)
    repo.set_scan_headers("r1", "checkpoint", True)

    repo.upsert_binding("r1", "checkpoint", "renamed", 10)

    assert _flag(repo, "r1", "checkpoint") == 1


def test_set_scan_headers_reports_whether_a_binding_existed(mock_db):
    repo = ModelRootRepository()
    assert repo.set_scan_headers("nope", "checkpoint", True) is False


def test_the_resolver_snapshot_carries_the_flag(mock_db, tmp_path):
    repo = ModelRootRepository()
    _add_root(repo, "r1")
    repo.insert_binding("r1", "checkpoint", "Stable-diffusion", 10, False)
    repo.insert_binding("r1", "diffusion_model", "diffusion_models", 10, False)
    resolver = ModelRootResolver(repo, RootProbe(), tmp_path)

    by_type = {t: [d for d in resolver.type_dirs(t, online_only=False) if d.root_id == "r1"][0] for t in ("checkpoint", "diffusion_model")}

    assert by_type["checkpoint"].scan_headers is True
    assert by_type["diffusion_model"].scan_headers is False


def test_home_bindings_scan_only_the_unet_folder_by_default(mock_db, tmp_path):
    repo = ModelRootRepository()
    with mock_db.get_cursor() as cursor:
        cursor.execute("DELETE FROM model_root_bindings")
        cursor.execute("DELETE FROM model_roots")

    ensure_home_bindings(repo, str(tmp_path / "models"), base_dir=tmp_path)

    assert _flag(repo, HOME_ROOT_ID, "unet") == 1
    assert _flag(repo, HOME_ROOT_ID, "checkpoint") == 0
    assert _flag(repo, HOME_ROOT_ID, "diffusion_model") == 0
