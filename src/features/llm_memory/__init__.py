"""LLM memory management for persistent cross-session notes.

`operations` (module-level functions over `LLMMemoryRepository`) replaces the
former `LLMMemoryManager` class - see that package's docstring. Callers that
used to hold an `LLMMemoryManager` now hold an `LLMMemoryRepository` instead
(field/param renamed `llm_memory_manager` -> `llm_memory_repository`
throughout `chat`, `mcp`, `prompt_enhancement`, and `ToolContext`).
"""
