"""
Form operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. `get_field_options` takes the
`field_registry`/`plugin_registry` it needs; `validate_form_data` takes
`plugin_registry`; `get_form_defaults` needs neither. `FormController`
(`routes.py`) holds the collaborators and passes them in.

`get_select_options`/`get_model_database_options`/`get_checkbox_options` are
the concrete option loaders `register_builtin_fields`
(`src.features.fields.builtin`) wires onto the `FieldTypeRegistry` as each
field type's `options_provider` - `get_field_options` above dispatches
through that same table, so a field type's options load exactly one way
regardless of caller. `model_directories`/`settings` were accepted by the
old `FormManager` but never read anywhere in this domain - dropped rather
than forwarded, per the `WorkspaceManager` precedent (batch 1).

Shape rule: one module per concern (`options`, `validation`, `defaults`) -
each re-exported here as the public surface. Callers import from the package
(`from src.features.forms import operations`), never from a submodule
directly.
"""
from src.features.forms.operations.options import (
    get_field_options,
    get_select_options,
    get_model_database_options,
    get_checkbox_options,
)
from src.features.forms.operations.validation import validate_form_data
from src.features.forms.operations.defaults import get_form_defaults

__all__ = [
    "get_field_options",
    "get_select_options",
    "get_model_database_options",
    "get_checkbox_options",
    "validate_form_data",
    "get_form_defaults",
]
