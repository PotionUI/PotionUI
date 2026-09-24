"""`artifacts.plan` against a fake `ModelRepository` surface: present vs
missing by `(model_type, filename)` identity, and the awaiting-consent shape."""

from src.features.recipes.executors.artifacts_plan import ArtifactsPlanExecutor
from src.features.recipes.executors.base import StepContext
from src.features.recipes.schema import Recipe, RecipeArtifact, RecipeStep
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.platform.runtime.gpu_profile import GpuProfile


class FakeModelRepository:
    def __init__(self, present=(), by_sha256=None):
        self.present = set(present)  # set of (model_type, filename)
        self.by_sha256 = dict(by_sha256 or {})  # sha256 -> row (SimpleNamespace)
        self.updated = []

    def get_by_identity(self, model_type, filename, include_providers=True):
        return object() if (model_type, filename) in self.present else None

    def get_by_sha256(self, sha256, include_providers=True):
        return self.by_sha256.get(sha256)

    def update(self, model):
        self.updated.append(model)
        return True


class FakeProviderMetadata:
    def __init__(self, name, website):
        self.name = name
        self.website = website


class FakeProviderRegistry:
    """A minimal stand-in for `ProviderRegistry` exposing only what
    `credential_prompt_for_provider` reads: metadata, settings schema, and
    current settings."""

    def __init__(self, providers):
        # providers: {provider_id: {"name", "website", "schema", "settings"}}
        self._providers = providers

    def get_provider_metadata(self, provider_id):
        info = self._providers.get(provider_id)
        return FakeProviderMetadata(info["name"], info["website"]) if info else None

    def get_provider_settings_schema(self, provider_id):
        info = self._providers.get(provider_id)
        return info["schema"] if info else None

    def get_provider_current_settings(self, provider_id):
        info = self._providers.get(provider_id)
        return info["settings"] if info else {}


def _recipe(artifacts):
    return Recipe(
        id="x", schema_version=1, version=1, name="X", engine="native", artifacts=artifacts
    )


def _context(recipe, artifact_ids):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    step = RecipeStep(key="artifacts.plan", kind="artifacts.plan", title="Plan", params={"artifact_ids": artifact_ids})
    return StepContext(run=run, recipe=recipe, step=step)


def _artifact(
    aid,
    filename="model.safetensors",
    size_bytes=100,
    provider_hint=None,
    gated=False,
    license_url=None,
    checksum=None,
):
    return RecipeArtifact(
        id=aid,
        kind="checkpoint",
        model_type="checkpoint",
        filename=filename,
        display_name=aid,
        size_bytes=size_bytes,
        provider_hint=provider_hint or {},
        gated=gated,
        license_url=license_url,
        checksum=checksum,
    )


def test_everything_present_succeeds_with_nothing_to_download():
    artifact = _artifact("ckpt")
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present={("checkpoint", "model.safetensors")})
    executor = ArtifactsPlanExecutor(repo)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is True
    assert result.awaiting_consent is False
    assert "already_present" in result.safe_output


def test_missing_artifact_parks_awaiting_consent_with_request_payload():
    artifact = _artifact("ckpt", size_bytes=12345)
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    executor = ArtifactsPlanExecutor(repo, gpu_profile_provider=GpuProfile)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.awaiting_consent is True
    assert result.consent_request["total_bytes"] == 12345
    assert result.consent_request["artifacts"] == [
        {
            "id": "ckpt",
            "variant_id": "ckpt",
            "display_name": "ckpt",
            "size_bytes": 12345,
            "kind": "checkpoint",
            "gated": False,
            "license_url": None,
        }
    ]


def test_unknown_artifact_id_fails_misconfigured():
    recipe = _recipe([])
    executor = ArtifactsPlanExecutor(FakeModelRepository())

    result = executor.execute(_context(recipe, ["missing"]))

    assert result.success is False
    assert result.error_code == "ARTIFACTS_PLAN_MISCONFIGURED"


def test_no_artifact_ids_fails_misconfigured():
    recipe = _recipe([])
    executor = ArtifactsPlanExecutor(FakeModelRepository())

    result = executor.execute(_context(recipe, []))

    assert result.success is False
    assert result.error_code == "ARTIFACTS_PLAN_MISCONFIGURED"


