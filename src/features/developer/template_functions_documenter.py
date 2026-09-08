"""Template functions / context documentation generator.

Documents the ACTUAL surface a preset's ``pipeline.yml`` (and templated form
values) may use after the templating rework (see docs/presets.md and
src/platform/templating/processor.py):

- the one allowlisted global function, ``get_speed_profile``;
- the custom filters (``active_loras``, ``strip_model_dir``) plus Jinja's
  builtin ``default`` (the ONLY suppression of a missing value under
  StrictUndefined);
- the template context roots (``form``, ``request``, ``generation``,
  ``preset``, ``runtime``, ``paths``) that native attribute access reads from.

The deleted render globals (``get_form``, ``value``/``get``,
``contains``/``get_is_in``, ``dict``/``@dict:``, ``@object:``,
``setting``/``config``, ``path``/``get_path_for``, ``icon``/``get_icon``),
the deleted filters (``matches``/``regex_search``), the whole ``input.*``
context, and ``preset.speed_profiles``/``preset.configuration``/
``generation.seed``/``generation.quantity`` direct access (a live audit found
zero real consumers of any of them across every shipped preset -
``get_speed_profile()`` and ``generation.profile`` replace the first,
``@config:<key>`` field indirection the second, the ``seed_generator`` pipe's
own ``seed``/``quantity`` config the last two) are intentionally absent -
they are build errors now, not documented syntax.

Consumed by ``DeveloperController.get_template_functions_documentation`` (served
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
            "Speed Profiles": [
                {
                    "name": "get_speed_profile",
                    "alias": None,
                    "signature": "get_speed_profile(profile_name: str, default: Any = <required>) -> dict",
                    "description": (
                        "Look up a named entry from preset.yml's `speed_profiles:` block "
                        "(draft/standard/max, ...) and return its dict of generation-knob "
                        "overrides. Omitting `default` makes a missing profile a build error "
                        "that names both the preset and the profile. Use this for an EXPLICIT "
                        "profile name; `generation.profile` (Template Context, below) is the "
                        "already-resolved profile for whichever name the current request selected."
                    ),
                    "parameters": [
                        {"name": "profile_name", "type": "str", "description": "Profile name to look up, e.g. 'draft'"},
                        {"name": "default", "type": "Any", "default": "<required>", "description": "Returned if the profile is absent; omit to raise on a missing profile"},
                    ],
                    "return_type": "dict",
                    "examples": [
                        {"code": "{{ get_speed_profile('draft')['steps'] }}", "result": "6 (from speed_profiles.draft.steps)"},
                        {"code": "{{ generation.profile.steps }}", "result": "6 (the profile resolved for this request)"},
                    ],
                }
            ],
            "Filters": [
                {
                    "name": "active_loras",
                    "alias": None,
                    "signature": "value | active_loras -> list",
                    "description": (
                        "Drop the lora_picker list's zero-strength entries. A missing strength "
                        "(lora_picker's own strength_default applies), a negative strength "
                        "(inverted LoRA) and a non-numeric strength all stay - only an exact-zero "
                        "strength is dropped."
                    ),
                    "parameters": [
                        {"name": "value", "type": "list", "description": "A lora_picker field's value (pipe input)"},
                    ],
                    "return_type": "list",
                    "examples": [
                        {"code": "{{ form.loras | default([]) | active_loras }}", "result": "The list with zero-strength entries removed"},
                    ],
                },
                {
                    "name": "strip_model_dir",
                    "alias": None,
                    "signature": "value | strip_model_dir -> str",
                    "description": (
                        "Strip a model picker value down to its path relative to the depot type "
                        "directory (`models/<checkpoints|loras|vae|...>/`), keeping any "
                        "subdirectories underneath. A bare filename or an already backend-native "
                        "ref (no such prefix) passes through unchanged; None/'' map to ''. "
                        "Replaces the old `replace('models/loras/', '')` idiom in ComfyUI preset "
                        "templates - see docs/models.md."
                    ),
                    "parameters": [
                        {"name": "value", "type": "str", "description": "A model/lora_picker field's value (pipe input)"},
                    ],
                    "return_type": "str",
                    "examples": [
                        {"code": "{{ form.vae | strip_model_dir }}", "result": "'x.safetensors' for 'models/vae/x.safetensors'"},
                        {"code": "{{ item.model | strip_model_dir }}", "result": "'style/x.safetensors' for 'models/loras/style/x.safetensors'"},
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
                    "signature": "generation.{prompts:{first,pairs,positives,negatives}, profile}",
                    "description": (
                        "Generation-level data resolved before the pipeline builds: expanded "
                        "prompt pairs (one per image), and `profile` - the `speed_profiles:` entry "
                        "resolved for this request (the one `form.speed_profile` names; the first "
                        "declared profile if the form has no matching value; `{}` if the preset "
                        "declares none, so `generation.profile.steps` fails loudly like any other "
                        "missing key). `prompts.first` is the first pair; `prompts.pairs` the full "
                        "list; `prompts.positives`/`negatives` the flattened sides. There is no "
                        "`generation.seed`/`generation.quantity` - read `form.seed`/`form.quantity` "
                        "(the seed_generator pipe's own config is the only real consumer)."
                    ),
                    "parameters": [
                        {"name": "prompts.first", "type": "dict", "description": "First expanded prompt pair {positive, negative}"},
                        {"name": "prompts.pairs", "type": "list", "description": "All per-image expanded prompt pairs"},
                        {"name": "profile", "type": "dict", "description": "The speed_profiles: entry resolved for this request ({} if the preset declares none)"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ generation.prompts.first.positive }}", "result": "First image's positive prompt"},
                        {"code": "{{ generation.prompts.pairs }}", "result": "List of {positive, negative} pairs"},
                        {"code": "{{ form.steps | default(generation.profile.steps) }}", "result": "form.steps if set, else the resolved profile's steps"},
                    ],
                },
                {
                    "name": "preset",
                    "alias": None,
                    "signature": "preset.{id, name, vars}",
                    "description": (
                        "The preset manifest: its id/name and the `vars:` bag (preset-wide "
                        "constants). There is no `preset.speed_profiles`/`preset.configuration` - "
                        "use `get_speed_profile()`/`generation.profile` for speed profiles, and "
                        "`@config:<key>` field indirection for admin-set configuration (see "
                        "[Configuration (admin-set)](presets.md#configuration-admin-set))."
                    ),
                    "parameters": [
                        {"name": "id", "type": "str", "description": "Preset id"},
                        {"name": "name", "type": "str", "description": "Preset display name"},
                        {"name": "vars", "type": "dict", "description": "Preset-wide constants declared under `vars:`"},
                    ],
                    "return_type": "native value",
                    "examples": [
                        {"code": "{{ preset.vars.default_cfg }}", "result": "The preset's default CFG constant"},
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
