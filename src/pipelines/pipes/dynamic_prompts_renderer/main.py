"""Surfaces the per-image rendered prompts as first-class provenance.

The dynamic constructs in an authored prompt - ``{a|b}`` choices,
``${variables}`` and phrasebook-sourced values - are all resolved on the
backend before the pipeline runs, once per image against ``base_seed + index``
(``src/features/prompt/expander.py``, driven from the orchestrator's
``PromptExpander``). The result reaches the pipeline as
``generation.prompts.pairs`` and every family's preset hands it here verbatim as
``pairs``.

This pipe does no expansion of its own - keeping the ``dynamicprompts`` library
usage in one place - and imports nothing from ``src.features`` (layering). It is
a pure emitter, the prompt counterpart of ``seed_generator``: it turns the
already-resolved per-image pairs into ``RenderedPromptGenerationOutput``
artifacts so the concrete prompt each image ran with is visible in the pipeline
view/history exactly like the seed.
"""

from typing import Dict, Any, List

from src.pipelines.outputs import RenderedPromptGenerationOutput, ProgressGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)


class DynamicPromptsRenderedPipe(BasePipe):
    name = "dynamic_prompts_renderer"
    description = (
        "Emits the fully-rendered per-image prompts ({a|b}, ${variables} and "
        "phrasebook already resolved on the backend) as provenance artifacts"
    )

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        pairs = self.config.get("pairs") or []
        if not pairs:
            # No per-image pairs were wired (e.g. a preset that doesn't expand,
            # or a legacy submission). Nothing to surface - stay silent rather
            # than invent an empty artifact.
            return PipeOutput(output={})

        try:
            quantity = int(self.config.get("quantity", len(pairs)))
        except (TypeError, ValueError):
            quantity = len(pairs)

        # Native produces one pair per image (len(pairs) == quantity). A
        # single-workflow engine (ComfyUI) produces only pairs[0] but runs
        # `quantity` images off it, so broadcast the last known pair to fill the
        # batch - every image then reports the prompt it actually ran with.
        count = max(len(pairs), quantity)

        generation_outputs(ProgressGenerationOutput(state="Resolving prompts"))

        for index in range(count):
            pair = pairs[index] if index < len(pairs) else pairs[-1]
            if not isinstance(pair, dict):
                logger.warning(
                    f"[DYNAMIC_PROMPTS_RENDERED] pair at index {index} is not a "
                    f"mapping ({type(pair).__name__}); emitting empty prompt"
                )
                pair = {}
            generation_outputs(RenderedPromptGenerationOutput(
                index=index,
                positive=(pair.get("positive") or ""),
                negative=(pair.get("negative") or ""),
            ))

        return PipeOutput(output={})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "pairs": [],
            "quantity": 1,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                "pairs", list, [],
                "Per-image expanded prompt pairs (from generation.prompts.pairs)",
                required=False,
            ),
            PipeConfigSpec(
                "quantity", int, 1,
                "Number of images being generated (fills the batch when only "
                "pairs[0] was produced)",
                required=False, min_value=1, max_value=20,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Pure emitter - reads the resolved pairs from configuration, like seed_generator."""
        return []

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """No IOType outputs - this pipe only emits provenance generation outputs."""
        return []
