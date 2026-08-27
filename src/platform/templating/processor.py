"""
Native Jinja2 expression evaluator/renderer.

Two execution paths, chosen by shape:

- **Exact expression**: a scalar that is, after stripping whitespace,
  precisely one ``{{ expression }}`` block. Compiled with
  ``Environment.compile_expression`` and evaluated to its *native* Python
  value (int/float/bool/list/dict/None/...). This is the path `@loop` items
  and typed config fields (steps, cfg, loras, enabled, ...) go through.
- **String template**: anything else containing template syntax (mixed
  text, multiple ``{{ }}`` blocks, or any ``{% %}`` statement) - rendered
  the normal Jinja way and returned as a string, newlines preserved.

The environment is an ``ImmutableSandboxedEnvironment`` (blocks unsafe
attribute access AND mutating methods like ``list.append``/``dict.update``)
with ``StrictUndefined`` (any operation on a missing variable raises).
Rendering/evaluation failures raise ``TemplateEvaluationError`` - there is
no catch-log-return-None left in this module.
"""

import re
from typing import Any, Dict, Optional, Tuple

from jinja2 import BaseLoader, StrictUndefined, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from src.platform.observability.logger import logger
from src.platform.settings.settings import Settings
from src.platform.templating.errors import TemplateEvaluationError
from src.platform.templating.hooks import TEMPLATE_HOOKS
from src.platform.templating.path_resolver import PathResolver
from src.platform.templating.icon_mapper import IconMapper
from src.platform.templating.dict_utils import (
    active_loras,
    regex_search,
    get_speed_profile_value,
    _NO_DEFAULT as _NO_SPEED_PROFILE_DEFAULT,
)


def _extract_exact_expression(value: str) -> Optional[str]:
    """Return the inner source of an exact `{{ expression }}` scalar, or None.

    "Exact" means: after stripping surrounding whitespace, the string starts
    with `{{` and ends with `}}`, and contains no further `{{`/`}}`/`{%`/`%}`
    inside - i.e. precisely one expression block and nothing else (no
    surrounding text, no second block, no statement tags). Jinja expression
    syntax never itself contains `{{`/`}}` (dict/set literals use single
    braces), so this is a safe, simple check without a real parse.
    """
    stripped = value.strip()
    if not (stripped.startswith("{{") and stripped.endswith("}}")):
        return None

    inner = stripped[2:-2]
    if "{{" in inner or "}}" in inner or "{%" in inner or "%}" in inner:
        return None

    return inner.strip()


