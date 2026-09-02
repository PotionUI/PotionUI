"""Contributing a phrasebook batch tool.

A batch tool is an action the Phrasebook's Find & replace selection bar can
run over the values a user has selected - a bulk rewrite, an enrichment, a
sync to an external service, anything that takes a list of value ids.
Declare it in `manifest.yml` under `phrasebook_ops:`, pointing `backend` at a
`PhrasebookBatchOperation` subclass and (optionally) `component` at the
plugin frontend asset that collects its parameters in a modal.

Subclass `PhrasebookBatchOperation` and implement `run()`: read whatever
your modal posted in `params`, touch values through `ctx` (never the
repositories - `ctx` is scoped to the calling user and writes each call in
one transaction), and return a `BatchOutcome`; its `message` is what the UI
toasts. Set `supports_preview = True` and implement `preview()` to offer a
dry run (`BatchPreview`) before the user applies.

Core's replace / set_active / move / delete are registered through the same
registry, so your operation lists beside them in `GET /api/phrasebook/batch-ops`
and runs through the same `phrasebook.batch.before` / `phrasebook.batch.after`
hooks.
"""

from src.platform.plugins.phrasebook_ops import (
    BatchOperationError,
    BatchOutcome,
    BatchPreview,
    PhrasebookBatchContext,
    PhrasebookBatchOperation,
)

__all__ = [
    "BatchOperationError",
    "BatchOutcome",
    "BatchPreview",
    "PhrasebookBatchContext",
    "PhrasebookBatchOperation",
]
