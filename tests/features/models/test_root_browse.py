import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.models import root_browse
from src.features.models.root_browse import BrowseError, browse_subfolders
from src.features.models.roots_routes import build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


def _touch(base: Path, *relatives: str) -> None:
    for relative in relatives:
        target = base / relative
        if relative.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")


def _names(result):
    return [f["name"] for f in result["folders"]]


def _by(result, name):
    return next(f for f in result["folders"] if f["name"] == name)


class TestBrowseSubfolders:
    def test_lists_child_folders_sorted_with_model_hints(self, tmp_path):
        _touch(tmp_path, "zeta/", "alpha/a.safetensors", "beta/deep/er/b.gguf", "empty/", "notes.txt", ".hidden/x.safetensors")
        result = browse_subfolders(str(tmp_path))
        assert _names(result) == ["alpha", "beta", "empty", "zeta"]
        assert [_by(result, n)["has_models"] for n in ("alpha", "beta", "empty", "zeta")] == [True, True, False, False]
        assert result["sub"] == "" and result["parent"] is None and result["truncated"] is False
        assert result["has_models"] is True

    def test_only_directories_are_listed(self, tmp_path):
        _touch(tmp_path, "a.safetensors", "readme.md", "dir/")
        assert _names(browse_subfolders(str(tmp_path))) == ["dir"]

    def test_navigating_into_a_subfolder_reports_parents_and_full_subdirs(self, tmp_path):
        _touch(tmp_path, "models/loras/x.safetensors", "models/vae/")
        result = browse_subfolders(str(tmp_path), "models")
        assert result["sub"] == "models" and result["parent"] == ""
        assert [f["subdir"] for f in result["folders"]] == ["models/loras", "models/vae"]
        deeper = browse_subfolders(str(tmp_path), "models/loras")
        assert deeper["parent"] == "models" and deeper["folders"] == []
        assert deeper["has_models"] is True

    def test_backslash_and_edge_slashes_are_normalised(self, tmp_path):
        _touch(tmp_path, "a/b/")
        assert browse_subfolders(str(tmp_path), "a\\b\\")["sub"] == "a/b"

    def test_empty_folder(self, tmp_path):
        result = browse_subfolders(str(tmp_path))
        assert result["folders"] == [] and result["has_models"] is False

    @pytest.mark.parametrize("sub", ["..", "../x", "a/../../x", "/etc", "/abs", "\\abs"])
    def test_sub_must_stay_relative_and_inside(self, tmp_path, sub):
        (tmp_path / "a").mkdir()
        with pytest.raises(BrowseError) as caught:
            browse_subfolders(str(tmp_path), sub)
        assert caught.value.status == 400

    def test_depth_is_bounded(self, tmp_path):
        deep = "/".join(f"d{i}" for i in range(root_browse.MAX_SUB_DEPTH + 1))
        (tmp_path / deep).mkdir(parents=True)
        with pytest.raises(BrowseError) as caught:
            browse_subfolders(str(tmp_path), deep)
        assert "deeper" in caught.value.reason

    def test_missing_root_and_missing_sub_are_not_found(self, tmp_path):
        with pytest.raises(BrowseError) as missing_root:
            browse_subfolders(str(tmp_path / "nope"))
        with pytest.raises(BrowseError) as missing_sub:
            browse_subfolders(str(tmp_path), "nope")
        assert missing_root.value.status == 404 and missing_sub.value.status == 404

    def test_a_file_is_not_a_folder(self, tmp_path):
        _touch(tmp_path, "file.txt")
        with pytest.raises(BrowseError):
            browse_subfolders(str(tmp_path / "file.txt"))
        with pytest.raises(BrowseError):
            browse_subfolders(str(tmp_path), "file.txt")

    def test_blank_path_is_refused(self):
        with pytest.raises(BrowseError):
            browse_subfolders("   ")

    def test_links_leaving_the_root_are_hidden_and_not_followed(self, tmp_path):
        outside = tmp_path / "outside"
        _touch(outside, "secret/x.safetensors")
        root = tmp_path / "root"
        _touch(root, "real/")
        try:
            os.symlink(outside, root / "escape", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        result = browse_subfolders(str(root))
        assert _names(result) == ["real"]
        with pytest.raises(BrowseError) as caught:
            browse_subfolders(str(root), "escape")
        assert caught.value.status == 400

    def test_links_inside_the_root_are_listed_and_marked(self, tmp_path):
        _touch(tmp_path, "real/x.safetensors")
        try:
            os.symlink(tmp_path / "real", tmp_path / "alias", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        result = browse_subfolders(str(tmp_path))
        assert _by(result, "alias")["linked"] is True and _by(result, "alias")["has_models"] is True
        assert _by(result, "real")["linked"] is False

    def test_the_root_may_itself_be_reached_through_a_link(self, tmp_path):
        _touch(tmp_path, "real/inner/")
        try:
            os.symlink(tmp_path / "real", tmp_path / "front", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        assert _names(browse_subfolders(str(tmp_path / "front"))) == ["inner"]

    def test_an_exhausted_budget_stops_the_listing(self, tmp_path):
        _touch(tmp_path, "a/", "b/", "c/")
        ticks = iter(range(0, 1000, 10))
        result = browse_subfolders(str(tmp_path), clock=lambda: next(ticks))
        assert result["truncated"] is True and result["folders"] == []

    def test_the_folder_count_is_capped(self, tmp_path, monkeypatch):
        _touch(tmp_path, "a/", "b/", "c/", "d/")
        monkeypatch.setattr(root_browse, "MAX_FOLDERS", 2)
        result = browse_subfolders(str(tmp_path))
        assert _names(result) == ["a", "b"] and result["truncated"] is True

    def test_the_model_hint_scan_is_bounded(self, tmp_path, monkeypatch):
        _touch(tmp_path, "big/one.txt", "big/two.txt", "big/three.txt", "big/sub/four.safetensors")
        monkeypatch.setattr(root_browse, "HINT_ENTRY_LIMIT", 2)
        assert _by(browse_subfolders(str(tmp_path)), "big")["has_models"] is False


def _client(role):
    container = SimpleNamespace(
        model_roots_manager=Mock(),
        model_index_manager=SimpleNamespace(indexing=Mock()),
        model_layout_catalog=None,
    )
    app = FastAPI()
    app.include_router(build_router(container))
    user = User(id="u1", username="u", email="u@example.com", password_hash="h", account_type=role)
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


class TestBrowseRoute:
    def test_admin_browses_a_folder(self, tmp_path):
        _touch(tmp_path, "loras/a.safetensors", "vae/")
        response = _client(AccountType.ADMIN).get("/api/models/roots/browse", params={"path": str(tmp_path)})
        assert response.status_code == 200
        data = response.json()["data"]
        assert [f["name"] for f in data["folders"]] == ["loras", "vae"]

    def test_sub_is_passed_through(self, tmp_path):
        _touch(tmp_path, "models/loras/")
        response = _client(AccountType.ADMIN).get(
            "/api/models/roots/browse", params={"path": str(tmp_path), "sub": "models"})
        assert response.json()["data"]["folders"][0]["subdir"] == "models/loras"

    def test_regular_user_is_denied(self, tmp_path):
        response = _client(AccountType.USER).get("/api/models/roots/browse", params={"path": str(tmp_path)})
        assert response.status_code == 403

    def test_traversal_is_a_bad_request(self, tmp_path):
        response = _client(AccountType.ADMIN).get(
            "/api/models/roots/browse", params={"path": str(tmp_path), "sub": "../x"})
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "model_roots_browse_failed"

    def test_missing_folder_is_not_found(self, tmp_path):
        response = _client(AccountType.ADMIN).get("/api/models/roots/browse", params={"path": str(tmp_path / "gone")})
        assert response.status_code == 404

    def test_path_is_required(self):
        assert _client(AccountType.ADMIN).get("/api/models/roots/browse").status_code == 422
