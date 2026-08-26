"""
LLM configuration/generation/assignment operations.

Post-Manager reference shape (see `src/features/plugins/operations/`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the collaborators it needs (repository, plugin
registry, and - only where relevant - the LLM gateway, settings manager, or
tool-governance repository) as leading arguments, followed by the operation's
own parameters. `LLMController` holds the collaborators and passes them in;
nothing here is stored across calls.

Shape rule: one module per concern (`configuration`, `generation`,
`assignments`), each re-exported here as the public surface - split a module
before it outgrows ~200 lines rather than let it absorb an unrelated concern.
Callers import from the package (`from src.features.llm import operations`),
never from a submodule directly.
"""
from src.features.llm.operations.configuration import (
    create_configuration,
    update_configuration,
    delete_configuration,
    set_default_provider,
)
from src.features.llm.operations.generation import generate_response
from src.features.llm.operations.assignments import (
    assign_llm_to_user,
    unassign_llm_from_user,
)

__all__ = [
    "create_configuration",
    "update_configuration",
    "delete_configuration",
    "set_default_provider",
    "generate_response",
    "assign_llm_to_user",
    "unassign_llm_from_user",
]
