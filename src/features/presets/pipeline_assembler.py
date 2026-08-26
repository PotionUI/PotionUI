"""The pipeline-build contract `operations.get_pipeline` depends on.

`operations.get_pipeline` renders a preset's pipeline graph for the UI, which
means it has to build the pipeline exactly the way an execution does -
through the one canonical builder, never a preview-only reimplementation. It
needs nothing of that builder beyond the call below, so it states that need
as a structural type and lets the composition root hand it the real
implementation.
"""

from typing import Any, Dict, List, Protocol


class AssembledPipeline(Protocol):
    """The part of a built pipeline the graph projection reads."""

    pipes: List[Dict[str, Any]]


class PipelineAssembler(Protocol):
    """Turns a preset template plus a bound form submission into a pipe list."""

    def build_pipeline(
        self,
        preset_id: Any,
        form_data: Dict[str, Any],
        mode: str = ...,
        **kwargs: Any,
    ) -> AssembledPipeline:
        ...
