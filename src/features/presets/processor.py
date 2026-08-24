"""
Preset processor - processes preset templates with Jinja2 templating.

This module provides the PresetProcessor class that handles template
rendering and value processing for preset configurations.

Templating rework: the pipeline
context built by `process()` is native-first (`form.x` is a real int/list/
dict/etc., not a rendered string) and flat - `input.*` is gone. `@object:`/
`@dict:` are gone too (native attribute access on `form`/`preset`/etc.
replaces them); the only surviving directive is `@loop`, whose `items:` is
now evaluated to a native list/dict/range rather than round-tripped through
a rendered string + `ast.literal_eval`.

Every template failure raises `TemplateEvaluationError` (src/platform/templating/
errors.py); this module's only job on top of that is enriching the error
with *where* it happened (preset_id/source_file/mode/form_name/pipe_id/
config_path) as it propagates, so pipeline builds fail loudly instead of
silently rendering `None` into a config value.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.platform.observability.logger import logger
from src.features.models.directory import ModelManager
from src.platform.settings.settings import SettingsManager
from src.platform.templating import TemplateProcessor
from src.platform.templating.errors import TemplateEvaluationError
from src.features.presets.templates import PresetTemplate, FieldTemplate, default_form_name

from .loader import PresetTemplateLoader

# @loop expansion is capped so a runaway `items:`/`count:` expression (e.g. an
# accidental cartesian product) can't hang preset processing or blow up memory.
_LOOP_ITEMS_CAP = 10_000


class PresetProcessor:
    """
    Processes preset templates with Jinja2 templating.

    Responsibilities:
    - Build the pipeline template context (form/request/generation/preset/
      runtime/paths - see module docstring) and process generation data into
      concrete pipeline configurations
    - Expand `@loop` fields into concrete field definitions
    - Resolve external children files
    - Handle template value processing recursively, enriching any
      `TemplateEvaluationError` with location info as it propagates
    """

    def __init__(
            self,
            template_processor: TemplateProcessor,
            model_manager: ModelManager,
            settings_manager: SettingsManager,
            preset_template_loader: PresetTemplateLoader,
    ):
        self.template_processor = template_processor
        self.model_manager = model_manager
        self.settings_manager = settings_manager
        self.preset_template_loader = preset_template_loader

    def _convert_dict_to_field_template(self, item: dict, context: Dict[str, Any]) -> FieldTemplate:
        """Convert a dictionary to FieldTemplate, recursively processing children"""
        # If children is a list of dicts, convert them to FieldTemplate objects
        if 'children' in item and isinstance(item['children'], list):
            item = item.copy()
            children = []
            for child in item['children']:
                if isinstance(child, dict):
                    children.append(self._convert_dict_to_field_template(child, context))
                elif isinstance(child, FieldTemplate):
                    children.append(child)
            item['children'] = children

        return FieldTemplate(**item)

    def _expand_loop_fields(self, fields: List[FieldTemplate], context: Dict[str, Any]) -> List[FieldTemplate]:
        """Expand @loop fields into concrete field definitions"""
        expanded_fields = []

        for field in fields:
            # Check if this is a loop field
            if field.type == "@loop":
                # Extract loop configuration from field configuration
                if not field.configuration:
                    raise ValueError("@loop field requires configuration with count/items and template")

                loop_config = {
                    'count': field.configuration.get('count'),
                    'items': field.configuration.get('items'),
                    'template': field.configuration.get('template'),
                    'when': field.configuration.get('when'),
                    'as': field.configuration.get('as')
                }

                # Use existing _process_loop to expand the template
                expanded_items = self._process_loop(loop_config, context)

                # Convert expanded items to FieldTemplate objects
                for item in expanded_items:
                    if isinstance(item, dict):
                        # If the item is a dict with field properties, create FieldTemplate
                        try:
                            expanded_field = self._convert_dict_to_field_template(item, context)
                            # Recursively expand loops in children
                            if expanded_field.children and isinstance(expanded_field.children, list):
                                expanded_field_dict = expanded_field.__dict__.copy()
                                expanded_field_dict['children'] = self._expand_loop_fields(expanded_field.children, context)
                                expanded_field = FieldTemplate(**expanded_field_dict)
                            expanded_fields.append(expanded_field)
                        except Exception as e:
                            logger.warning(f"Error creating FieldTemplate from loop expansion: {e}")
                    elif isinstance(item, list):
                        # If the template returns a list of fields, process each
                        for sub_item in item:
                            if isinstance(sub_item, dict):
                                try:
                                    expanded_field = self._convert_dict_to_field_template(sub_item, context)
                                    # Recursively expand loops in children
                                    if expanded_field.children and isinstance(expanded_field.children, list):
                                        expanded_field_dict = expanded_field.__dict__.copy()
                                        expanded_field_dict['children'] = self._expand_loop_fields(expanded_field.children, context)
                                        expanded_field = FieldTemplate(**expanded_field_dict)
                                    expanded_fields.append(expanded_field)
                                except Exception as e:
                                    logger.warning(f"Error creating FieldTemplate from loop expansion: {e}")
            else:
                # Not a loop field, recursively process children
                if field.children and isinstance(field.children, list):
                    field_dict = field.__dict__.copy()
                    field_dict['children'] = self._expand_loop_fields(field.children, context)
                    field = FieldTemplate(**field_dict)
                expanded_fields.append(field)

        return expanded_fields

    def _resolve_loop_items(self, items: Any, context: Dict[str, Any]) -> List[Any]:
        """Resolve `@loop`'s `items:` to a concrete list of loop items.

        `items` must evaluate (natively - no rendered-string + `ast.literal_eval`
        round-trip) to a list, dict, or range. A dict iterates as `(key, value)`
        tuples (so `as: "key,value"` can unpack it); anything else is an error
        naming the actual resolved type, not a silent single-item loop.

        A *literal* YAML list (docs/presets.md: "a literal YAML list is fine")
        is not itself a `{{ expression }}` scalar, so it never passes through
        `evaluate_expression` - each of its elements must be template-processed
        here, or a templated element (e.g. `items: ["{{ form.x }}"]`) reaches
        the loop body unrendered.
        """
        if isinstance(items, str):
            items = self.template_processor.evaluate_expression(items, context)
            is_literal_list = False
        else:
            is_literal_list = isinstance(items, list)

        if isinstance(items, dict):
            resolved = list(items.items())
        elif isinstance(items, range):
            resolved = list(items)
        elif isinstance(items, list):
            resolved = [self.process_value(item, context) for item in items] if is_literal_list else items
        else:
            raise TemplateEvaluationError(
                expression=repr(items),
                cause=TypeError(
                    f"@loop items must evaluate to a list/dict/range, got "
                    f"{type(items).__name__}: {items!r}"
                ),
            )

        if len(resolved) > _LOOP_ITEMS_CAP:
            raise TemplateEvaluationError(
                expression=repr(items),
                cause=ValueError(
                    f"@loop items resolved to {len(resolved)} items, exceeding the "
                    f"cap of {_LOOP_ITEMS_CAP}"
                ),
            )

        return resolved

    def _process_loop(self, loop_config, context):
        """
        Process @loop directive supporting both count and items based loops

        Args:
            loop_config: Dictionary containing loop configuration
            context: Current template context

        Returns:
            List of processed items
        """
        # Extract loop configuration
        count = loop_config.get('count')
        items = loop_config.get('items')
        template = loop_config.get('template')
        when = loop_config.get('when')
        as_var = loop_config.get('as')

        # count may itself be an exact `{{ expression }}` scalar (native int).
        if isinstance(count, str):
            count = self.process_value(count, context)
            if not isinstance(count, int):
                raise TemplateEvaluationError(
                    expression=count if isinstance(count, str) else repr(count),
                    cause=TypeError(f"@loop count must evaluate to an int, got: {count!r}"),
                )

        # Determine loop source
        if count is not None:
            if count > _LOOP_ITEMS_CAP:
                raise TemplateEvaluationError(
                    expression=str(count),
                    cause=ValueError(f"@loop count {count} exceeds the cap of {_LOOP_ITEMS_CAP}"),
                )
            loop_items = list(range(1, count + 1))
        elif items is not None:
            loop_items = self._resolve_loop_items(items, context)
        else:
            raise ValueError("@loop requires either 'count' or 'items' parameter")

        # Process each iteration
        results = []
        for index, item in enumerate(loop_items):
            # Create a copy of context for this iteration
            loop_context = context.copy()

            # Add loop variables
            loop_vars = {
                'index': index + 1,
                'index0': index,
                'first': index == 0,
                'last': index == len(loop_items) - 1,
                'length': len(loop_items)
            }
            loop_context['loop'] = loop_vars

            # Handle item variable
            if items is not None:
                if as_var and isinstance(item, tuple) and len(item) == 2:
                    # Handle key-value unpacking for dicts
                    if ',' in as_var:
                        key_var, val_var = [v.strip() for v in as_var.split(',', 1)]
                        loop_context[key_var] = item[0]
                        loop_context[val_var] = item[1]
                    else:
                        loop_context[as_var] = item
                else:
                    loop_context['item'] = item

            # Check 'when' condition if specified
            if when:
                when_result = self.process_value(when, loop_context)
                if isinstance(when_result, str):
                    # Legacy templated-bool-as-string ("true"/"false"/""); native
                    # exact-expression `when` values are already real bools.
                    when_result = when_result.strip().lower() not in ('false', '')
                if not when_result:
                    continue

            # Process template
            result = self.process_value(template, loop_context)

            # Handle the case where result is a dict with templated keys
            if isinstance(result, dict):
                processed_result = {}
                for key, value in result.items():
                    # Process key if it contains template markers
                    if isinstance(key, str) and ("{{" in key or "{%" in key):
                        processed_key = self.template_processor.process_template(key, loop_context)
                        processed_result[processed_key] = value
                    else:
                        processed_result[key] = value
                result = processed_result

            results.append(result)

        return results

    @staticmethod
    def _path_str(path: Tuple[Any, ...]) -> Optional[str]:
        return ".".join(str(p) for p in path) if path else None

    def process_value(self, value, context, path: Tuple[Any, ...] = ()):
        """
        Recursively process a value that might be a template string, dict, or list.

        Any `TemplateEvaluationError` raised deep inside is enriched with
        `config_path` (the dotted key/index path to the value that failed)
        before propagating - `process()` fills in the remaining location
        fields (preset_id, source_file, mode, form_name, pipe_id) once it
        reaches the top.
        """
        if isinstance(value, str):
            if "{{" not in value and "{%" not in value:
                return value
            try:
                return self.template_processor.process_template(value, context)
            except TemplateEvaluationError as e:
                raise e.with_location(config_path=self._path_str(path)) from e
        elif isinstance(value, dict):
            # @loop is the only surviving directive (@object:/@dict: are gone -
            # native `.` attribute access on form/preset/etc. replaces them).
            if '@loop' in value:
                try:
                    return self._process_loop(value['@loop'], context)
                except TemplateEvaluationError as e:
                    raise e.with_location(config_path=self._path_str(path)) from e
            return {k: self.process_value(v, context, path + (k,)) for k, v in value.items()}
        elif isinstance(value, list):
            return [self.process_value(item, context, path + (i,)) for i, item in enumerate(value)]
        else:
            return value

    @staticmethod
    def _get_configuration_values(preset_id: str) -> Dict[str, Any]:
        """Admin-set configuration values stored for an installed preset.

        Lazily imported (mirrors other core/pipe modules reaching into the
        persistence layer on demand) to avoid a hard import-time dependency
        from the preset template layer onto the database.
        """
        try:
            from src.features.presets.repository import preset_repo
            return preset_repo.get_preset_configuration(preset_id)
        except Exception:
            return {}

    def _build_runtime_settings(self, user_id: Optional[str]) -> Dict[str, Any]:
        """Snapshot the allowlisted settings ONCE per `process()` call.

        No settings-manager method calls happen at render time - `runtime.settings`
        is this pre-resolved dict. Allowlist (spec §2): `file_storage_directory`
        plus `nsfw`, the other setting actually read from a preset (SDXL's
        inpaint/txt2img pipelines gate an NSFW check on it). Both are
        user-scoped and must be resolved with the authenticated user id passed
        into the build; resolving `setting('USER', 'nsfw')` with no user
        silently falls back to the global default instead of the user's own
        setting.
        """
        return {
            'file_storage_directory': self.settings_manager.get_file_storage_directory(user_id),
            'nsfw': self.settings_manager.is_nsfw_enabled(user_id),
        }

    def _resolve_enabled(
        self,
        raw_enabled: Any,
        context: Dict[str, Any],
    ) -> bool:
        """Resolve a pipe's `enabled:` to a real bool.

        Native YAML bool passes through. A string must be an exact
        `{{ expression }}` scalar evaluating to a bool - the old
        `enabled: "true"` string literal and templated
        `{% if %}true{% else %}false{% endif %}` idioms are no longer
        special-cased (spec §2/§4/§6: the runtime `== 'true'` compare this
        replaces silently disabled any pipe using capitalized "True" or a
        non-exact-expression template). Omitted (`None`, the dataclass
        default) means enabled.
        """
        if raw_enabled is None:
            return True
        if isinstance(raw_enabled, bool):
            return raw_enabled
        if isinstance(raw_enabled, str):
            try:
                result = self.template_processor.evaluate_expression(raw_enabled, context)
            except TemplateEvaluationError as e:
                raise e.with_location(config_path='enabled') from e
            if not isinstance(result, bool):
                raise TemplateEvaluationError(
                    expression=raw_enabled,
                    cause=TypeError(
                        f"'enabled' must evaluate to a bool, got {type(result).__name__}: {result!r}"
                    ),
                    config_path='enabled',
                )
            return result
        raise TemplateEvaluationError(
            expression=repr(raw_enabled),
            cause=TypeError(
                f"'enabled' must be a bool or an exact `{{{{ expression }}}}` string, "
                f"got {type(raw_enabled).__name__}"
            ),
            config_path='enabled',
        )

    def process(
        self,
        preset_template: PresetTemplate,
        generation_data: dict,
        user_id: Optional[str] = None,
    ) -> list:
        """
        Process generation data to a pipeline configuration with recursive template processing.

        Args:
            preset_template: The loaded preset.
            generation_data: `{mode, form_data, form_name, prompts, ...}` -
                see `PipelineBuilder.build_pipeline` for the exact shape.
            user_id: The authenticated user, if any - resolves user-scoped
                `runtime.settings` entries (e.g. `nsfw`). `None` for
                unauthenticated/system callers (preview, test suite), which
                resolve those settings against the global default.
        """
        # Get prompts array (new format) or fallback to legacy format
        prompts = generation_data.get('prompts', [])
        if not prompts:
            # Legacy format fallback
            p_prompt = generation_data.get('prompt', '')
            n_prompt = generation_data.get('negative_prompt', '')
            prompts = [{'positive': p_prompt, 'negative': n_prompt}]

        mode = generation_data['mode']
        form = generation_data.get('form_data') or {}

        mode_data = preset_template.modes[mode]

        # Which form "variant" this submission came from (see docs/presets.md
        # "Variants"), resolved to the mode's default when the caller (or an
        # older client) didn't pick one explicitly.
        form_name = generation_data.get('form_name') or default_form_name(mode_data)

        first_pair = prompts[0] if prompts else {'positive': '', 'negative': ''}

        context = {
            # The bound form's values (src/features/forms/binding.py's BoundForm,
            # once W3 lands) - `form.steps` is an int, `form.loras` is a
            # list, etc. Injected runtime documents (video_director, prompt
            # timelines, ...) arrive as ordinary keys of this same dict
            # (the orchestrator merges them into form_data before this ever
            # runs - e.g. `form.video_director`), not a separate context
            # namespace.
            'form': form,
            'request': {
                'mode': mode,
                'form_name': form_name,
            },
            'generation': {
                'prompts': {
                    'first': first_pair,
                    'pairs': prompts,
                    'positives': [p.get('positive', '') for p in prompts],
                    'negatives': [p.get('negative', '') for p in prompts],
                },
                'seed': form.get('seed', -1),
                'quantity': form.get('quantity', 1),
            },
            'preset': {
                'id': preset_template.id,
                'name': preset_template.name,
                'vars': preset_template.vars,
                # Named generation profiles from preset.yml's `speed_profiles:`
                # (roadmap 3.6) - a plain name -> dict mapping, read directly as
                # `preset.speed_profiles.draft.steps` or looked up dynamically
                # via the `get_speed_profile(name)` global (src/platform/templating/
                # processor.py), which raises a clear error naming the preset
                # and the missing profile instead of silently rendering None.
                'speed_profiles': preset_template.speed_profiles or {},
                # Admin-set configuration values (roadmap: preset configuration),
                # e.g. `preset.configuration.checkpoint_tags`. Stored per installed
                # preset (src/features/presets/records.py), not part of the YAML -
                # see docs/presets.md "Configuration (admin-set)". Empty dict for a
                # preset with no `configuration:` block or no admin-set values yet.
                'configuration': self._get_configuration_values(preset_template.id),
            },
            'runtime': {
                'settings': self._build_runtime_settings(user_id),
            },
            'paths': {
                'preset': preset_template.path,
            },
        }

        # Process all pipes configurations recursively
        processed_pipes = []
        pipe_templates = mode_data.pipes
        source_file = str(Path(preset_template.path) / 'modes' / mode / 'pipeline.yml')

        for pipe in pipe_templates:
            try:
                inpts = []
                for inpt in pipe.input if pipe.input else []:
                    inpt = {
                        "name": self.process_value(inpt[0], context),
                        "provider": self.process_value(inpt[1], context),  # This is the provider_pipe name
                        "output_var": self.process_value(inpt[2] if len(inpt) > 2 else None, context),  # This is the provider_output_var
                        # All inputs are enabled by default - this is a
                        # different flag than the pipe-level `enabled:`
                        # (spec §4/§6 only redefines that one).
                        "enabled": True,
                    }
                    inpts.append(inpt)

                cache = []
                if pipe.cache:
                    for c in pipe.cache:
                        cache.append(self.process_value(c, context))

                # `enabled` resolves FIRST: a disabled pipe's config is never
                # rendered. Under StrictUndefined, rendering configs of pipes
                # that will not run turns optional features (e.g. ControlNet
                # slots referenced only when enable_controlnet is on) into
                # spurious build failures.
                enabled = self._resolve_enabled(pipe.enabled, context)
                processed_pipe = {
                    'id': pipe.id,
                    'name': self.process_value(pipe.name, context, path=('name',)),
                    'enabled': enabled,
                    'config': (
                        self.process_value(pipe.configuration, context, path=('config',))
                        if enabled and pipe.configuration else {}
                    ),
                    'input': inpts,
                    'cache': cache,
                }
            except TemplateEvaluationError as e:
                raise e.with_location(
                    preset_id=preset_template.id,
                    source_file=source_file,
                    mode=mode,
                    form_name=form_name,
                    pipe_id=pipe.id,
                ) from e

            processed_pipes.append(processed_pipe)

        return processed_pipes