def test_consent_request_offers_a_credential_prompt_for_an_unconfigured_provider():
    """A missing artifact whose `provider_hint.source` resolves to a provider
    that takes a credential (its settings schema has a password-format
    field) and doesn't have one configured surfaces in `consent_request.
    providers`, so the consent gate can collect it inline."""
    artifact = _artifact("ckpt", provider_hint={"source": "civitai"})
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    registry = FakeProviderRegistry(
        {
            "civitai": {
                "name": "CivitAI",
                "website": "https://civitai.com",
                "schema": {"properties": {"api_key": {"format": "password"}}},
                "settings": {},
            }
        }
    )
    executor = ArtifactsPlanExecutor(repo)
    executor._get_provider_registry = lambda: registry

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.awaiting_consent is True
    assert result.consent_request["providers"] == [
        {
            "id": "civitai",
            "name": "CivitAI",
            "website": "https://civitai.com",
            "field_name": "api_key",
            "configured": False,
        }
    ]


def test_consent_request_omits_providers_when_already_configured():
    artifact = _artifact("ckpt", provider_hint={"source": "civitai"})
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    registry = FakeProviderRegistry(
        {
            "civitai": {
                "name": "CivitAI",
                "website": "https://civitai.com",
                "schema": {"properties": {"api_key": {"format": "password"}}},
                "settings": {"api_key": "already-set"},
            }
        }
    )
    executor = ArtifactsPlanExecutor(repo)
    executor._get_provider_registry = lambda: registry

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert "providers" not in result.consent_request


def test_consent_request_omits_providers_when_artifact_has_no_provider_hint():
    artifact = _artifact("ckpt")
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    executor = ArtifactsPlanExecutor(repo)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert "providers" not in result.consent_request


def _huggingface_registry(configured=False):
    return FakeProviderRegistry(
        {
            "huggingface": {
                "name": "Hugging Face",
                "website": "https://huggingface.co",
                "schema": {"properties": {"api_key": {"format": "password"}}},
                "settings": {"api_key": "already-set"} if configured else {},
            }
        }
    )


def test_gated_artifact_without_credentials_carries_a_warning():
    artifact = _artifact(
        "ckpt",
        provider_hint={"source": "huggingface"},
        gated=True,
        license_url="https://huggingface.co/some/model",
    )
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    executor = ArtifactsPlanExecutor(repo)
    executor._get_provider_registry = lambda: _huggingface_registry(configured=False)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.awaiting_consent is True
    warnings = result.consent_request["warnings"]
    assert len(warnings) == 1
    assert "ckpt" in warnings[0]
    assert "Hugging Face" in warnings[0]
    assert "https://huggingface.co/some/model" in warnings[0]
    assert "Admin -> Plugins" in warnings[0]


def test_gated_artifact_with_credentials_configured_has_no_warning():
    artifact = _artifact("ckpt", provider_hint={"source": "huggingface"}, gated=True)
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    executor = ArtifactsPlanExecutor(repo)
    executor._get_provider_registry = lambda: _huggingface_registry(configured=True)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert "warnings" not in result.consent_request


def test_non_gated_artifact_without_credentials_has_no_warning():
    artifact = _artifact("ckpt", provider_hint={"source": "huggingface"}, gated=False)
    recipe = _recipe([artifact])
    repo = FakeModelRepository(present=set())
    executor = ArtifactsPlanExecutor(repo)
    executor._get_provider_registry = lambda: _huggingface_registry(configured=False)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert "warnings" not in result.consent_request


def test_hash_matched_misfiled_row_is_adopted_instead_of_redownloaded():
    """The same bytes already indexed under another folder/type (a download
    that landed in models/models/<type>/ and was typed 'unknown') count as
    present: the row is retyped to the artifact's model_type and no consent
    is requested."""
    from types import SimpleNamespace
    from src.features.recipes.schema import RecipeChecksum

    artifact = _artifact("ckpt", checksum=RecipeChecksum(algorithm="sha256", value="abc123"))
    row = SimpleNamespace(
        model_type="unknown", filename=artifact.filename,
        file_path="models/models/checkpoints/" + artifact.filename, is_available=True,
    )
    repo = FakeModelRepository(by_sha256={"abc123": row})
    recipe = _recipe([artifact])
    executor = ArtifactsPlanExecutor(repo)

    result = executor.execute(_context(recipe, [artifact.id]))

    assert result.success is True
    assert result.safe_output["already_present"][0]["id"] == artifact.id
    assert row.model_type == artifact.model_type
    assert repo.updated == [row]


