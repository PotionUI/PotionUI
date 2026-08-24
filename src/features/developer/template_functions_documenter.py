"""Template functions / context documentation generator.

Documents the ACTUAL surface a preset's ``pipeline.yml`` (and templated form
values) may use after the templating rework (see docs/presets.md and
src/platform/templating/processor.py):

- the three allowlisted global functions (``path``/``get_path_for``,
  ``icon``/``get_icon``, ``get_speed_profile``);
- the two custom filters (``matches``/``regex_search``) plus Jinja's builtin
  ``default`` (the ONLY suppression of a missing value under StrictUndefined);
- the template context roots (``form``, ``request``, ``generation``,
  ``preset``, ``runtime``, ``paths``) that native attribute access reads from.

The deleted render globals (``get_form``, ``value``/``get``,
``contains``/``get_is_in``, ``dict``/``@dict:``, ``@object:``,
``setting``/``config``) and the whole ``input.*`` context are intentionally
absent - they are build errors now, not documented syntax.

Consumed by ``DeveloperManager.get_template_functions_documentation`` (served
at the developer docs endpoint); the ``{functions, total, categories}`` /
per-entry ``{name, alias, signature, description, parameters, return_type,
examples, category}`` shape is unchanged so the frontend renderer is unaffected.
"""
from typing import List, Dict, Any


