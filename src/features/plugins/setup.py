from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional
from urllib.parse import urlencode

from pydantic import BaseModel

from src.features.plugins.setup_checks import SetupCheckContext
from src.features.presets.loader import plugin_preset_roots
from src.platform.observability.logger import logger

SetupStatus = Literal["done", "todo", "waiting"]
SetupActionKind = Literal["link", "restart", "settings"]


class SetupAction(BaseModel):
    kind: SetupActionKind
    label: str
    href: Optional[str] = None


class SetupStep(BaseModel):
    id: str
    kind: str
    label: str
    description: str = ""
    status: SetupStatus
    action: Optional[SetupAction] = None


class SetupGuide(BaseModel):
    title: str
    href: str


class PluginSetupReport(BaseModel):
    plugin_id: str
    complete: bool
    remaining: int
    steps: List[SetupStep]
    guide: Optional[SetupGuide] = None


def admin_href(**params: str) -> str:
    return "/admin?" + urlencode(params)


@dataclass
class _Outcome:
    status: SetupStatus
    description: str
    action: Optional[SetupAction] = None


DEFAULT_LABELS = {
    "plugin.settings": "Fill in the plugin settings",
    "backend.added": "Add a backend with your credentials",
    "cloud.models_enabled": "Turn on models in the catalog",
    "presets.installed": "Install the plugin's presets",
    "presets.assigned": "Give people access to the presets",
}


class PluginSetup:
    def __init__(
        self,
        plugin_registry: Any,
        plugin_repository: Any,
        backend_registry: Any,
        catalog_repository: Any,
        preset_loader: Any,
        preset_db_repo: Any,
        group_repo: Any,
    ) -> None:
        self.plugin_registry = plugin_registry
        self.plugin_repository = plugin_repository
        self.backend_registry = backend_registry
        self.catalog_repository = catalog_repository
        self.preset_loader = preset_loader
        self.preset_db_repo = preset_db_repo
        self.group_repo = group_repo

    def reports(self) -> List[PluginSetupReport]:
        return [self.report(manifest) for manifest in self.plugin_registry.get_enabled_plugins() if manifest.setup]

    def report(self, manifest: Any) -> PluginSetupReport:
        state = _State(self, manifest)
        steps: List[SetupStep] = []
        if state.unloaded_drivers:
            steps.append(
                SetupStep(
                    id="restart",
                    kind="restart",
                    label="Restart PotionUI",
                    description="Restart so the backend this plugin adds can be set up.",
                    status="todo",
                    action=SetupAction(kind="restart", label="Restart now"),
                )
            )
        for index, spec in enumerate(manifest.setup):
            kind = spec.get("kind")
            outcome = self._evaluate(kind, spec, state)
            steps.append(
                SetupStep(
                    id=f"{index}-{kind}",
                    kind=kind,
                    label=spec.get("label") or DEFAULT_LABELS.get(kind, kind),
                    description=(outcome.status != "done" and spec.get("description")) or outcome.description,
                    status=outcome.status,
                    action=None if outcome.status == "done" else outcome.action,
                )
            )
        remaining = sum(1 for step in steps if step.status != "done")
        return PluginSetupReport(
            plugin_id=manifest.id,
            complete=remaining == 0,
            remaining=remaining,
            steps=steps,
            guide=_guide(manifest),
        )

    def _evaluate(self, kind: str, spec: Mapping[str, Any], state: "_State") -> _Outcome:
        try:
            if kind == "plugin.settings":
                return self._settings(state)
            if kind == "backend.added":
                return self._backend(spec["driver"], state)
            if kind == "cloud.models_enabled":
                return self._models(spec["driver"], state)
            if kind == "presets.installed":
                return self._presets_installed(state)
            if kind == "presets.assigned":
                return self._presets_assigned(state)
            if kind == "check":
                return self._check(spec, state)
        except Exception as exc:
            logger.warning(f"[PLUGIN_SETUP] {state.manifest.id} step '{kind}' failed: {exc}")
            return _Outcome("todo", "This step could not be checked. See the server log.")
        return _Outcome("todo", f"Unknown setup step '{kind}'.")

    def _settings(self, state: "_State") -> _Outcome:
        missing = [
            setting.get("label") or setting["name"]
            for setting in state.manifest.settings
            if setting.get("required") and _blank(state.settings.get(setting["name"], setting.get("default")))
        ]
        if not missing:
            return _Outcome("done", "Every required setting is filled in.")
        return _Outcome(
            "todo",
            f"Still empty: {', '.join(missing)}.",
            SetupAction(kind="settings", label="Open settings"),
        )

    def _backend(self, driver: str, state: "_State") -> _Outcome:
        if driver in state.unloaded_drivers:
            return _Outcome("waiting", "Available after the restart.")
        configs = state.backends(driver)
        enabled = [config for config in configs if config.enabled]
        if enabled:
            names = ", ".join(config.name for config in enabled)
            return _Outcome("done", f"Set up: {names}.")
        if configs:
            return _Outcome(
                "todo",
                f"{configs[0].name} is turned off. Turn it on to use it.",
                SetupAction(kind="link", label="Open backend", href=admin_href(tab="backends", backend=configs[0].id)),
            )
        return _Outcome(
            "todo",
            "Add a backend for this plugin and enter its credentials, such as the API key.",
            SetupAction(kind="link", label="Add backend", href=admin_href(tab="backends", add=driver)),
        )

    def _models(self, driver: str, state: "_State") -> _Outcome:
        configs = [config for config in state.backends(driver) if config.enabled]
        if driver in state.unloaded_drivers or not configs:
            return _Outcome("waiting", "Available once a backend is set up.")
        enabled = sum(int(self.catalog_repository.counts(config.id).get("enabled") or 0) for config in configs)
        if enabled:
            return _Outcome("done", f"{enabled} model{'s' if enabled != 1 else ''} turned on.")
        return _Outcome(
            "todo",
            "Nothing is turned on yet. Pick the models people may use from the backend's catalog.",
            SetupAction(kind="link", label="Open catalog", href=admin_href(tab="backends", backend=configs[0].id, view="catalog")),
        )

    def _presets_installed(self, state: "_State") -> _Outcome:
        presets = state.presets
        if not presets:
            return _Outcome("done", "This plugin ships no presets.")
        installed = [preset for preset in presets if preset.id in state.installed_ids]
        if installed:
            return _Outcome("done", f"{len(installed)} of {len(presets)} installed.")
        return _Outcome(
            "todo",
            f"None of its {len(presets)} presets are installed yet.",
            SetupAction(kind="link", label="Open presets", href=admin_href(tab="presets", id=presets[0].id)),
        )

    def _presets_assigned(self, state: "_State") -> _Outcome:
        if not state.presets:
            return _Outcome("done", "This plugin ships no presets.")
        installed = [preset for preset in state.presets if preset.id in state.installed_ids]
        if not installed:
            return _Outcome("waiting", "Available once a preset is installed.")
        assigned = [preset for preset in installed if self._assignment_count(preset.id) > 0]
        if assigned:
            return _Outcome("done", f"{len(assigned)} of {len(installed)} installed presets are assigned.")
        return _Outcome(
            "todo",
            "No one can see the presets yet. Assign them to users or groups.",
            SetupAction(kind="link", label="Assign presets", href=admin_href(tab="presets", id=installed[0].id)),
        )

    def _assignment_count(self, preset_id: str) -> int:
        summary = self.preset_db_repo.get_preset_assignment_summary(preset_id)
        return int(summary.get("total_assignments") or 0) + int(self.group_repo.get_group_count_for_preset(preset_id) or 0)

    def _check(self, spec: Mapping[str, Any], state: "_State") -> _Outcome:
        check_class = self.plugin_registry.loader.load_class(state.manifest, spec["check"])
        if check_class is None:
            return _Outcome("todo", "This step could not be loaded. See the server log.")
        result = check_class().evaluate(SetupCheckContext(plugin_id=state.manifest.id, settings=dict(state.settings)))
        action = (
            SetupAction(kind="link", label=result.action_label or "Open", href=result.action_href)
            if result.action_href
            else None
        )
        return _Outcome("done" if result.done else "todo", result.description, action)


