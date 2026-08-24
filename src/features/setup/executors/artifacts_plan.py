"""`artifacts.plan` - work out which of the recipe's declared artifacts are
already on disk and which still need fetching, using the model index's
`(model_type, filename)` identity (see `src.features.models.repository.
ModelRepository.get_by_identity`) - the same identity `BackendModelIndexer`/
`ModelScanner` enforce with a `UNIQUE(model_type, filename)` constraint, so
"present" here means the same thing "present" means everywhere else in the
app.

Nothing missing -> the step just succeeds ("nothing to download"). Anything
missing -> the step parks in `awaiting_consent` carrying a `consent_request`
(pinned contract: `{"artifacts": [...], "total_bytes", "providers"?}`)
describing exactly what would be downloaded, for the owner to approve before
any network access happens. `providers` is only present when at least one
missing artifact resolves (by `provider_hint.source`) to a provider that
takes a credential and doesn't have one configured yet - see
`_provider_credentials.credential_prompt_for_provider` - so the consent gate
can offer to collect it inline rather than the owner discovering the gap only
after a download fails. `SetupRunManager.grant_consent` is what lets it
proceed; the approved artifact list travels forward as `consent_request`
inside that attempt's `safe_output`, which `artifacts.fetch` reads back out.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.features.models.repository import ModelRepository
from src.features.setup.executors._provider_credentials import (
    credential_prompt_for_provider,
    resolve_provider_registry,
)
from src.features.setup.executors.base import StepContext, StepResult


class ArtifactsPlanExecutor:
    def __init__(self, model_repository: ModelRepository, provider_registry_factory=None):
        self.model_repository = model_repository
        # Lazy/optional, mirrors `ArtifactsFetchExecutor` - see its
        # docstring on why provider-registry resolution isn't forced in the
        # constructor. Injectable for tests; defaults to the real
        # module-level registry.
        self._provider_registry_factory = provider_registry_factory

    def execute(self, context: StepContext) -> StepResult:
        artifact_ids = context.step.params.get("artifact_ids") or []
        if not artifact_ids:
            return StepResult.fail(
                "ARTIFACTS_PLAN_MISCONFIGURED",
                "This step doesn't say which artifacts to plan for.",
            )

        missing: List[Dict[str, Any]] = []
        missing_artifacts: List[Any] = []
        present: List[Dict[str, Any]] = []
        for artifact_id in artifact_ids:
            artifact = context.recipe.get_artifact(artifact_id)
            if artifact is None:
                return StepResult.fail(
                    "ARTIFACTS_PLAN_MISCONFIGURED",
                    f"This step references an artifact ('{artifact_id}') the recipe doesn't declare.",
                )
            existing = self.model_repository.get_by_identity(artifact.model_type, artifact.filename)
            entry = {
                "id": artifact.id,
                "display_name": artifact.display_name or artifact.filename,
                "size_bytes": artifact.size_bytes,
                "kind": artifact.kind,
            }
            if existing is not None:
                present.append(entry)
            else:
                missing.append(entry)
                missing_artifacts.append(artifact)

        if not missing:
            return StepResult.ok(
                {
                    "message": "Everything this recipe needs is already available - nothing to download.",
                    "already_present": present,
                }
            )

        sizes = [a["size_bytes"] for a in missing if a["size_bytes"] is not None]
        total_bytes = sum(sizes) if sizes and len(sizes) == len(missing) else None
        consent_request: Dict[str, Any] = {"artifacts": missing, "total_bytes": total_bytes}
        providers = self._unconfigured_credential_providers(missing_artifacts)
        if providers:
            consent_request["providers"] = providers
        return StepResult.awaiting(
            consent_request,
            safe_output={"already_present": present} if present else None,
        )

    def _get_provider_registry(self):
        return resolve_provider_registry(self._provider_registry_factory)

    def _unconfigured_credential_providers(self, artifacts: List[Any]) -> List[Dict[str, Any]]:
        sources = sorted({(a.provider_hint or {}).get("source") for a in artifacts if (a.provider_hint or {}).get("source")})
        if not sources:
            return []
        registry = self._get_provider_registry()
        if registry is None:
            return []
        prompts = []
        for source in sources:
            info = credential_prompt_for_provider(registry, source)
            if info and not info["configured"]:
                prompts.append(info)
        return prompts
