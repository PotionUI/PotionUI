from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.recipes.routes import build_router
from src.features.recipes.schema import (
    Recipe,
    RecipeArtifact,
    RecipeArtifactVariant,
    RecipeChecksum,
    RecipePresetRef,
    RecipeVariantRule,
)
from src.features.recipes.slot_variants import RecipeSlotVariants, VariantDownloadError
from src.platform.runtime.gpu_profile import build_gpu_profile
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

GB = 1024 ** 3


def _variant(vid, precision, size_gb, rules=(), default=False, sha=None):
    return RecipeArtifactVariant(
        id=vid,
        label=vid,
        precision=precision,
        filename=f"{vid}.safetensors",
        size_bytes=int(size_gb * GB),
        checksum=RecipeChecksum("sha256", sha) if sha else None,
        provider_hint={"source": "huggingface", "model_id": "org/repo", "download_url": f"https://example.test/{vid}"},
        uploader="org",
        recommended_for=tuple(rules),
        default=default,
    )


def _dit():
    return RecipeArtifact(
        id="dit",
        kind="diffusion_model",
        model_type="diffusion_model",
        filename="dit_fp8.safetensors",
        display_name="DiT",
        variants=(
            _variant("dit_bf16", "bf16", 24.5, [RecipeVariantRule(min_vram_gb=40)]),
            _variant(
                "dit_fp8",
                "fp8",
                12.2,
                [RecipeVariantRule(min_vram_gb=20, generations=("ada", "hopper", "blackwell"))],
                default=True,
                sha="abc123",
            ),
            _variant("dit_int8", "int8", 12.6, [RecipeVariantRule(min_vram_gb=20)]),
            _variant("dit_nvfp4", "nvfp4", 7.1, [RecipeVariantRule(generations=("blackwell",))]),
        ),
    )


def _vae():
    return RecipeArtifact(id="vae", kind="vae", model_type="vae", filename="vae.safetensors")


def _recipe():
    return Recipe(
        id="demo",
        schema_version=1,
        version=1,
        name="Demo Recipe",
        engine="native",
        artifacts=[_dit(), _vae()],
        presets=[RecipePresetRef(preset_id="PRESET1")],
    )


class _Catalog:
    def __init__(self, recipes):
        self.recipes = {r.id: r for r in recipes}

    def list_recipes(self):
        return list(self.recipes.values())

    def get_recipe(self, recipe_id, version=None):
        return self.recipes.get(recipe_id)

    def recipes_for_preset(self, preset_id):
        return [r for r in self.recipes.values() if any(p.preset_id == preset_id for p in r.presets)]


class _Repo:
    def __init__(self, models=()):
        self.models = list(models)

    def get_by_identity(self, model_type, filename):
        for m in self.models:
            if m.model_type == model_type and m.filename == filename:
                return m
        return None

    def get_by_sha256(self, sha):
        for m in self.models:
            if m.sha256 == sha:
                return m
        return None

    def get_by_id(self, model_id):
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    def update(self, model):
        return True


def _model(model_id, filename, model_type="diffusion_model", sha256=None):
    return SimpleNamespace(
        id=model_id,
        filename=filename,
        model_type=model_type,
        sha256=sha256,
        file_path=f"/models/{filename}",
        is_available=True,
    )


def _ada24():
    return build_gpu_profile((8, 9), 24, "ada-24")


def _service(models=(), queue=None, gpu=_ada24):
    return RecipeSlotVariants(_Catalog([_recipe()]), _Repo(models), download_queue=queue, gpu_profile_provider=gpu)


def test_for_model_lists_siblings_with_installed_flags_and_suggestion():
    service = _service([_model("m1", "dit_int8.safetensors")])
    result = service.for_model_id("m1")
    slot = result["slot"]
    assert slot["recipe_id"] == "demo"
    assert slot["current_variant_id"] == "dit_int8"
    assert [v["id"] for v in slot["variants"]] == ["dit_bf16", "dit_fp8", "dit_int8", "dit_nvfp4"]
    assert {v["id"]: v["installed"] for v in slot["variants"]} == {
        "dit_bf16": False,
        "dit_fp8": False,
        "dit_int8": True,
        "dit_nvfp4": False,
    }
    assert slot["suggested_variant_id"] == "dit_fp8"
    assert slot["suggested_reason"] == "Fits your 24 GB"
    assert slot["variants"][0]["uploader"] == "org"
    assert result["gpu"]["generation"] == "ada"


def test_for_model_matches_by_sha256_when_renamed():
    service = _service([_model("m1", "renamed.safetensors", sha256="ABC123")])
    assert service.for_model_id("m1")["slot"]["current_variant_id"] == "dit_fp8"