class _State:
    def __init__(self, setup: PluginSetup, manifest: Any) -> None:
        self.setup = setup
        self.manifest = manifest
        self._backends: Optional[List[Any]] = None
        self._presets: Optional[List[Any]] = None
        self._installed: Optional[set] = None
        self._settings: Optional[Dict[str, Any]] = None
        registered = setup.backend_registry.get_registered_config_types()
        self.unloaded_drivers = {
            spec["driver"] for spec in manifest.setup if spec.get("driver") and spec["driver"] not in registered
        }

    @property
    def settings(self) -> Dict[str, Any]:
        if self._settings is None:
            self._settings = {
                setting.setting_key: setting.setting_value
                for setting in self.setup.plugin_repository.get_plugin_settings(self.manifest.id)
            }
        return self._settings

    def backends(self, driver: str) -> List[Any]:
        if self._backends is None:
            self._backends = list(self.setup.backend_registry.backend_config_store.get_backends())
        return [config for config in self._backends if getattr(config, "driver", None) == driver]

    @property
    def presets(self) -> List[Any]:
        if self._presets is None:
            roots = {root.resolve() for root in plugin_preset_roots([self.manifest])}
            templates = [self.setup.preset_loader.load_preset_by_id(preset_id) for _, preset_id in self.setup.preset_loader.get_all_presets()]
            self._presets = sorted(
                (t for t in templates if t is not None and t.base_path and Path(t.base_path).resolve() in roots),
                key=lambda t: t.name,
            )
        return self._presets

    @property
    def installed_ids(self) -> set:
        if self._installed is None:
            self._installed = {preset.preset_id for preset in self.setup.preset_db_repo.get_all_installed_presets()}
        return self._installed


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _guide(manifest: Any) -> Optional[SetupGuide]:
    docs = manifest.docs or []
    if not docs:
        return None
    first = docs[0]
    return SetupGuide(
        title=first.get("title") or "Guide",
        href=admin_href(tab="docs", doc=f"plugin/{manifest.id}/{Path(first['path']).stem}"),
    )
