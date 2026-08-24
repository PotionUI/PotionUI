"""
Import/export for the phrasebook system.

`PhrasebookImporter` is the contract an importer must satisfy: given raw
content plus the import args, return the same statistics dict the API emits.
`PhrasebookImporterRegistry` maps a format id to its importer, mirroring
the `FieldTypeRegistry`/`OutputTypeRegistry` idiom - registering a second
format (e.g. from a plugin) is adding a class and a registry entry, not
editing `PhrasebookImportService`.
"""
import logging
from abc import ABC, abstractmethod
import yaml
from typing import Dict, List, Any, Optional, Tuple
from src.platform.util.ids import generate_ulid
from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookValue
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
    phrasebook_category_repo,
    phrasebook_value_repo
)
from src.features.phrasebook.hooks import PHRASEBOOK_HOOKS

logger = logging.getLogger(__name__)


class DuplicatePhrasebookImporterError(ValueError):
    """Raised when registering a format id that is already registered."""


class PhrasebookImporter(ABC):
    """Contract for importing serialized phrasebook data.

    `format_id` is the registry key (e.g. "yaml"). `import_data` receives the
    raw content plus the import args and returns the statistics dict the API
    responds with: `{success, categories_created, values_created, error?}`.
    """

    format_id: str = ""

    @abstractmethod
    def import_data(
        self, content: str, user_id: str, root_category: Optional[str] = None
    ) -> Dict[str, Any]:
        ...


