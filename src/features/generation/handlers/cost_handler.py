import logging
from typing import Any, Dict, Optional

from src.features.cloud.cost_repository import GenerationCostRepository
from src.features.cloud.costs import SOURCE_ESTIMATE, SOURCE_PROVIDER, SOURCE_UNKNOWN, estimate
from src.features.cloud.repository import CloudCatalogRepository
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, output_type_registry
from src.pipelines.outputs import CostGenerationOutput
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE

logger = logging.getLogger(__name__)


class CostGenerationOutputHandler(BaseGenerationOutputHandler):
    def can_handle(self, output: Any) -> bool:
        return isinstance(output, CostGenerationOutput)

    def handle(self, output: CostGenerationOutput) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {"handler": "CostGenerationOutputHandler", "processed": True}
        try:
            self._record(output)
        except Exception as error:
            metadata["processed"] = False
            metadata["error"] = "cost not recorded"
            logger.error(f"[COST HANDLER] Could not record the full cost of generation {self.generation_id}: {error}")
            self._record_fallback(output, error)
        return metadata

    def _record(self, output: CostGenerationOutput) -> None:
        from src.features.generation.repository import generation_repo
        from src.features.models.repository import model_repo

        generation = generation_repo.get_by_id(self.generation_id)
        backend_id = getattr(generation, "backend_id", None)
        model = model_repo.get_by_identity(CLOUD_MODEL_TYPE, output.model, include_providers=False)
        model_id = model.id if model else None

        amount = output.amount_usd
        source = output.source if amount is not None else SOURCE_UNKNOWN
        detail: Dict[str, Any] = {"task": output.task, "count": output.count, "outputs": output.outputs}
        if amount is None:
            guessed = self._estimate(backend_id, output)
            if guessed is not None:
                amount, source = guessed.amount_usd, SOURCE_ESTIMATE
                detail.update(guessed.detail)
        GenerationCostRepository().record(
            self.generation_id,
            backend_id=backend_id,
            model_id=model_id,
            user_id=self.user_id,
            amount_usd=amount,
            source=source if source in (SOURCE_PROVIDER, SOURCE_ESTIMATE) else SOURCE_UNKNOWN,
            detail=detail,
        )

    def _record_fallback(self, output: CostGenerationOutput, error: Exception) -> None:
        try:
            backend_id = self._known_backend_id()
            GenerationCostRepository().record(
                self.generation_id,
                backend_id=backend_id,
                model_id=None,
                user_id=self.user_id,
                amount_usd=None,
                source=SOURCE_UNKNOWN,
                detail={
                    "model": output.model,
                    "error": str(error),
                    "provider_amount_usd": str(output.amount_usd) if output.amount_usd is not None else None,
                    "task": output.task,
                    "outputs": output.outputs,
                },
            )
        except Exception as write_error:
            logger.error(
                f"[COST HANDLER] Cost of generation {self.generation_id} was not recorded at all: "
                f"provider amount {output.amount_usd} USD (source {output.source}), model {output.model}, "
                f"error {write_error}"
            )

    def _known_backend_id(self) -> Optional[str]:
        try:
            from src.features.generation.repository import generation_repo

            return getattr(generation_repo.get_by_id(self.generation_id), "backend_id", None)
        except Exception:
            return None

    @staticmethod
    def _estimate(backend_id: Optional[str], output: CostGenerationOutput) -> Optional[Any]:
        if not backend_id:
            return None
        entries = CloudCatalogRepository().get_many(backend_id, [output.model])
        if not entries:
            return None
        return estimate(entries[0].spec, output.task, output.params, output.count, output.outputs)


output_type_registry.register(OutputTypeSpec(
    output_cls=CostGenerationOutput,
    key="cost",
    message_type="generation_update",
    serializer=None,
    handler_cls=CostGenerationOutputHandler,
    server_only=True,
))