def _slot_artifact():
    from src.features.recipes.schema import RecipeArtifactVariant, RecipeVariantRule

    def variant(vid, precision, size, rules=(), default=False, gated=False):
        return RecipeArtifactVariant(
            id=vid,
            label=vid.upper(),
            precision=precision,
            filename=f"{vid}.safetensors",
            size_bytes=size,
            provider_hint={"source": "huggingface", "model_id": "org/repo", "version_id": f"main@{vid}.safetensors"},
            gated=gated,
            license_url="https://huggingface.co/org/repo" if gated else None,
            uploader="org",
            source_url="https://huggingface.co/org/repo",
            recommended_for=tuple(rules),
            default=default,
        )

    return RecipeArtifact(
        id="dit",
        kind="diffusion_model",
        model_type="diffusion_model",
        filename="fp8.safetensors",
        display_name="DiT",
        size_bytes=200,
        variants=(
            variant("bf16", "bf16", 400, [RecipeVariantRule(min_vram_gb=40)]),
            variant("fp8", "fp8", 200, [RecipeVariantRule(min_vram_gb=20, generations=("ada", "blackwell"))], default=True),
            variant("nvfp4", "nvfp4", 100, [RecipeVariantRule(generations=("blackwell",))], gated=True),
        ),
    )


def _ada24():
    from src.platform.runtime.gpu_profile import build_gpu_profile

    return build_gpu_profile((8, 9), 24, "RTX 4090")


def test_variant_slot_consent_request_carries_slots_gpu_and_recommendation():
    vae = _artifact("vae", filename="vae.safetensors", size_bytes=5)
    recipe = _recipe([_slot_artifact(), vae])
    repo = FakeModelRepository(present={("checkpoint", "vae.safetensors")})
    executor = ArtifactsPlanExecutor(repo, gpu_profile_provider=_ada24)

    result = executor.execute(_context(recipe, ["dit", "vae"]))

    assert result.awaiting_consent is True
    request = result.consent_request
    assert request["gpu"]["generation"] == "ada"
    assert request["gpu"]["vram_gb"] == 24.0
    assert [a["variant_id"] for a in request["artifacts"]] == ["fp8"]
    assert request["total_bytes"] == 200
    dit, vae_slot = request["slots"]
    assert dit["id"] == "dit"
    assert dit["recommended_variant_id"] == "fp8"
    assert dit["reason"] == "Fits your 24 GB"
    by_id = {v["id"]: v for v in dit["variants"]}
    assert set(by_id) == {"bf16", "fp8", "nvfp4"}
    assert by_id["nvfp4"]["note"] == "nvfp4 needs an RTX 50-series (Blackwell) GPU"
    assert by_id["nvfp4"]["gated"] is True
    assert by_id["nvfp4"]["license_url"] == "https://huggingface.co/org/repo"
    assert by_id["fp8"]["uploader"] == "org"
    assert by_id["fp8"]["source_url"] == "https://huggingface.co/org/repo"
    assert by_id["fp8"]["repo_id"] == "org/repo"
    assert by_id["fp8"]["is_recipe_default"] is True
    assert by_id["fp8"]["size_bytes"] == 200
    assert not any(v["installed"] for v in dit["variants"])
    assert vae_slot["recommended_variant_id"] == "vae"
    assert vae_slot["variants"][0]["installed"] is True
    assert result.safe_output["already_present"][0]["id"] == "vae"


def test_an_installed_variant_counts_as_present_and_needs_no_download():
    recipe = _recipe([_slot_artifact()])
    repo = FakeModelRepository(present={("diffusion_model", "bf16.safetensors")})
    executor = ArtifactsPlanExecutor(repo, gpu_profile_provider=_ada24)

    result = executor.execute(_context(recipe, ["dit"]))

    assert result.success is True
    assert result.awaiting_consent is False
    assert result.safe_output["already_present"][0]["variant_id"] == "bf16"


def test_installed_flag_follows_a_hash_match_under_another_name():
    from dataclasses import replace
    from types import SimpleNamespace

    from src.features.recipes.schema import RecipeChecksum

    artifact = _slot_artifact()
    variants = list(artifact.variants)
    variants[2] = replace(variants[2], checksum=RecipeChecksum("sha256", "cafe"))
    artifact = replace(artifact, variants=tuple(variants))
    row = SimpleNamespace(model_type="diffusion_model", filename="renamed.safetensors", file_path="/m/renamed.safetensors", is_available=True)
    repo = FakeModelRepository(by_sha256={"cafe": row})
    executor = ArtifactsPlanExecutor(repo, gpu_profile_provider=_ada24)

    result = executor.execute(_context(_recipe([artifact]), ["dit"]))

    assert result.success is True
    present = result.safe_output["already_present"][0]
    assert present["variant_id"] == "nvfp4"
    assert present["found_as"] == "/m/renamed.safetensors"