class YamlPhrasebookImporter(PhrasebookImporter):
    """Imports YAML content, supporting both the nested dictionary format
    and the label/value list format.
    """

    format_id = "yaml"

    def __init__(
        self,
        category_repository: PhrasebookCategoryRepository = phrasebook_category_repo,
        value_repository: PhrasebookValueRepository = phrasebook_value_repo,
    ):
        self.category_repo = category_repository
        self.value_repo = value_repository

    def _get_plugin_registry(self):
        """Get the plugin registry lazily to avoid import cycles."""
        from src.platform.plugins.runtime_registries import get_global_plugin_registry
        return get_global_plugin_registry()

    def _execute_hook(self, hook: str, data: dict) -> Tuple[dict, bool]:
        """
        Execute a hook and return the context data and whether it was blocked.

        Args:
            hook: The hook definition to execute
            data: Context data for the hook

        Returns:
            Tuple of (context_data, blocked)
        """
        plugins = self._get_plugin_registry()
        context, results = plugins.execute_hook(
            hook,
            initial_data=data
        )

        # Check if any plugin blocked the operation
        blocked = context.data.get("blocked", False)

        return context.data, blocked

    def import_data(
        self, content: str, user_id: str, root_category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Import YAML content into the phrasebook system

        Executes hooks:
        - phrasebook.before_import: Can modify/validate import data or block
        - phrasebook.after_import: Notification of successful import

        Args:
            content: YAML string content
            user_id: User ID for ownership
            root_category: Optional root category name (if not specified in YAML)

        Returns:
            Dictionary with import statistics
        """
        yaml_content = content
        try:
            # Execute before_import hook
            hook_data, blocked = self._execute_hook(
                PHRASEBOOK_HOOKS.before_import,
                {
                    "yaml_content": yaml_content,
                    "user_id": user_id,
                    "root_category": root_category
                }
            )

            if blocked:
                reason = hook_data.get("block_reason", "Import blocked by plugin")
                logger.warning(f"Phrasebook import blocked by plugin: {reason}")
                return {
                    'success': False,
                    'error': reason,
                    'categories_created': 0,
                    'values_created': 0
                }

            # Allow hooks to modify data
            yaml_content = hook_data.get("yaml_content", yaml_content)
            root_category = hook_data.get("root_category", root_category)

            data = yaml.safe_load(yaml_content)

            if data is None:
                return {
                    'success': False,
                    'error': 'Empty YAML file',
                    'categories_created': 0,
                    'values_created': 0
                }

            # Determine the format and process accordingly
            if isinstance(data, dict):
                # Nested dictionary format (like clothes.yaml)
                result = self._import_nested_dict(data, user_id, root_category)
            elif isinstance(data, list):
                # List of label/value pairs (like emotions.yml)
                result = self._import_label_value_list(data, user_id, root_category)
            else:
                return {
                    'success': False,
                    'error': 'Unsupported YAML format. Expected dict or list.',
                    'categories_created': 0,
                    'values_created': 0
                }

            # Execute after_import hook on success
            if result.get('success'):
                self._execute_hook(
                    PHRASEBOOK_HOOKS.after_import,
                    {
                        "user_id": user_id,
                        "root_category": root_category,
                        "categories_created": result.get('categories_created', 0),
                        "values_created": result.get('values_created', 0)
                    }
                )
                logger.info(
                    f"Phrasebook import completed: {result.get('categories_created', 0)} categories, "
                    f"{result.get('values_created', 0)} values"
                )

            return result

        except yaml.YAMLError as e:
            return {
                'success': False,
                'error': f'Invalid YAML format: {str(e)}',
                'categories_created': 0,
                'values_created': 0
            }
        except Exception as e:
            logger.error(f"Phrasebook import failed: {e}")
            return {
                'success': False,
                'error': f'Import failed: {str(e)}',
                'categories_created': 0,
                'values_created': 0
            }

    def _import_nested_dict(self, data: Dict, user_id: str, root_category: Optional[str] = None) -> Dict[str, Any]:
        """Import nested dictionary format"""
        categories_created = 0
        values_created = 0

        # If root_category is specified, wrap the data
        if root_category:
            data = {root_category: data}

        # Process the nested structure
        for key, value in data.items():
            cat_count, val_count = self._process_nested_structure(
                key, value, user_id, parent_id=None, parent_path=""
            )
            categories_created += cat_count
            values_created += val_count

        return {
            'success': True,
            'categories_created': categories_created,
            'values_created': values_created
        }

    def _import_label_value_list(self, data: List, user_id: str, root_category: Optional[str] = None) -> Dict[str, Any]:
        """Import list of label/value pairs format"""
        if not root_category:
            root_category = 'imported'

        # Create or get the root category
        category = self._ensure_category(root_category, root_category, user_id, None)
        if not category:
            return {
                'success': False,
                'error': 'Failed to create root category',
                'categories_created': 0,
                'values_created': 0
            }

        # Create values for the category
        values = []
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                # Label/value format
                label = item.get('label', '')
                value = item.get('value', '')
                if not label:
                    label = value  # Use value as label if label is missing
            elif isinstance(item, str):
                # Simple string format - use as both label and value
                label = item
                value = item
            else:
                continue  # Skip invalid items

            if label and value:
                values.append(PhrasebookValue(
                    id=generate_ulid(),
                    category_id=category.id,
                    label=label,
                    value=value,
                    sort_order=idx,
                    user_id=user_id
                ))

        # Bulk create values
        values_created = self.value_repo.create_bulk(values)

        return {
            'success': True,
            'categories_created': 1,
            'values_created': values_created
        }

    def _process_nested_structure(
        self,
        name: str,
        data: Any,
        user_id: str,
        parent_id: Optional[str],
        parent_path: str
    ) -> Tuple[int, int]:
        """
        Recursively process nested structure

        Returns:
            Tuple of (categories_created, values_created)
        """
        categories_created = 0
        values_created = 0

        # Build the current path
        current_path = f"{parent_path}.{name}" if parent_path else name

        # Create or get the current category
        category = self._ensure_category(name, current_path, user_id, parent_id)
        if not category:
            return 0, 0

        categories_created += 1

        if isinstance(data, dict):
            # Process nested categories
            for key, value in data.items():
                cat_count, val_count = self._process_nested_structure(
                    key, value, user_id, category.id, current_path
                )
                categories_created += cat_count
                values_created += val_count

        elif isinstance(data, list):
            # Process values for this category
            values = []
            for idx, item in enumerate(data):
                if isinstance(item, dict):
                    # Label/value format
                    label = item.get('label', '')
                    value = item.get('value', '')
                    if not label:
                        label = value
                elif isinstance(item, str):
                    # Simple string format
                    label = item
                    value = item
                else:
                    continue

                if label and value:
                    values.append(PhrasebookValue(
                        id=generate_ulid(),
                        category_id=category.id,
                        label=label,
                        value=value,
                        sort_order=idx,
                        user_id=user_id
                    ))

            if values:
                values_created += self.value_repo.create_bulk(values)

        return categories_created, values_created

    def _ensure_category(
        self,
        name: str,
        path: str,
        user_id: str,
        parent_id: Optional[str]
    ) -> Optional[PhrasebookCategory]:
        """Ensure a category exists, create if it doesn't"""
        # Check if category already exists
        existing = self.category_repo.get_by_path(path, user_id)
        if existing:
            return existing

        # Create new category
        category = PhrasebookCategory(
            id=generate_ulid(),
            name=name,
            path=path,
            parent_id=parent_id,
            user_id=user_id,
            description=f"Auto-imported category: {name}"
        )

        success = self.category_repo.create(category)
        return category if success else None


class PhrasebookImporterRegistry:
    """Registry mapping a format id to its `PhrasebookImporter`."""

    def __init__(self):
        self._by_format: Dict[str, PhrasebookImporter] = {}

    def register(self, importer: PhrasebookImporter) -> None:
        """Register an importer. Raises `DuplicatePhrasebookImporterError` on collision."""
        if importer.format_id in self._by_format:
            raise DuplicatePhrasebookImporterError(
                f"Phrasebook importer already registered for format: '{importer.format_id}'"
            )
        self._by_format[importer.format_id] = importer

    def get(self, format_id: str) -> Optional[PhrasebookImporter]:
        """Look up the importer for a format id, or None if unregistered."""
        return self._by_format.get(format_id)

    def all(self) -> List[PhrasebookImporter]:
        """Return every registered importer."""
        return list(self._by_format.values())


# Module-level singleton; plugins register additional formats onto it.
phrasebook_importer_registry = PhrasebookImporterRegistry()
phrasebook_importer_registry.register(YamlPhrasebookImporter())


class PhrasebookImportService:
    """Import/export entry point used by the phrasebook routes.

    Dispatches import to the registered `PhrasebookImporter` for
    `format_id`; export stays here since it isn't part of the importer
    contract.
    """

    def __init__(
        self,
        importer_registry: PhrasebookImporterRegistry = phrasebook_importer_registry,
        format_id: str = "yaml",
        category_repository: PhrasebookCategoryRepository = phrasebook_category_repo,
        value_repository: PhrasebookValueRepository = phrasebook_value_repo,
    ):
        self.importer_registry = importer_registry
        self.format_id = format_id
        self.category_repo = category_repository
        self.value_repo = value_repository

    def import_yaml(self, yaml_content: str, user_id: str, root_category: Optional[str] = None) -> Dict[str, Any]:
        """Import content using the registered importer for `self.format_id`."""
        importer = self.importer_registry.get(self.format_id)
        if importer is None:
            return {
                'success': False,
                'error': f"No importer registered for format '{self.format_id}'",
                'categories_created': 0,
                'values_created': 0
            }
        return importer.import_data(yaml_content, user_id, root_category)

    def export_to_yaml(self, category_id: str, user_id: str) -> Optional[str]:
        """
        Export a category and its values to YAML format

        Args:
            category_id: Category ID to export
            user_id: User ID for ownership check

        Returns:
            YAML string or None if failed
        """
        category = self.category_repo.get_by_id(category_id, user_id)
        if not category:
            return None

        # Get all child categories
        children = self.category_repo.get_children(category_id, user_id)

        if children:
            # Export as nested structure
            result = self._export_nested_structure(category, user_id)
        else:
            # Export as simple list
            values = self.value_repo.get_by_category(category_id, user_id)
            if not values:
                return "[]"

            result = []
            for value in values:
                if value.label == value.value:
                    # Simple format
                    result.append(value.value)
                else:
                    # Label/value format
                    result.append({
                        'label': value.label,
                        'value': value.value
                    })

        return yaml.dump(result, default_flow_style=False, allow_unicode=True)

    def _export_nested_structure(self, category: PhrasebookCategory, user_id: str) -> Dict:
        """Export category and children as nested structure"""
        result = {}

        # Get direct values
        values = self.value_repo.get_by_category(category.id, user_id)
        if values:
            value_list = []
            for value in values:
                if value.label == value.value:
                    value_list.append(value.value)
                else:
                    value_list.append({
                        'label': value.label,
                        'value': value.value
                    })
            if value_list:
                result = value_list

        # Get child categories
        children = self.category_repo.get_children(category.id, user_id)
        for child in children:
            child_data = self._export_nested_structure(child, user_id)
            if isinstance(result, list):
                # Convert to dict if we have both values and children
                result = {category.name: result}
            result[child.name] = child_data

        return result


# Global service instance
phrasebook_import_service = PhrasebookImportService()
