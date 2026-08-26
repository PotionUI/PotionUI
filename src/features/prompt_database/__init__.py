"""
Prompt database domain module.

Owns normalized prompt aggregates. A prompt is one channel-agnostic ordered
list; positive/negative usage is a browsing hint only and never a coupled
pair or generation configuration.

`operations` (`operations/`) drives prompt mutations, search, and embedding
on behalf of `PromptDatabaseController` (`routes.py`) and outside callers
(the chat/MCP saved-prompts tools, `PromptEnhancementManager`,
`src.plugin_api.prompts`): module-level functions taking a
`PromptDatabaseCollaborators` bundle (`collaborators.py`) as their leading
arg.
"""
