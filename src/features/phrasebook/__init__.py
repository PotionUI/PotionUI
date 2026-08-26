"""
Phrasebook domain module.

`operations` (`operations/`) drives phrasebook mutations on behalf of
`PhrasebookController` (`routes.py`) and outside callers (the phrasebook
chat/MCP tool surface, the `@phrasebook` resource provider): module-level
functions taking `PhrasebookCategoryRepository`/`PhrasebookValueRepository`/
`PluginRegistry` as explicit arguments.
"""
from src.features.phrasebook.preview_generator import PhrasebookPreviewGenerator

__all__ = ["PhrasebookPreviewGenerator"]
