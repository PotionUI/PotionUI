"""Unit tests for tests/e2e/harness/onboarding_e2e.py.

Covers the pure/near-pure parts: env-var assembly for the backend subprocess,
the ephemeral-recipe mutation logic, the read-only models-dir mirror, stage
sequencing against a faked HTTP client (no real network, no real backend
process), and that a failure mid-journey still tears down the ephemeral
instance. Nothing here boots a real backend or touches a GPU - that's
tests/e2e/harness/onboarding_e2e.py itself, meant to be run by a
human/agent, not by this suite.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

# `tests/scripts/` has no __init__.py on purpose (see tests/e2e/harness/'s own
# workaround) - a dotted `tests.e2e.harness.*` import can silently resolve to
# the third-party `tests` package ultralytics ships in site-packages, which
# PYTHONPATH puts ahead of the repo root. Add the harness dir straight to
# sys.path and import it unqualified instead.
_HARNESS_DIR = Path(__file__).resolve().parents[1] / "e2e" / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import onboarding_e2e as e2e  # noqa: E402
import e2e_harness  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body: Optional[Dict[str, Any]] = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text

    def json(self):
        return self._json


class FakeSetupClient:
    """Duck-types `SetupClient`: `.get`/`.post` are queued responses keyed by
    path, so a test can script an exact sequence of server replies without
    any real HTTP traffic."""

    def __init__(self):
        self.base_url = "http://127.0.0.1:0"
        self.token: Optional[str] = None
        self._get_queue: Dict[str, List[FakeResponse]] = {}
        self._post_queue: Dict[str, List[FakeResponse]] = {}
        self.calls: List[tuple] = []

    def queue_get(self, path: str, response: FakeResponse) -> None:
        self._get_queue.setdefault(path, []).append(response)

    def queue_post(self, path: str, response: FakeResponse) -> None:
        self._post_queue.setdefault(path, []).append(response)

    def get(self, path: str, **kwargs) -> FakeResponse:
        self.calls.append(("GET", path))
        queue = self._get_queue.get(path)
        if not queue:
            raise AssertionError(f"FakeSetupClient.get: no stubbed response for {path!r}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def post(self, path: str, timeout: float = 30.0, **kwargs) -> FakeResponse:
        self.calls.append(("POST", path))
        queue = self._post_queue.get(path)
        if not queue:
            raise AssertionError(f"FakeSetupClient.post: no stubbed response for {path!r}")
        return queue.pop(0) if len(queue) > 1 else queue[0]


def make_journey(client, **overrides) -> e2e.Journey:
    defaults = dict(
        client=client,
        instance_dir=Path("/tmp/does-not-matter"),
        storage_dir=Path("/tmp/does-not-matter/storage"),
        recipe_id="sdxl-starter",
        no_gpu=False,
        fresh_download=False,
    )
    defaults.update(overrides)
    return e2e.Journey(**defaults)


# ---------------------------------------------------------------------------
# Port guards
# ---------------------------------------------------------------------------


class TestForbiddenPorts:
    @pytest.mark.parametrize("port", sorted(e2e_harness.FORBIDDEN_PORTS))
    def test_rejects_every_forbidden_port(self, port):
        with pytest.raises(e2e.StageError) as exc:
            e2e.reject_forbidden_port(port)
        assert exc.value.stage == "args"
        assert str(port) in exc.value.message

    def test_allows_the_default_port(self):
        e2e.reject_forbidden_port(e2e.DEFAULT_PORT)  # must not raise

    def test_allows_an_arbitrary_high_port(self):
        e2e.reject_forbidden_port(19999)  # must not raise


class TestFreePort:
    def test_find_free_port_returns_a_bindable_port(self):
        port = e2e_harness.find_free_port()
        assert e2e.port_is_free(port)


# ---------------------------------------------------------------------------
# Env assembly (the env-var seams the subprocess is driven with)
# ---------------------------------------------------------------------------


class TestBuildBackendEnv:
    def _instance(self, tmp_path: Path) -> e2e.EphemeralInstance:
        return e2e.EphemeralInstance(
            instance_dir=tmp_path,
            storage_dir=tmp_path / "storage",
            db_path=tmp_path / "storage" / "db.sqlite",
            models_dir=tmp_path / "models",
            recipes_dir=tmp_path / "recipes",
            port=18055,
        )

    def test_sets_every_documented_seam(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYTHONPATH", raising=False)
        instance = self._instance(tmp_path)
        site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        env = e2e_harness.build_backend_env(instance, site_packages, tmp_path)

        assert env["POTIONUI_DB_PATH"] == str(instance.db_path)
        assert env["POTIONUI_STORAGE_PATH"] == str(instance.storage_dir)
        assert env["POTIONUI_MODELS_DIR"] == str(instance.models_dir)
        assert env["POTIONUI_RECIPES_DIR"] == str(instance.recipes_dir)
        assert str(site_packages) in env["PYTHONPATH"].split(os.pathsep)
        assert str(tmp_path) in env["PYTHONPATH"].split(os.pathsep)

    def test_preserves_an_existing_pythonpath(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/some/existing/path")
        instance = self._instance(tmp_path)
        site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        env = e2e_harness.build_backend_env(instance, site_packages, tmp_path)
        assert "/some/existing/path" in env["PYTHONPATH"].split(os.pathsep)

    def test_never_reuses_a_real_deployments_auth_secret(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POTIONUI_AUTH_SECRET_KEY", raising=False)
        instance = self._instance(tmp_path)
        env = e2e_harness.build_backend_env(instance, tmp_path / "site-packages", tmp_path)
        assert env["POTIONUI_AUTH_SECRET_KEY"]  # a fresh random one was generated

    def test_respects_an_already_set_auth_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POTIONUI_AUTH_SECRET_KEY", "operator-set-value")
        instance = self._instance(tmp_path)
        env = e2e_harness.build_backend_env(instance, tmp_path / "site-packages", tmp_path)
        assert env["POTIONUI_AUTH_SECRET_KEY"] == "operator-set-value"


class TestResolveBackendLaunch:
    def test_raises_a_clear_error_when_no_venv_exists(self, tmp_path):
        with pytest.raises(e2e.StageError) as exc:
            e2e_harness.resolve_backend_launch(tmp_path)
        assert exc.value.stage == "subprocess-boot"
        assert "venv" in exc.value.message

    def test_finds_site_packages_when_venv_exists(self, tmp_path):
        site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        site_packages.mkdir(parents=True)
        found = e2e_harness.find_venv_site_packages(tmp_path)
        assert found == site_packages


# ---------------------------------------------------------------------------
# Models-dir mirror (read-only depot)
# ---------------------------------------------------------------------------


class TestMirrorModelsDirReadonly:
    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(e2e.StageError) as exc:
            e2e.mirror_models_dir_readonly(tmp_path / "nope", tmp_path / "dest")
        assert exc.value.stage == "models-dir"

    def test_mirrors_nested_files_as_symlinks(self, tmp_path):
        source = tmp_path / "depot"
        (source / "checkpoints").mkdir(parents=True)
        (source / "loras" / "styleA").mkdir(parents=True)
        checkpoint = source / "checkpoints" / "model.safetensors"
        checkpoint.write_bytes(b"fake-weights")
        lora = source / "loras" / "styleA" / "x.safetensors"
        lora.write_bytes(b"fake-lora")

        dest = tmp_path / "mirror"
        e2e.mirror_models_dir_readonly(source, dest)

        mirrored_checkpoint = dest / "checkpoints" / "model.safetensors"
        mirrored_lora = dest / "loras" / "styleA" / "x.safetensors"
        assert mirrored_checkpoint.is_symlink()
        assert mirrored_lora.is_symlink()
        assert mirrored_checkpoint.read_bytes() == b"fake-weights"
        assert mirrored_lora.read_bytes() == b"fake-lora"
        # The source file itself must never be touched.
        assert checkpoint.read_bytes() == b"fake-weights"

    def test_new_files_can_be_written_into_the_mirror_without_touching_the_source(self, tmp_path):
        source = tmp_path / "depot"
        (source / "checkpoints").mkdir(parents=True)
        (source / "checkpoints" / "model.safetensors").write_bytes(b"fake-weights")
        dest = tmp_path / "mirror"
        e2e.mirror_models_dir_readonly(source, dest)

        # Simulate a fresh download landing in the mirror (a REAL new file,
        # not a symlink) - this must not require touching `source` at all.
        (dest / "loras").mkdir(parents=True, exist_ok=True)
        (dest / "loras" / "new-download.safetensors").write_bytes(b"downloaded")
        assert not (source / "loras").exists()

    def test_idempotent_on_rerun(self, tmp_path):
        source = tmp_path / "depot"
        (source / "checkpoints").mkdir(parents=True)
        (source / "checkpoints" / "model.safetensors").write_bytes(b"fake-weights")
        dest = tmp_path / "mirror"
        e2e.mirror_models_dir_readonly(source, dest)
        e2e.mirror_models_dir_readonly(source, dest)  # must not raise on existing symlinks


# ---------------------------------------------------------------------------
# Ephemeral recipe generation
# ---------------------------------------------------------------------------


def _write_fixture_recipes(marketplace_dir: Path) -> None:
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    sdxl = {
        "schema_version": 1,
        "id": "sdxl-starter",
        "version": 1,
        "name": "SDXL Starter",
        "engine": "native",
        "plugins": [{"id": "downloader", "reason": "fetches things"}],
        "backend": {"engine": "native"},
        "artifacts": [
            {
                "id": "sdxl-checkpoint",
                "kind": "checkpoint",
                "model_type": "checkpoint",
                "filename": "cyberrealisticPony_v125.safetensors",
                "display_name": "SDXL checkpoint",
                "required": True,
                "size_bytes": 123,
                "provider_hint": {"source": "civitai"},
            }
        ],
        "presets": [{"preset_id": "p1", "path_hint": "native/SDXL"}],
        "smoke": {"preset_id": "p1", "mode": "txt2img"},
        "steps": [
            {"key": "plugins.ensure", "kind": "plugins.ensure", "title": "t", "params": {"plugin_ids": ["downloader"]}},
            {"key": "backend.ensure", "kind": "backend.ensure", "title": "t", "params": {"engine": "native"}},
            {"key": "artifacts.plan", "kind": "artifacts.plan", "title": "t", "params": {"artifact_ids": ["sdxl-checkpoint"]}},
            {"key": "artifacts.fetch", "kind": "artifacts.fetch", "title": "t", "params": {"artifact_ids": ["sdxl-checkpoint"]}},
            {"key": "models.index", "kind": "models.index", "title": "t", "params": {"engine": "native"}},
            {"key": "preset.ensure", "kind": "preset.ensure", "title": "t", "params": {"preset_id": "p1"}},
            {"key": "pipeline.render", "kind": "pipeline.render", "title": "t", "params": {"preset_id": "p1", "mode": "txt2img"}},
            {"key": "generation.smoke", "kind": "generation.smoke", "title": "t", "params": {"preset_id": "p1", "mode": "txt2img"}},
            {"key": "workspace.activate", "kind": "workspace.activate", "title": "t", "params": {}},
        ],
    }
    other = {
        "schema_version": 1,
        "id": "comfyui-detect",
        "version": 1,
        "name": "ComfyUI Detect",
        "engine": "comfyui",
        "presets": [{"preset_id": "p2"}],
        "steps": [{"key": "backend.detect", "kind": "backend.detect", "title": "t", "params": {"engine": "comfyui"}}],
    }
    (marketplace_dir / "sdxl-starter.yml").write_text(yaml.safe_dump(sdxl, sort_keys=False))
    (marketplace_dir / "comfyui-detect.yml").write_text(yaml.safe_dump(other, sort_keys=False))


class TestPrepareEphemeralRecipes:
    def test_passthrough_recipe_is_copied_verbatim(self, tmp_path, monkeypatch):
        source_recipes = tmp_path / "content" / "recipes" / "marketplace"
        _write_fixture_recipes(source_recipes)
        monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)

        dest = tmp_path / "ephemeral-recipes"
        e2e.prepare_ephemeral_recipes(dest, no_gpu=False, dummy_artifact_url=None)

        other = yaml.safe_load((dest / "marketplace" / "comfyui-detect.yml").read_text())
        assert other["id"] == "comfyui-detect"
        assert len(other["steps"]) == 1

    def test_no_gpu_drops_smoke_and_activate_steps(self, tmp_path, monkeypatch):
        source_recipes = tmp_path / "content" / "recipes" / "marketplace"
        _write_fixture_recipes(source_recipes)
        monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)

        dest = tmp_path / "ephemeral-recipes"
        e2e.prepare_ephemeral_recipes(dest, no_gpu=True, dummy_artifact_url=None)

        recipe = yaml.safe_load((dest / "marketplace" / "sdxl-starter.yml").read_text())
        kinds = [s["kind"] for s in recipe["steps"]]
        assert "generation.smoke" not in kinds
        assert "workspace.activate" not in kinds
        assert "pipeline.render" in kinds  # everything else stays

    def test_default_keeps_the_full_step_list(self, tmp_path, monkeypatch):
        source_recipes = tmp_path / "content" / "recipes" / "marketplace"
        _write_fixture_recipes(source_recipes)
        monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)

        dest = tmp_path / "ephemeral-recipes"
        e2e.prepare_ephemeral_recipes(dest, no_gpu=False, dummy_artifact_url=None)

        recipe = yaml.safe_load((dest / "marketplace" / "sdxl-starter.yml").read_text())
        kinds = [s["kind"] for s in recipe["steps"]]
        assert "generation.smoke" in kinds
        assert "workspace.activate" in kinds

    def test_fresh_download_adds_a_dummy_artifact_without_touching_the_real_one(self, tmp_path, monkeypatch):
        source_recipes = tmp_path / "content" / "recipes" / "marketplace"
        _write_fixture_recipes(source_recipes)
        monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)

        dest = tmp_path / "ephemeral-recipes"
        dummy_url = "http://127.0.0.1:12345/dummy.safetensors"
        e2e.prepare_ephemeral_recipes(dest, no_gpu=False, dummy_artifact_url=dummy_url)

        recipe = yaml.safe_load((dest / "marketplace" / "sdxl-starter.yml").read_text())
        artifact_ids = {a["id"] for a in recipe["artifacts"]}
        assert "sdxl-checkpoint" in artifact_ids  # untouched
        assert "e2e-dummy-lora" in artifact_ids

        dummy = next(a for a in recipe["artifacts"] if a["id"] == "e2e-dummy-lora")
        assert dummy["provider_hint"]["download_url"] == dummy_url
        assert dummy["model_type"] == "lora"  # never collides with the checkpoint's identity

        for step in recipe["steps"]:
            if step["kind"] in ("artifacts.plan", "artifacts.fetch"):
                assert "e2e-dummy-lora" in step["params"]["artifact_ids"]
                assert "sdxl-checkpoint" in step["params"]["artifact_ids"]

    def test_no_recipes_directory_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)  # tmp_path/recipes doesn't exist
        with pytest.raises(e2e.StageError) as exc:
            e2e.prepare_ephemeral_recipes(tmp_path / "dest", no_gpu=False, dummy_artifact_url=None)
        assert exc.value.stage == "recipes"


class TestPrimaryArtifact:
    def test_returns_first_artifact(self):
        recipe = {"artifacts": [{"id": "a"}, {"id": "b"}]}
        assert e2e.primary_artifact(recipe)["id"] == "a"

    def test_none_when_no_artifacts(self):
        assert e2e.primary_artifact({"artifacts": []}) is None
        assert e2e.primary_artifact({}) is None


# ---------------------------------------------------------------------------
# Dummy artifact server
# ---------------------------------------------------------------------------


class TestDummyArtifactServer:
    def test_serves_the_generated_dummy_file(self, tmp_path):
        import urllib.request

        server = e2e.DummyArtifactServer(tmp_path / "serve")
        try:
            with urllib.request.urlopen(server.url, timeout=5) as resp:
                body = resp.read()
            assert len(body) == e2e.DummyArtifactServer.SIZE_BYTES
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Stage sequencing against a faked HTTP client
# ---------------------------------------------------------------------------


class TestStageClaimOwnerAndLogin:
    def test_claim_owner_sets_token_and_requires_admin(self):
        client = FakeSetupClient()
        client.queue_post(
            "/api/auth/register",
            FakeResponse(200, {"success": True, "data": {"access_token": "tok1", "user": {"account_type": "ADMIN"}}}),
        )
        journey = make_journey(client)
        e2e.stage_claim_owner(journey)
        assert client.token == "tok1"

    def test_claim_owner_raises_when_not_admin(self):
        client = FakeSetupClient()
        client.queue_post(
            "/api/auth/register",
            FakeResponse(200, {"success": True, "data": {"access_token": "tok1", "user": {"account_type": "USER"}}}),
        )
        journey = make_journey(client)
        with pytest.raises(e2e.StageError) as exc:
            e2e.stage_claim_owner(journey)
        assert exc.value.stage == "claim-owner"

    def test_claim_owner_raises_on_http_error(self):
        client = FakeSetupClient()
        client.queue_post("/api/auth/register", FakeResponse(422, {"detail": "bad email"}))
        journey = make_journey(client)
        with pytest.raises(e2e.StageError) as exc:
            e2e.stage_claim_owner(journey)
        assert "claim-owner" == exc.value.stage
        assert "bad email" in exc.value.message

    def test_login_sets_a_fresh_token(self):
        client = FakeSetupClient()
        client.queue_post("/api/auth/login", FakeResponse(200, {"access_token": "tok2", "token_type": "bearer"}))
        journey = make_journey(client)
        e2e.stage_login(journey)
        assert client.token == "tok2"


class TestStageListRecipes:
    def test_raises_when_recipe_not_present(self):
        client = FakeSetupClient()
        client.queue_get("/api/setup/recipes", FakeResponse(200, {"recipes": [{"id": "comfyui-detect"}]}))
        journey = make_journey(client, recipe_id="sdxl-starter")
        with pytest.raises(e2e.StageError):
            e2e.stage_list_recipes(journey)

    def test_passes_when_recipe_present(self):
        client = FakeSetupClient()
        client.queue_get("/api/setup/recipes", FakeResponse(200, {"recipes": [{"id": "sdxl-starter"}]}))
        journey = make_journey(client, recipe_id="sdxl-starter")
        recipes = e2e.stage_list_recipes(journey)
        assert recipes[0]["id"] == "sdxl-starter"


def _run_view(status: str, steps=None, current_step=None, run_id="run-1") -> Dict[str, Any]:
    return {"id": run_id, "status": status, "current_step": current_step, "steps": steps or []}


class TestDriveToCompletion:
    def test_single_consent_grant_reaches_completion(self, monkeypatch):
        monkeypatch.setattr(e2e, "POLL_INTERVAL_SECONDS", 0.01)
        client = FakeSetupClient()
        parked = _run_view(
            "awaiting_consent",
            current_step="artifacts.plan",
            steps=[
                {
                    "step_key": "artifacts.plan",
                    "kind": "artifacts.plan",
                    "status": "awaiting_consent",
                    "attempts": [
                        {
                            "step_key": "artifacts.plan",
                            "status": "awaiting_consent",
                            "safe_output": {
                                "consent_request": {
                                    "artifacts": [{"id": "e2e-dummy-lora", "display_name": "dummy"}],
                                    "total_bytes": 4096,
                                }
                            },
                        }
                    ],
                }
            ],
        )
        completed = _run_view("completed", current_step="workspace.activate")

        client.queue_get(f"/api/setup/runs/{parked['id']}", FakeResponse(200, parked))
        client.queue_post(f"/api/setup/runs/{parked['id']}/actions/grant_consent", FakeResponse(200, completed))

        journey = make_journey(client)
        journey.run_view = parked
        result = e2e.stage_drive_to_completion(journey)
        assert result["status"] == "completed"

    def test_stuck_on_same_step_raises(self, monkeypatch):
        monkeypatch.setattr(e2e, "POLL_INTERVAL_SECONDS", 0.01)
        client = FakeSetupClient()
        parked = _run_view(
            "awaiting_consent",
            current_step="artifacts.plan",
            steps=[
                {
                    "step_key": "artifacts.plan",
                    "kind": "artifacts.plan",
                    "status": "awaiting_consent",
                    "attempts": [
                        {
                            "step_key": "artifacts.plan",
                            "status": "awaiting_consent",
                            "safe_output": {"consent_request": {"artifacts": [], "total_bytes": 0}},
                        }
                    ],
                }
            ],
        )
        client.queue_get(f"/api/setup/runs/{parked['id']}", FakeResponse(200, parked))
        # Re-parks on the SAME step forever - must be detected as stuck, not looped forever.
        client.queue_post(f"/api/setup/runs/{parked['id']}/actions/grant_consent", FakeResponse(200, parked))

        journey = make_journey(client)
        journey.run_view = parked
        with pytest.raises(e2e.StageError) as exc:
            e2e.stage_drive_to_completion(journey)
        assert exc.value.stage == "consent-loop"

    def test_missing_consent_request_raises(self, monkeypatch):
        monkeypatch.setattr(e2e, "POLL_INTERVAL_SECONDS", 0.01)
        client = FakeSetupClient()
        parked = _run_view(
            "awaiting_consent",
            current_step="artifacts.plan",
            steps=[{"step_key": "artifacts.plan", "kind": "artifacts.plan", "status": "awaiting_consent", "attempts": []}],
        )
        journey = make_journey(client)
        journey.run_view = parked
        with pytest.raises(e2e.StageError) as exc:
            e2e.stage_drive_to_completion(journey)
        assert "consent_request" in exc.value.message


class TestAssertNoUnexpectedDownload:
    def test_passes_when_checkpoint_never_in_a_consent_request(self):
        client = FakeSetupClient()
        journey = make_journey(client)
        journey.run_view = _run_view(
            "completed",
            steps=[
                {
                    "step_key": "artifacts.plan",
                    "kind": "artifacts.plan",
                    "status": "succeeded",
                    "attempts": [{"safe_output": {"already_present": [{"id": "sdxl-checkpoint"}]}}],
                }
            ],
        )
        e2e.stage_assert_no_unexpected_download(journey, "cyberrealisticPony_v125.safetensors")  # must not raise

    def test_raises_when_checkpoint_was_asked_for(self):
        client = FakeSetupClient()
        journey = make_journey(client)
        journey.run_view = _run_view(
            "awaiting_consent",
            steps=[
                {
                    "step_key": "artifacts.plan",
                    "kind": "artifacts.plan",
                    "status": "awaiting_consent",
                    "attempts": [
                        {
                            "safe_output": {
                                "consent_request": {
                                    "artifacts": [{"id": "sdxl-checkpoint", "display_name": "cyberrealisticPony_v125.safetensors"}]
                                }
                            }
                        }
                    ],
                }
            ],
        )
        with pytest.raises(e2e.StageError) as exc:
            e2e.stage_assert_no_unexpected_download(journey, "cyberrealisticPony_v125.safetensors")
        assert exc.value.stage == "assert-no-download"

    def test_noop_when_recipe_has_no_plan_step(self):
        client = FakeSetupClient()
        journey = make_journey(client)
        journey.run_view = _run_view("completed", steps=[])
        e2e.stage_assert_no_unexpected_download(journey, "whatever.safetensors")  # must not raise


class TestAssertSmokeOutput:
    def test_returns_absolute_path_when_file_exists(self, tmp_path):
        storage_dir = tmp_path / "storage"
        (storage_dir / "generations" / "2026-01-01" / "gen1").mkdir(parents=True)
        output_file = storage_dir / "generations" / "2026-01-01" / "gen1" / "0.png"
        output_file.write_bytes(b"fake-png")

        client = FakeSetupClient()
        journey = make_journey(client, storage_dir=storage_dir)
        journey.run_view = _run_view(
            "completed",
            steps=[
                {
                    "step_key": "generation.smoke",
                    "kind": "generation.smoke",
                    "status": "succeeded",
                    "attempts": [
                        {
                            "safe_output": {
                                "output_count": 1,
                                "file_path": "generations/2026-01-01/gen1/0.png",
                            }
                        }
                    ],
                }
            ],
        )
        path = e2e.stage_assert_smoke_output(journey)
        assert Path(path) == output_file.resolve()

    def test_raises_when_file_missing_on_disk(self, tmp_path):
        storage_dir = tmp_path / "storage"
        client = FakeSetupClient()
        journey = make_journey(client, storage_dir=storage_dir)
        journey.run_view = _run_view(
            "completed",
            steps=[
                {
                    "step_key": "generation.smoke",
                    "kind": "generation.smoke",
                    "status": "succeeded",
                    "attempts": [{"safe_output": {"output_count": 1, "file_path": "nowhere/0.png"}}],
                }
            ],
        )
        with pytest.raises(e2e.StageError) as exc:
            e2e.stage_assert_smoke_output(journey)
        assert exc.value.stage == "assert-smoke-output"

    def test_raises_when_step_did_not_succeed(self):
        client = FakeSetupClient()
        journey = make_journey(client)
        journey.run_view = _run_view(
            "failed",
            steps=[{"step_key": "generation.smoke", "kind": "generation.smoke", "status": "failed", "attempts": []}],
        )
        with pytest.raises(e2e.StageError):
            e2e.stage_assert_smoke_output(journey)


# ---------------------------------------------------------------------------
# Teardown-on-failure
# ---------------------------------------------------------------------------


class TestTeardownBackend:
    def _instance(self, tmp_path) -> e2e.EphemeralInstance:
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        (instance_dir / "marker.txt").write_text("hi")
        return e2e.EphemeralInstance(
            instance_dir=instance_dir,
            storage_dir=instance_dir / "storage",
            db_path=instance_dir / "storage" / "db.sqlite",
            models_dir=instance_dir / "models",
            recipes_dir=instance_dir / "recipes",
            port=18055,
            process=None,  # never spawned - e.g. a models-dir stage failed first
        )

    def test_removes_the_instance_dir_when_not_keeping(self, tmp_path):
        instance = self._instance(tmp_path)
        e2e.teardown_backend(instance, keep=False)
        assert not instance.instance_dir.exists()

    def test_leaves_the_instance_dir_when_keeping(self, tmp_path):
        instance = self._instance(tmp_path)
        e2e.teardown_backend(instance, keep=True)
        assert instance.instance_dir.exists()

    def test_safe_to_call_with_no_process_ever_spawned(self, tmp_path):
        instance = self._instance(tmp_path)
        assert instance.process is None
        e2e.teardown_backend(instance, keep=False)  # must not raise


class TestMainTearsDownOnEarlyFailure:
    """A failure before the backend subprocess is even spawned (e.g. a bad
    --models-dir) must still remove the temp instance dir and exit non-zero -
    exercised through `main()` itself with the network/subprocess-touching
    stages monkeypatched out, since nothing past `mirror_models_dir_readonly`
    should ever run in this scenario."""

    def test_bad_models_dir_tears_down_and_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        created_dirs: List[Path] = []
        real_mkdtemp = e2e.tempfile.mkdtemp

        def _tracking_mkdtemp(*args, **kwargs):
            d = real_mkdtemp(*args, **kwargs)
            created_dirs.append(Path(d))
            return d

        monkeypatch.setattr(e2e.tempfile, "mkdtemp", _tracking_mkdtemp)

        def _boom(*args, **kwargs):
            raise AssertionError("spawn_backend must never be called when models-dir prep failed")

        monkeypatch.setattr(e2e, "spawn_backend", _boom)

        missing_models_dir = tmp_path / "does-not-exist"
        exit_code = e2e.main(["--models-dir", str(missing_models_dir), "--port", "19321"])

        assert exit_code == 1
        assert len(created_dirs) == 1
        assert not created_dirs[0].exists()  # torn down even though the failure was early
        captured = capsys.readouterr()
        assert "models-dir" in captured.err

    def test_keep_flag_leaves_the_instance_dir_on_failure(self, tmp_path, monkeypatch, capsys):
        created_dirs: List[Path] = []
        real_mkdtemp = e2e.tempfile.mkdtemp

        def _tracking_mkdtemp(*args, **kwargs):
            d = real_mkdtemp(*args, **kwargs)
            created_dirs.append(Path(d))
            return d

        monkeypatch.setattr(e2e.tempfile, "mkdtemp", _tracking_mkdtemp)
        monkeypatch.setattr(e2e, "spawn_backend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")))

        missing_models_dir = tmp_path / "does-not-exist"
        exit_code = e2e.main(["--models-dir", str(missing_models_dir), "--port", "19322", "--keep"])

        assert exit_code == 1
        assert len(created_dirs) == 1
        assert created_dirs[0].exists()  # --keep wins even on failure
        shutil.rmtree(created_dirs[0], ignore_errors=True)  # test cleanup


class TestParseArgs:
    def test_requires_models_dir_or_env_var(self, monkeypatch, capsys):
        monkeypatch.delenv("POTIONUI_E2E_MODELS_DIR", raising=False)
        exit_code = e2e.main(["--port", "19323"])
        assert exit_code == 2

    def test_env_var_fills_in_models_dir(self, monkeypatch):
        monkeypatch.setenv("POTIONUI_E2E_MODELS_DIR", "/some/depot")
        args = e2e.parse_args([])
        assert args.models_dir == "/some/depot"

    def test_defaults(self):
        args = e2e.parse_args(["--models-dir", "/x"])
        assert args.port == e2e.DEFAULT_PORT
        assert args.recipe == e2e.DEFAULT_RECIPE
        assert args.no_gpu is False
        assert args.keep is False
        assert args.fresh_download is False
