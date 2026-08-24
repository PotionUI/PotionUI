"""The execution contract an in-process backend drives.

A backend's job is to decide *how* a pipeline is prepared for its engine and to
own the lifecycle of a run; the actual pipe-by-pipe execution belongs to
something else. This states what that something else must offer, so the backend
package depends on the capability rather than on a particular implementation of
it - the composition root supplies one per backend.

Implementations are single-occupancy: one run in flight per executor, which is
why every backend gets its own.
"""

from typing import Any, Callable, Dict, List, Optional, Protocol


class PipelineExecutor(Protocol):
    """Runs a prepared pipe list to completion and can abandon it mid-flight."""

    def generate(
        self,
        pipes: List[Dict[str, Any]],
        generation_outputs: Callable[[Any], None],
        generation_id: Optional[str] = None,
        cache_owner: Optional[str] = None,
    ) -> Any:
        """Execute `pipes`, calling `generation_outputs` with each output.

        Blocking: the caller runs it off the event loop. `cache_owner` tags the
        weights this run caches so a later run can evict them as a group; None
        leaves them untagged.
        """
        ...

    def cancel(self, generation_id: str) -> bool:
        """Abandon `generation_id` if it is the run in flight here.

        Returns False when it is not - already finished, or executing on another
        executor - so a cancel can never abort a run it does not own.
        """
        ...