class TemplateProcessor:
    """
    Evaluate/render Jinja2 expressions and templates for the preset/form/pipeline systems.

    Public API kept stable across the templating rework (only the callers'
    inputs/outputs change meaning): ``process_template(value, context)`` is
    still how every caller in the codebase invokes this class.
    """

    def __init__(
        self,
        settings: Settings,
        path_resolver: Optional[PathResolver] = None,
        icon_mapper: Optional[IconMapper] = None,
    ):
        """
        Initialize TemplateProcessor.

        Args:
            settings: Settings manager for configuration access.
            path_resolver: Optional custom path resolver (defaults to PathResolver()).
            icon_mapper: Optional custom icon mapper (defaults to IconMapper()).
        """
        self.settings = settings
        self.path_resolver = path_resolver or PathResolver()
        self.icon_mapper = icon_mapper or IconMapper()

        # Sandboxed + strict: unsafe attribute access, mutating methods (list.append,
        # dict.update, ...), and missing variables all raise instead of silently
        # producing a blank/garbage result.
        self.env = ImmutableSandboxedEnvironment(
            loader=BaseLoader(),
            undefined=StrictUndefined,
            variable_start_string='{{',
            variable_end_string='}}',
            block_start_string='{%',
            block_end_string='%}',
            trim_blocks=True,
            lstrip_blocks=True,
            # Jinja defaults to eating one trailing newline from the source;
            # multiline pipeline.yml/config templates must preserve it verbatim.
            keep_trailing_newline=True,
        )

        self._register_globals()
        self._register_filters()

    def _register_globals(self) -> None:
        """Register the allowlisted global functions in the Jinja environment.

        Only these three globals are allowed: `path`/`get_path_for`,
        `icon`/`get_icon`, `get_speed_profile`. Other render globals
        (`get_form`, `value`/`get`, `contains`/`get_is_in`, `dict`,
        `setting`/`config`) are intentionally absent - `form.x`, `preset.vars.x`,
        and `runtime.settings.x` native attribute access replace them.
        """
        self.env.globals['path'] = self.get_path_for
        self.env.globals['get_path_for'] = self.get_path_for
        self.env.globals['icon'] = self.get_icon
        self.env.globals['get_icon'] = self.get_icon
        # get_speed_profile needs the per-call context (preset.speed_profiles),
        # so it's bound per-render in process_template/evaluate_expression rather
        # than registered here as a context-free global.

    def _register_filters(self) -> None:
        """Register custom filters in Jinja environment."""
        self.env.filters['matches'] = self.regex_search
        self.env.filters['regex_search'] = self.regex_search
        self.env.filters['active_loras'] = self.active_loras

    def _get_plugin_registry(self):
        """Get plugin registry for hook execution (lazy load to avoid import cycles)."""
        try:
            from src.platform.plugins.runtime_registries import get_global_plugin_registry
            return get_global_plugin_registry()
        except Exception:
            return None

    def _execute_hook(self, hook_name: str, data: dict) -> Tuple[dict, bool]:
        """
        Execute a plugin hook.

        Args:
            hook_name: The hook name to execute.
            data: The data to pass to the hook.

        Returns:
            Tuple of (modified_data, blocked).
        """
        plugins = self._get_plugin_registry()
        if plugins is None:
            return data, False

        try:
            context, _ = plugins.execute_hook(hook_name, initial_data=data)
            blocked = context.data.get("blocked", False)
            return context.data, blocked
        except Exception as e:
            logger.debug(f"Hook execution failed: {e}")
            return data, False

    def process_template(self, template: Any, context: Dict[str, Any]) -> Any:
        """
        Process a template value with the given context.

        - dict/list: recurse into values/items (structure preserved).
        - non-string scalar: passed through untouched.
        - string that is exactly one `{{ expression }}` block (surrounding
          whitespace ok): evaluated to its native Python value.
        - any other string containing template syntax (mixed text, multiple
          blocks, `{% %}` statements): rendered as a string, newlines
          preserved.

        Args:
            template: The value to process.
            context: Dictionary of context variables for the template.

        Returns:
            The native value (exact-expression path) or rendered string
            (string-template path), or `template` unchanged if it isn't a
            string.

        Raises:
            TemplateEvaluationError: if evaluation/rendering fails (missing
                variable, sandbox violation, syntax error, ...).
        """
        if isinstance(template, dict):
            return {k: self.process_template(v, context) for k, v in template.items()}
        if isinstance(template, list):
            return [self.process_template(v, context) for v in template]
        if not isinstance(template, str):
            return template

        # Execute before_process hook
        hook_data, blocked = self._execute_hook(
            TEMPLATE_HOOKS.before_process,
            {"template": template, "context": context}
        )
        if blocked:
            logger.debug("Template processing blocked by hook")
            return None

        template = hook_data.get("template", template)
        context = hook_data.get("context", context)

        expr_source = _extract_exact_expression(template)
        if expr_source is not None:
            result = self._evaluate(expr_source, context)
        else:
            result = self._render(template, context)

        self._execute_hook(
            TEMPLATE_HOOKS.after_process,
            {"template": template, "context": context, "result": result}
        )
        return result

    def evaluate_expression(self, scalar: str, context: Dict[str, Any]) -> Any:
        """
        Evaluate an exact-expression scalar to its native Python value.

        Used directly by `@loop` (`items:`) so loop expansion never needs the
        rendered-string + `ast.literal_eval` round-trip: `items` must itself
        be an exact expression yielding a list/dict/range.

        Args:
            scalar: A string that must be exactly one `{{ expression }}` block.
            context: The template context.

        Returns:
            The expression's native value.

        Raises:
            TemplateEvaluationError: if `scalar` is not an exact-expression
                string, or if evaluation fails.
        """
        expr_source = _extract_exact_expression(scalar) if isinstance(scalar, str) else None
        if expr_source is None:
            raise TemplateEvaluationError(
                expression=str(scalar),
                cause=ValueError(
                    "not an exact `{{ expression }}` scalar "
                    "(mixed text, multiple blocks, or a `{% %}` statement)"
                ),
            )
        return self._evaluate(expr_source, context)

    def _call_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Build the kwargs passed into a compiled expression/template.

        Adds `get_speed_profile`, which (unlike the other globals) needs the
        per-render context bound in, since it looks up `preset.speed_profiles`.
        """
        call_context = dict(context)
        call_context['get_speed_profile'] = lambda profile_name, default=_NO_SPEED_PROFILE_DEFAULT: \
            self.get_speed_profile(context, profile_name, default)
        return call_context

    def _evaluate(self, expr_source: str, context: Dict[str, Any]) -> Any:
        """Compile and evaluate an exact expression, raising TemplateEvaluationError on failure."""
        try:
            compiled = self.env.compile_expression(expr_source, undefined_to_none=False)
            result = compiled(**self._call_context(context))
            if isinstance(result, Undefined):
                result._fail_with_undefined_error()
            return result
        except TemplateEvaluationError:
            raise
        except Exception as e:
            raise TemplateEvaluationError(expr_source, e) from e

    def _render(self, template: str, context: Dict[str, Any]) -> str:
        """Render a string template, raising TemplateEvaluationError on failure."""
        try:
            template_obj = self.env.from_string(template)
            return template_obj.render(self._call_context(context))
        except TemplateEvaluationError:
            raise
        except Exception as e:
            raise TemplateEvaluationError(template, e) from e

    def get_path_for(self, path_type: str, file_name: str = None) -> str:
        """
        Resolve a path based on the type and name.

        Args:
            path_type: The type of the resource (e.g., "lora", "model").
            file_name: The name or identifier of the resource.

        Returns:
            The resolved path as a string.
        """
        # Execute resolve_path hook to allow plugins to add custom path types
        hook_data, _ = self._execute_hook(
            TEMPLATE_HOOKS.resolve_path,
            {"path_type": path_type, "file_name": file_name}
        )

        # If hook provided a resolved path, use it
        if "resolved_path" in hook_data:
            return hook_data["resolved_path"]

        return self.path_resolver.get_path_for(path_type, file_name)

    def get_icon(self, icon_type: str) -> str:
        """
        Get an icon name for the specified type.

        Args:
            icon_type: The type of icon needed (e.g., "prompt", "lora").

        Returns:
            The icon name/identifier to be used in the frontend.
        """
        return self.icon_mapper.get_icon(icon_type)

    def active_loras(self, value: Any) -> Any:
        """
        Drop LoRA entries whose strength is zero (see `dict_utils.active_loras`).

        Args:
            value: The LoRA list.

        Returns:
            The filtered list.
        """
        return active_loras(value)

    def regex_search(self, value: str, pattern: str) -> bool:
        """
        Check if a regex pattern matches a value.

        Args:
            value: The string to search in.
            pattern: The regex pattern to match.

        Returns:
            True if the pattern matches, False otherwise.
        """
        return regex_search(value, pattern)

    def get_speed_profile(
        self,
        context: Dict[str, Any],
        profile_name: str,
        default: Any = _NO_SPEED_PROFILE_DEFAULT,
    ) -> Any:
        """
        Look up a named entry from preset.yml's `speed_profiles:` block.

        Args:
            context: The template context.
            profile_name: The profile name to look up (e.g. 'draft').
            default: Returned when the profile is missing; if omitted, raises
                ``ValueError`` naming both the preset and the missing profile.

        Returns:
            The profile's dict of overrides.
        """
        return get_speed_profile_value(context, profile_name, default)
