"""Developer documentation manager."""
from typing import Dict, Any

from src.pipelines.catalog import PipeCatalog
from src.features.presets import PresetTemplateLoader
from src.features.presets.linter import PresetLinter
from src.platform.templating import TemplateProcessor
from src.features.fields.field_factory import FieldFactory

from .pipes_documenter import PipesDocumenter
from .fields_documenter import FieldsDocumenter
from .io_types_documenter import IoTypesDocumenter
from .template_functions_documenter import TemplateFunctionsDocumenter


class DeveloperManager:
    """
    Manager for developer documentation.

    Coordinates documentation generation for pipes, fields, IO types,
    and template functions.
    """

    def __init__(
        self,
        pipe_catalog: PipeCatalog,
        preset_loader: PresetTemplateLoader,
        template_processor: TemplateProcessor
    ):
        """Initialize the developer manager.

        Args:
            pipe_catalog: Registry for pipeline components
            preset_loader: Loader for preset templates
            template_processor: Processor for templates
        """
        self.pipe_catalog = pipe_catalog
        self.preset_loader = preset_loader
        self.template_processor = template_processor

        # Create field factory for fields documentation
        self.field_factory = FieldFactory(preset_loader, template_processor)

        # Initialize documenters
        self._pipes_documenter = PipesDocumenter(pipe_catalog)
        self._fields_documenter = FieldsDocumenter(self.field_factory)
        self._io_types_documenter = IoTypesDocumenter()
        self._template_functions_documenter = TemplateFunctionsDocumenter()

    def get_pipes_documentation(self) -> Dict[str, Any]:
        """Get documentation for all available pipes.

        Returns:
            Dict with 'pipes' list and 'total' count
        """
        return self._pipes_documenter.generate_documentation()

    def get_fields_documentation(self) -> Dict[str, Any]:
        """Get documentation for all available field types.

        Returns:
            Dict with 'fields' list and 'total' count
        """
        return self._fields_documenter.generate_documentation()

    def get_io_types(self) -> Dict[str, Any]:
        """Get all available IOType enums used in pipes.

        Returns:
            Dict with 'io_types' list and 'total' count
        """
        return self._io_types_documenter.generate_documentation()

    def get_template_functions_documentation(self) -> Dict[str, Any]:
        """Get documentation for all template functions.

        Returns:
            Dict with 'functions' list, 'total' count, and 'categories' list
        """
        return self._template_functions_documenter.generate_documentation()

    def get_presets_lint(self) -> Dict[str, Any]:
        """Get preset schema validation errors plus a full lint run.

        Returns:
            Dict with 'load_errors' (per-preset.yml validation errors from the
            loader that runs at startup) and 'lint_issues' (a fresh, deeper lint
            pass including cross-file checks like orphaned mode dirs).
        """
        self.preset_loader._ensure_loaded()
        plugin_manifests = (
            self.preset_loader.plugin_registry.get_enabled_plugins()
            if self.preset_loader.plugin_registry is not None
            else []
        )
        linter = PresetLinter(
            [str(p) for p in self.preset_loader.all_preset_roots()],
            plugin_manifests=plugin_manifests,
        )
        issues = linter.lint()

        return {
            "load_errors": dict(self.preset_loader.load_errors),
            "lint_issues": [
                {"level": issue.level, "preset_path": issue.preset_path, "message": issue.message}
                for issue in issues
            ],
            "total_errors": sum(1 for i in issues if i.level == "error")
            + sum(len(v) for v in self.preset_loader.load_errors.values()),
            "total_warnings": sum(1 for i in issues if i.level == "warning"),
        }

    def get_docs_lint(self) -> Dict[str, Any]:
        """Lint the typed documentation (Docs 2.0).

        Returns a dict with ``issues`` (each ``{level, path, message}``) plus
        ``total_errors`` / ``total_warnings`` — mirroring get_presets_lint's shape.
        """
        from src.features.docs.lint import lint_docs

        return lint_docs("docs").to_dict()
