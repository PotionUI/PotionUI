"""
Developer documentation operations: preset lint and docs lint.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. `get_presets_lint` takes the
`preset_loader` it needs; `get_docs_lint` needs none. `DeveloperController`
(`routes.py`) holds the collaborators and passes them in.

The template-functions/pipes documentation reads
(`TemplateFunctionsDocumenter`/`PipesDocumenter`) have no logic beyond
delegating to the documenter - `DeveloperController` and `DocsController`
call those directly, the way a controller reads a repository.
"""
from typing import Any, Dict

from src.features.presets.linter import PresetLinter


def get_presets_lint(preset_loader) -> Dict[str, Any]:
    """Get preset schema validation errors plus a full lint run.

    Returns:
        Dict with 'load_errors' (per-preset.yml validation errors from the
        loader that runs at startup) and 'lint_issues' (a fresh, deeper lint
        pass including cross-file checks like orphaned mode dirs).
    """
    preset_loader._ensure_loaded()
    plugin_manifests = (
        preset_loader.plugin_registry.get_enabled_plugins()
        if preset_loader.plugin_registry is not None
        else []
    )
    linter = PresetLinter(
        [str(p) for p in preset_loader.all_preset_roots()],
        plugin_manifests=plugin_manifests,
    )
    issues = linter.lint()

    return {
        "load_errors": dict(preset_loader.load_errors),
        "lint_issues": [
            {"level": issue.level, "preset_path": issue.preset_path, "message": issue.message}
            for issue in issues
        ],
        "total_errors": sum(1 for i in issues if i.level == "error")
        + sum(len(v) for v in preset_loader.load_errors.values()),
        "total_warnings": sum(1 for i in issues if i.level == "warning"),
    }


def get_docs_lint() -> Dict[str, Any]:
    """Lint the typed documentation (Docs 2.0).

    Returns a dict with ``issues`` (each ``{level, path, message}``) plus
    ``total_errors`` / ``total_warnings`` — mirroring get_presets_lint's shape.
    """
    from src.features.docs.lint import lint_docs

    return lint_docs("docs").to_dict()
