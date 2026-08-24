"""Structured errors raised by the native expression evaluator.

Every evaluation/render failure in ``TemplateProcessor`` raises a
``TemplateEvaluationError`` rather than being swallowed. Callers upstream
(preset processor, pipeline builder) attach location info via
``with_location`` as the error propagates.
"""

from typing import Any, Optional


class TemplateEvaluationError(Exception):
    """Raised when a template scalar fails to render or evaluate.

    Attributes:
        expression: The Jinja source that failed (the exact-expression's
            inner text, or the full template string for a string render).
        cause: The underlying exception (UndefinedError, SecurityError,
            TemplateSyntaxError, ...).
        preset_id, source_file, mode, form_name, pipe_id, config_path:
            Location fields. All optional at raise time - the evaluator
            itself only knows the expression; callers further up the stack
            enrich the error with where it was found via ``with_location``.
    """

    def __init__(
        self,
        expression: str,
        cause: BaseException,
        *,
        preset_id: Optional[str] = None,
        source_file: Optional[str] = None,
        mode: Optional[str] = None,
        form_name: Optional[str] = None,
        pipe_id: Optional[str] = None,
        config_path: Optional[str] = None,
    ):
        self.expression = expression
        self.cause = cause
        self.preset_id = preset_id
        self.source_file = source_file
        self.mode = mode
        self.form_name = form_name
        self.pipe_id = pipe_id
        self.config_path = config_path
        super().__init__(self._message())

    def _message(self) -> str:
        location_bits = [
            f"{name}={value!r}"
            for name, value in (
                ("preset_id", self.preset_id),
                ("source_file", self.source_file),
                ("mode", self.mode),
                ("form_name", self.form_name),
                ("pipe_id", self.pipe_id),
                ("config_path", self.config_path),
            )
            if value is not None
        ]
        location = f" [{', '.join(location_bits)}]" if location_bits else ""
        return f"Template evaluation failed{location}: {self.expression!r}: {self.cause}"

    def with_location(self, **fields: Any) -> "TemplateEvaluationError":
        """Return self with any unset location fields filled in from ``fields``.

        Only fills fields that are still ``None`` so the innermost/first
        caller to attach location info wins; mutates and returns ``self`` so
        it composes naturally with ``raise ... from err`` chains.
        """
        for name, value in fields.items():
            if not hasattr(self, name):
                raise TypeError(f"Unknown location field: {name}")
            if getattr(self, name) is None and value is not None:
                setattr(self, name, value)
        self.args = (self._message(),)
        return self

    def __str__(self) -> str:
        return self._message()
