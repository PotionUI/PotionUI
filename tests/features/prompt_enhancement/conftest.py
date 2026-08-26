"""`PromptEnhancementManager` now calls the module-level
`src.features.prompt_database.operations` functions (`add_prompt`, `search`)
with its `self.prompt_database` collaborator as their leading arg, rather
than calling methods on an injected manager. This fixture patches the
`prompt_database_operations` name as imported into
`src.features.prompt_enhancement.manager` so it forwards straight to that
same call shape (`collaborators.add_prompt(**kwargs)` /
`collaborators.search(**kwargs)`) - every test in this directory can keep
building its `prompt_db` fixture as a plain mock with `.add_prompt`/`.search`
attributes, exactly like the retired manager mock.
"""
from types import SimpleNamespace

import pytest

from src.features.prompt_enhancement import manager as manager_module


@pytest.fixture(autouse=True)
def _forward_prompt_database_operations(monkeypatch):
    async def add_prompt(collaborators, **kwargs):
        return await collaborators.add_prompt(**kwargs)

    async def search(collaborators, **kwargs):
        return await collaborators.search(**kwargs)

    monkeypatch.setattr(
        manager_module, "prompt_database_operations",
        SimpleNamespace(add_prompt=add_prompt, search=search),
    )
