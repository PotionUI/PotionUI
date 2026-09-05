"""
Manifest/registry-derived reads for the frontend: quick actions, sidebar
widgets, renderer/extension-slot contributions, and the hooks catalog.
"""
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterator, List

from src.features.plugins.repository import PluginRepository
from src.platform.plugins.registry import PluginRegistry
from src.platform.plugins.hooks import hooks_registry


def get_active_quick_actions(repo: PluginRepository, registry: PluginRegistry) -> List[Dict[str, Any]]:
    """
    Get quick actions from enabled plugins that have show_quick_actions enabled.

    Returns:
        List of quick action dicts for sidebar display
    """
    actions = []
    enabled_db_plugins = repo.get_enabled_plugins()

    for plugin in enabled_db_plugins:
        manifest = registry.get_plugin(plugin.id)
        if not manifest or not manifest.quick_actions:
            continue

        # Check show_quick_actions setting (default to True)
        show = True
        db_settings = repo.get_plugin_settings(plugin.id)
        for s in db_settings:
            if s.setting_key == "show_quick_actions":
                show = s.setting_value not in ("false", "False", "0", False)
                break

        if not show:
            continue

        for action_def in manifest.quick_actions:
            actions.append({
                "plugin_id": manifest.id,
                "plugin_name": manifest.name,
                "action_id": action_def.get("id"),
                "label": action_def.get("label"),
                "icon": action_def.get("icon"),
                "endpoint": action_def.get("endpoint"),
                "method": action_def.get("method", "POST"),
                "confirm": action_def.get("confirm"),
                "require_role": action_def.get("require_role"),
            })

    return actions


def get_active_sidebar_widgets(repo: PluginRepository, registry: PluginRegistry) -> List[Dict[str, Any]]:
    """
    Get sidebar widgets from enabled plugins.

    Returns:
        List of sidebar widget dicts sorted by order
    """
    widgets = []
    enabled_db_plugins = repo.get_enabled_plugins()

    for plugin in enabled_db_plugins:
        manifest = registry.get_plugin(plugin.id)
        if not manifest or not manifest.sidebar_widgets:
            continue

        for widget_def in manifest.sidebar_widgets:
            widgets.append({
                "plugin_id": manifest.id,
                "widget_id": widget_def.get("id"),
                "position": widget_def.get("position", "bottom"),
                "component": widget_def.get("component"),
                "order": widget_def.get("order", 100),
                "label": widget_def.get("label"),
            })

    widgets.sort(key=lambda w: w["order"])
    return widgets


def _revision_inputs(manifest) -> Iterator[Path]:
    """The files whose identity a plugin's frontend revision is taken from."""
    yield manifest.manifest_path
    try:
        dist = manifest.plugin_dir / "frontend" / "dist"
        yield from sorted(path for path in dist.iterdir() if path.is_file())
    except (OSError, TypeError):
        return


def plugin_frontend_revision(manifest) -> str:
    """Identity of a plugin's frontend surface: its declared version, its
    manifest file, and the compiled bundles a browser imports.

    The dist files are stamped in deliberately. A browser caches an imported
    module by URL for the life of the document, so a rebuilt bundle behind an
    unchanged manifest version is precisely the case that would otherwise stay
    invisible until a reload.
    """
    digest = hashlib.sha256()
    digest.update(str(getattr(manifest, "version", "")).encode("utf-8", "replace"))

    for path in _revision_inputs(manifest):
        try:
            stat = path.stat()
        except (OSError, TypeError, AttributeError):
            continue
        digest.update(f"\0{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode("utf-8", "replace"))

    return digest.hexdigest()[:16]


def get_frontend_extensions(repo: PluginRepository, registry: PluginRegistry) -> Dict[str, Any]:
    """
    Get manifest-declared `renderers:` and `contributions:` from enabled
    plugins, for the frontend renderer registries (A5) and extension
    slots. Manifest-derived only - no DB tables.

    Returns:
        {"renderers": [...], "contributions": [...], "revisions": {...}},
        each renderer/contribution annotated with its owning `plugin_id` and
        `revisions` mapping every contributing plugin id to its frontend
        revision (see `plugin_frontend_revision`). This is the only plugin
        endpoint a non-admin may call, so it has to carry the version signal
        itself - `GET /api/plugins`, which returns `version`/`updated_at`, is
        admin-only.
    """
    revisions: Dict[str, str] = {}
    renderers: List[Dict[str, Any]] = []
    contributions: List[Dict[str, Any]] = []
    enabled_db_plugins = repo.get_enabled_plugins()

    for plugin in enabled_db_plugins:
        manifest = registry.get_plugin(plugin.id)
        if not manifest:
            continue

        revisions[manifest.id] = plugin_frontend_revision(manifest)

        for renderer_def in manifest.renderers:
            renderers.append({
                "plugin_id": manifest.id,
                "kind": renderer_def.get("kind"),
                "key": renderer_def.get("key"),
                "component": renderer_def.get("component"),
            })

        for contribution_def in manifest.contributions:
            contributions.append({
                "plugin_id": manifest.id,
                "slot": contribution_def.get("slot"),
                "component": contribution_def.get("component"),
                "label": contribution_def.get("label"),
                "icon": contribution_def.get("icon"),
                "route": contribution_def.get("route"),
                "order": contribution_def.get("order", 100),
                "require_role": contribution_def.get("require_role"),
            })

    contributions.sort(key=lambda c: c["order"])
    return {"renderers": renderers, "contributions": contributions, "revisions": revisions}


def get_hooks_catalog() -> List[Dict[str, Any]]:
    """
    Get the full catalog of declared hook points (core + plugin-provided).

    Returns:
        List of {name, type, description, payload, mutable, use_when, example} dicts.
        Fields with no documentation are present with empty values (stable shape).
    """
    return [
        {
            "name": spec.name,
            "type": spec.type,
            "description": spec.description,
            "payload": dict(spec.payload),
            "mutable": list(spec.mutable),
            "use_when": list(spec.use_when),
            "example": spec.example,
        }
        for spec in sorted(hooks_registry.all(), key=lambda s: s.name)
    ]
