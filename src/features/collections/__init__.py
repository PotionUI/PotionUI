"""
Collection administration.

`operations` (`operations/`) drives collection mutations on behalf of
`CollectionController` (`routes.py`) and outside callers (the
`manage_collections` chat/MCP tool, `GenerationOrchestrator`'s auto-tagging
path): module-level functions taking a `CollectionRepository` as an explicit
argument. `list_collections` is a pure repository call made directly by
callers; `get_collection` is the shared ownership/scope-checked lookup every
mutation (and several outside callers) needs.
"""