def test_for_model_without_recipe_slot_is_empty():
    service = _service([_model("m1", "other.safetensors")])
    assert service.for_model_id("m1")["slot"] is None
    assert service.for_model_id("missing") is None


def test_single_file_artifacts_are_never_offered_as_variants():
    service = _service([_model("v", "vae.safetensors", model_type="vae")])
    assert service.for_model_id("v")["slot"] is None
    assert [s["id"] for s in service.for_preset("PRESET1")["slots"]] == ["dit"]


def test_for_preset_filters_by_model_type_and_preset():
    service = _service()
    assert [s["id"] for s in service.for_preset("PRESET1", "diffusion_model")["slots"]] == ["dit"]
    assert service.for_preset("PRESET1", "text_encoder")["slots"] == []
    assert service.for_preset("OTHER")["slots"] == []


def test_queue_download_resolves_variant_and_queues_it():
    queue = SimpleNamespace(queue_model_download=AsyncMock(return_value=SimpleNamespace(id="dl-1")))
    result = _service(queue=queue).queue_download("demo", "dit", "dit_fp8", "admin-1")
    assert result == {"download_id": "dl-1", "filename": "dit_fp8.safetensors"}
    queue.queue_model_download.assert_awaited_once_with(
        url="https://example.test/dit_fp8",
        model_type="diffusion_model",
        filename="dit_fp8.safetensors",
        checksum_sha256="abc123",
        provider_id="huggingface",
        created_by="admin-1",
    )


def test_queue_download_rejects_unknown_and_installed_variants():
    queue = SimpleNamespace(queue_model_download=AsyncMock())
    service = _service([_model("m1", "dit_int8.safetensors")], queue=queue)
    with pytest.raises(VariantDownloadError) as unknown:
        service.queue_download("demo", "dit", "nope", None)
    assert unknown.value.code == "variant_not_found"
    with pytest.raises(VariantDownloadError) as installed:
        service.queue_download("demo", "dit", "dit_int8", None)
    assert installed.value.code == "already_installed"
    queue.queue_model_download.assert_not_awaited()


def _user(account_type):
    return User(username="u", email="u@example.com", password_hash="x", account_type=account_type, id="user-1")


def _client(service, account_type=AccountType.ADMIN):
    container = SimpleNamespace(recipe_slot_variants=service, recipe_runner=None, recipe_catalog=None, recipe_preset_links=None)
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: _user(account_type)
    return TestClient(app)


def test_variant_routes_are_admin_only():
    queue = SimpleNamespace(queue_model_download=AsyncMock())
    client = _client(_service(queue=queue), AccountType.USER)
    assert client.get("/api/recipes/variants/preset/PRESET1").status_code == 403
    assert client.get("/api/recipes/variants/model/m1").status_code == 403
    response = client.post(
        "/api/recipes/variants/download",
        json={"recipe_id": "demo", "artifact_id": "dit", "variant_id": "dit_fp8"},
    )
    assert response.status_code == 403
    queue.queue_model_download.assert_not_awaited()


def test_download_route_queues_the_chosen_variant():
    queue = SimpleNamespace(queue_model_download=AsyncMock(return_value=SimpleNamespace(id="dl-9")))
    client = _client(_service(queue=queue))
    response = client.post(
        "/api/recipes/variants/download",
        json={"recipe_id": "demo", "artifact_id": "dit", "variant_id": "dit_nvfp4"},
    )
    assert response.status_code == 202
    assert response.json() == {"download_id": "dl-9", "filename": "dit_nvfp4.safetensors"}
    assert queue.queue_model_download.await_args.kwargs["created_by"] == "user-1"


def test_download_route_request_shape_and_errors():
    client = _client(_service(queue=SimpleNamespace(queue_model_download=AsyncMock())))
    assert client.post("/api/recipes/variants/download", json={"recipe_id": "demo"}).status_code == 422
    missing = client.post(
        "/api/recipes/variants/download",
        json={"recipe_id": "demo", "artifact_id": "dit", "variant_id": "nope"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "variant_not_found"


def test_model_and_preset_routes_return_slots():
    client = _client(_service([_model("m1", "dit_bf16.safetensors")]))
    by_model = client.get("/api/recipes/variants/model/m1").json()
    assert by_model["slot"]["current_variant_id"] == "dit_bf16"
    assert client.get("/api/recipes/variants/model/unknown").status_code == 404
    by_preset = client.get("/api/recipes/variants/preset/PRESET1", params={"model_type": "diffusion_model"}).json()
    assert by_preset["slots"][0]["suggested_variant_id"] == "dit_fp8"
