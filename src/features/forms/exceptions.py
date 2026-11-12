"""Domain exceptions raised while resolving and binding a preset's forms."""


class FormNotFoundException(Exception):
    """Raised when a mode has no form under the requested name."""

    def __init__(self, preset_id: str, mode: str, form_name: str = None, replaced_by: str = None):
        self.preset_id = preset_id
        self.mode = mode
        self.form_name = form_name
        self.replaced_by = replaced_by
        message = f"No form configuration found for mode '{mode}'"
        if form_name:
            message += f" and form '{form_name}'"
        message += f" in preset '{preset_id}'"
        if replaced_by:
            message += f". Mode '{mode}' was replaced by '{replaced_by}'"
        super().__init__(message)
