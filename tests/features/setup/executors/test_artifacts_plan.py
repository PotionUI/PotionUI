"""`artifacts.plan` against a fake `ModelRepository` surface: present vs
missing by `(model_type, filename)` identity, and the awaiting-consent shape."""

from src.features.setup.executors.artifacts_plan import ArtifactsPlanExecutor
from src.features.setup.executors.base import StepContext
from src.features.setup.recipe_schema import Recipe, RecipeArtifact, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakeModelRepository:
    def __init__(self, present=()):
        self.present = set(present)  # set of (model_type, filename)

    def get_by_identity(self, model_type, filename, include_providers=True):
        return object() if (model_type, filename) in self.present else None


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
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    step = RecipeStep(key="artifacts.plan", kind="artifacts.plan", title="Plan", params={"artifact_ids": artifact_ids})
    return StepContext(run=run, recipe=recipe, step=step)


def _artifact(aid, filename="model.safetensors", size_bytes=100, provider_hint=None):
    return RecipeArtifact(
        id=aid,
        kind="checkpoint",
        model_type="checkpoint",
        filename=filename,
        display_name=aid,
        size_bytes=size_bytes,
        provider_hint=provider_hint or {},
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
        {"id": "ckpt", "display_name": "ckpt", "size_bytes": 12345, "kind": "checkpoint"}
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