class TemplateFunctionsDocumenter:
    """Generates documentation for the Jinja2 globals, filters, and context
    variables available in ``pipeline.yml`` and templated form values."""

    def _get_function_categories(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return the documentation entries organized by category."""
        return {
            "Path Helpers": [
                {
                    "name": "path",
                    "alias": "get_path_for",
                    "signature": "path(path_type: str, file_name: str = None) -> str",
                    "description": (
                        "Resolve a filesystem path by resource type (checkpoint, lora, "
                        "embedding, upscaler, detector, wildcard, diffusion_model, "
                        "controlnet, std, ...). With no file_name, returns the resource "
                        "directory; with one, the full path to that file. Plugins can "
                        "register additional path types via the resolve_path hook."
                    ),
                    "parameters": [
                        {"name": "path_type", "type": "str", "description": "Resource type, e.g. 'checkpoint', 'lora', 'embedding'"},
                        {"name": "file_name", "type": "str", "default": "None", "description": "Resource name/identifier (optional)"},
                    ],
                    "return_type": "str",
                    "examples": [
                        {"code": "{{ path('checkpoint') }}", "result": "models/checkpoints"},
                        {"code": "{{ path('lora', 'detail_enhancer.safetensors') }}", "result": "models/loras/detail_enhancer.safetensors"},
                        {"code": "{{ path('wildcard') }}", "result": "models/wildcards"},
                    ],
                }
            ],
            "Icon Mapping": [
                {
                    "name": "icon",
                    "alias": "get_icon",
                    "signature": "icon(icon_type: str) -> str",
                    "description": (
                        "Map a semantic icon type to the frontend icon identifier "
                        "(e.g. 'prompt', 'lora', 'controlnet', 'advanced', 'generation'). "
                        "Used in form tab/section configuration."
                    ),
                    "parameters": [
                        {"name": "icon_type", "type": "str", "description": "Semantic icon type, e.g. 'prompt', 'lora', 'advanced'"},
                    ],
                    "return_type": "str",
                    "examples": [
                        {"code": "{{ icon('prompt') }}", "result": "'pencil-square'"},
                        {"code": "{{ icon('lora') }}", "result": "'puzzle-piece'"},
                        {"code": "{{ icon('generation') }}", "result": "'bolt'"},
                    ],
                }
            ],
            "Speed Profiles": [
                {
                    "name": "get_speed_profile",
                    "alias": None,
                    "signature": "get_speed_profile(profile_name: str, default: Any = <required>) -> dict",
                    "description": (
                        "Look up a named entry from preset.yml's `speed_profiles:` block "
                        "(draft/standard/max, ...) and return its dict of generation-knob "
                        "overrides. Omitting `default` makes a missing profile a build error "
                        "that names both the preset and the profile. `preset.speed_profiles.<name>` "
                        "direct dot access is equivalent."
                    ),
                    "parameters": [
                        {"name": "profile_name", "type": "str", "description": "Profile name to look up, e.g. 'draft'"},
                        {"name": "default", "type": "Any", "default": "<required>", "description": "Returned if the profile is absent; omit to raise on a missing profile"},
                    ],
                    "return_type": "dict",
                    "examples": [
                        {"code": "{{ get_speed_profile('draft')['steps'] }}", "result": "6 (from speed_profiles.draft.steps)"},
                        {"code": "{{ preset.speed_profiles.draft.steps }}", "result": "6 (equivalent direct access)"},
                    ],
                }
            ],
            "Filters": [
                {
                    "name": "matches",
                    "alias": "regex_search",
                    "signature": "value | matches(pattern: str) -> bool",
                    "description": "Regex-search filter: True if `pattern` matches anywhere in the piped string value.",
                    "parameters": [
                        {"name": "value", "type": "str", "description": "String to search (pipe input)"},
                        {"name": "pattern", "type": "str", "description": "Regex pattern"},
                    ],
                    "return_type": "bool",
                    "examples": [
                        {"code": "{% if form.model | matches('flux') %}...{% endif %}", "result": "True if the selected model name contains 'flux'"},
                        {"code": "{% if preset.name | matches('^SDXL') %}...{% endif %}", "result": "True if the preset name starts with 'SDXL'"},
                    ],
                },
                {
                    "name": "default",
                    "alias": None,
                    "signature": "value | default(fallback: Any, boolean: bool = False) -> Any",
                    "description": (
                        "Jinja's builtin default filter - the ONLY way to tolerate a missing "
                        "value. The environment uses StrictUndefined, so any reference to a "
                        "field/key that wasn't provided RAISES a build error unless it is "
                        "guarded by `| default(...)`. Use it on every optional form field."
                    ),
                    "parameters": [
                        {"name": "fallback", "type": "Any", "description": "Value used when the input is undefined"},
                        {"name": "boolean", "type": "bool", "default": "False", "description": "If True, also substitute for falsy (empty) values, not just undefined"},
                    ],
                    "return_type": "Any",
                    "examples": [
                        {"code": "{{ form.steps | default(30) }}", "result": "form.steps if provided, else 30"},
                        {"code": "{{ form.cfg | default(preset.vars.default_cfg) }}", "result": "form.cfg or the preset's default_cfg var"},
                    ],
                },
            ],
            "Template Context": [
                {
                    "name": "form",
                    "alias": None,
                    "signature": "form.<field_name> -> native value",
                    "description": (
                        "The bound form's values (see bind_form / docs/presets.md). Each field "
                        "resolves to its native, typed value - form.steps is an int, form.enabled "
                        "a bool, form.loras a list, form.resolution a str. A reference to a field "
                        "the form didn't provide raises unless guarded by `| default(...)`."
                    ),
                    "parameters": [
                        {"name": "<field_name>", "type": "native", "description": "Any field declared in the mode's form tree (int/float/bool/str/list/dict)"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ form.steps | default(30) }}", "result": "Number of steps (int)"},
                        {"code": "{{ form.loras | default([]) }}", "result": "List of selected LoRAs"},
                        {"code": "{% if form.enable_controlnet | default(false) %}...{% endif %}", "result": "Bool gate"},
                    ],
                },
                {
                    "name": "request",
                    "alias": None,
                    "signature": "request.{mode, form_name}",
                    "description": "The active request: which mode and which form variant is being generated.",
                    "parameters": [
                        {"name": "mode", "type": "str", "description": "The mode being generated, e.g. 'txt2img'"},
                        {"name": "form_name", "type": "str", "description": "The selected form variant's name"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ request.mode }}", "result": "'txt2img'"},
                        {"code": "{{ request.form_name }}", "result": "'custom'"},
                    ],
                },
                {
                    "name": "generation",
                    "alias": None,
                    "signature": "generation.{prompts:{first,pairs,positives,negatives}, seed, quantity}",
                    "description": (
                        "Generation-level data resolved before the pipeline builds: expanded "
                        "prompt pairs (one per image), the resolved seed (never -1 here), and the "
                        "image quantity. `prompts.first` is the first pair; `prompts.pairs` the full "
                        "list; `prompts.positives`/`negatives` the flattened sides."
                    ),
                    "parameters": [
                        {"name": "prompts.first", "type": "dict", "description": "First expanded prompt pair {positive, negative}"},
                        {"name": "prompts.pairs", "type": "list", "description": "All per-image expanded prompt pairs"},
                        {"name": "seed", "type": "int", "description": "Resolved base seed"},
                        {"name": "quantity", "type": "int", "description": "Number of images requested"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ generation.prompts.first.positive }}", "result": "First image's positive prompt"},
                        {"code": "{{ generation.prompts.pairs }}", "result": "List of {positive, negative} pairs"},
                    ],
                },
                {
                    "name": "preset",
                    "alias": None,
                    "signature": "preset.{id, name, vars, speed_profiles, configuration}",
                    "description": (
                        "The preset manifest: its id/name, the `vars:` bag (preset-wide constants), "
                        "the `speed_profiles:` block, and admin-set `configuration:` values."
                    ),
                    "parameters": [
                        {"name": "id", "type": "str", "description": "Preset id"},
                        {"name": "name", "type": "str", "description": "Preset display name"},
                        {"name": "vars", "type": "dict", "description": "Preset-wide constants declared under `vars:`"},
                        {"name": "speed_profiles", "type": "dict", "description": "Named speed-profile overrides"},
                        {"name": "configuration", "type": "dict", "description": "Admin-set configuration values"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ preset.vars.default_cfg }}", "result": "The preset's default CFG constant"},
                        {"code": "{{ preset.speed_profiles.draft.steps }}", "result": "Steps for the 'draft' profile"},
                    ],
                },
                {
                    "name": "runtime",
                    "alias": None,
                    "signature": "runtime.settings.<allowlisted_key>",
                    "description": (
                        "A pre-resolved snapshot of allowlisted settings (resolved once per build "
                        "with the authenticated user), NOT a live settings-manager call. The main "
                        "allowlisted key is file_storage_directory."
                    ),
                    "parameters": [
                        {"name": "settings", "type": "dict", "description": "Snapshot of allowlisted settings, e.g. file_storage_directory"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ runtime.settings.file_storage_directory }}", "result": "The configured storage directory"},
                    ],
                },
                {
                    "name": "paths",
                    "alias": None,
                    "signature": "paths.preset",
                    "description": "Filesystem anchors known at build time. `paths.preset` is the preset's root directory (used by external `children:` tab references).",
                    "parameters": [
                        {"name": "preset", "type": "str", "description": "Absolute path to the preset's root directory"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": '"{{ paths.preset }}/modes/txt2img/tabs/advanced.yml"', "result": "Resolved tab-fragment path"},
                    ],
                },
            ],
        }

    def generate_documentation(self) -> Dict[str, Any]:
        """Generate documentation for all template functions and context roots.

        Returns:
            Dict with 'functions' list, 'total' count, and 'categories' list.
        """
        function_categories = self._get_function_categories()
        functions_docs = []

        for category, functions in function_categories.items():
            for func in functions:
                func_doc = func.copy()
                func_doc['category'] = category
                functions_docs.append(func_doc)

        return {
            'functions': functions_docs,
            'total': len(functions_docs),
            'categories': list(function_categories.keys())
        }
