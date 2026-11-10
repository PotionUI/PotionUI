from typing import Any, Dict, List

from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.pipes._shared.regional import (
    compile_moss_prompt,
    normalize_regional_prompt,
    parse_resolution,
)


class RegionalPromptMossImagePipe(BasePipe):
    name = "regional_prompt"
    description = "Rewrites each positive prompt into MOSS-Image's layout document from boxes drawn on the canvas"
    display_title = "Placing regions"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        pairs: List[Dict[str, Any]] = [dict(pair or {}) for pair in (self.config.get("pairs") or [])]
        document = normalize_regional_prompt(self.config.get("regional_prompt"))
        if not document.active:
            return PipeOutput(output={"pairs": pairs})
        width, height = parse_resolution(self.config.get("resolution"))
        for pair in pairs:
            pair["positive"] = compile_moss_prompt(pair.get("positive") or "", document, width, height)
        return PipeOutput(output={"pairs": pairs})

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                "pairs", list, [],
                "Per-image expanded prompt pairs (from generation.prompts.pairs)",
                required=False,
            ),
            PipeConfigSpec(
                "regional_prompt", dict, {},
                "Regional prompt document: enabled flag and items of fractional x, y, w, h plus a caption",
                required=False,
            ),
            PipeConfigSpec(
                "resolution", str, "1024x1024",
                "Generation size as WxH, the space the boxes are placed in",
                required=False,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return []

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec(
                "pairs", IOType.DICT,
                "Prompt pairs with each positive rewritten as the MOSS-Image layout document",
                is_array=True,
            ),
        ]
