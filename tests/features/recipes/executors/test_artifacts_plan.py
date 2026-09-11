"""`artifacts.plan` against a fake `ModelRepository` surface: present vs
missing by `(model_type, filename)` identity, and the awaiting-consent shape."""

from src.features.recipes.executors.artifacts_plan import ArtifactsPlanExecutor
from src.features.recipes.executors.base import StepContext
from src.features.recipes.schema import Recipe, RecipeArtifact, RecipeStep
from src.features.recipes.records import RecipeRun, RecipeRunStatus


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
    executor = ArtifactsPlanExecutor(repo)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.awaiting_consent is True
    assert result.consent_request["total_bytes"] == 12345
    assert result.consent_request["artifacts"] == [
        {
            "id": "ckpt",
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
