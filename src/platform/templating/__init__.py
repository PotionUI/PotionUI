"""
Native Jinja2 expression evaluator for the preset/form/pipeline systems.

An exact-expression scalar (a string that is, after stripping whitespace,
precisely one ``{{ expression }}`` block) evaluates to its native Python
value (int/float/bool/list/dict/None/...) via ``Environment.compile_expression``.
Anything else (mixed text, multiple blocks, ``{% %}`` statements) renders as
a normal string template. The environment is sandboxed (``ImmutableSandboxedEnvironment``)
with ``StrictUndefined`` - missing values and unsafe attribute/method access
raise ``TemplateEvaluationError`` rather than being swallowed.

Components:
    - TemplateProcessor: the evaluator/renderer
    - TemplateEvaluationError: structured error carrying expression + cause + location
    - PathResolver: resolves resource paths (checkpoints, loras, etc.)
    - IconMapper: maps icon types to icon names
    - dict_utils: regex_search filter + get_speed_profile_value helper

Usage:
    from src.platform.templating import TemplateProcessor

    processor = TemplateProcessor(settings_manager)
    result = processor.process_template("{{ 1 + 1 }}", {})       # -> 2 (int)
    result = processor.process_template("Hello {{ name }}", {"name": "World"})  # -> "Hello World"
"""

from src.platform.templating.processor import TemplateProcessor
from src.platform.templating.errors import TemplateEvaluationError

__all__ = [
    "TemplateProcessor",
    "TemplateEvaluationError",
]
