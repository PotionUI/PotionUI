from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.features.plugins.setup import PluginSetup
from src.plugin_api import SetupCheck, SetupCheckResult
from src.platform.plugins.manifest import PluginManifestSchema

DRIVER = "cloud.fake"


class World:
    def __init__(self, tmp_path):
        self.plugin_dir = tmp_path / "fake-provider"
        (self.plugin_dir / "presets").mkdir(parents=True)
        self.other_root = tmp_path / "core-presets"
        self.other_root.mkdir()
        self.registered = {}
        self.backends = []
        self.enabled_counts = {}
        self.installed = set()
        self.user_assignments = {}
        self.group_assignments = {}
        self.settings = {}
        self.check_class = None
        self.presets = [
            SimpleNamespace(id="p-video", name="Fake Video", base_path=str(self.plugin_dir / "presets")),
            SimpleNamespace(id="p-image", name="Fake Image", base_path=str(self.plugin_dir / "presets")),
            SimpleNamespace(id="p-core", name="Core Preset", base_path=str(self.other_root)),
        ]
        self.manifest = SimpleNamespace(
            id="fake-provider",
            plugin_dir=self.plugin_dir,
            presets=[{"path": "presets"}],
            settings=[],
            docs=[{"path": "README.md", "title": "Fake guide"}],
            setup=[
                {"kind": "backend.added", "driver": DRIVER},
                {"kind": "cloud.models_enabled", "driver": DRIVER},
                {"kind": "presets.installed"},
                {"kind": "presets.assigned"},
            ],
        )
        self.plain = SimpleNamespace(id="plain", setup=[], settings=[], docs=[], presets=[], plugin_dir=tmp_path)

    def setup(self):
        world = self
        return PluginSetup(
            plugin_registry=SimpleNamespace(
                get_enabled_plugins=lambda: [world.manifest, world.plain],
                loader=SimpleNamespace(load_class=lambda manifest, ref: world.check_class),
            ),
            plugin_repository=SimpleNamespace(
                get_plugin_settings=lambda plugin_id: [
                    SimpleNamespace(setting_key=key, setting_value=value) for key, value in world.settings.items()
                ]
            ),
            backend_registry=SimpleNamespace(
                get_registered_config_types=lambda: world.registered,
                backend_config_store=SimpleNamespace(get_backends=lambda: world.backends),
            ),
            catalog_repository=SimpleNamespace(counts=lambda backend_id: {"enabled": world.enabled_counts.get(backend_id, 0)}),
            preset_loader=SimpleNamespace(
                get_all_presets=lambda: [(p.name, p.id) for p in world.presets],
                load_preset_by_id=lambda preset_id: next(p for p in world.presets if p.id == preset_id),
            ),
            preset_db_repo=SimpleNamespace(
                get_all_installed_presets=lambda: [SimpleNamespace(preset_id=i) for i in world.installed],
                get_preset_assignment_summary=lambda preset_id: {"total_assignments": world.user_assignments.get(preset_id, 0)},
            ),
            group_repo=SimpleNamespace(get_group_count_for_preset=lambda preset_id: world.group_assignments.get(preset_id, 0)),
        )

    def report(self):
        return self.setup().report(self.manifest)

    def add_backend(self, enabled=True):
        self.registered[DRIVER] = object
        backend = SimpleNamespace(id="b1", name="Fake Cloud", driver=DRIVER, enabled=enabled)
        self.backends.append(backend)
        return backend


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _by_kind(report):
    return {step.kind: step for step in report.steps}


def test_unloaded_driver_puts_a_restart_first_and_holds_the_backend_steps(world):
    report = world.report()

    assert [step.kind for step in report.steps] == [
        "restart", "backend.added", "cloud.models_enabled", "presets.installed", "presets.assigned",
    ]
    steps = _by_kind(report)
    assert steps["restart"].status == "todo"
    assert steps["restart"].action.kind == "restart"
    assert steps["backend.added"].status == "waiting"
    assert steps["backend.added"].action is None
    assert steps["cloud.models_enabled"].status == "waiting"
    assert steps["presets.installed"].status == "todo"
    assert steps["presets.installed"].action.href == "/admin?tab=presets&id=p-image"
    assert steps["presets.assigned"].status == "waiting"
    assert report.remaining == 5
    assert report.complete is False
    assert report.guide.title == "Fake guide"
    assert report.guide.href == "/admin?tab=docs&doc=plugin%2Ffake-provider%2FREADME"


def test_loaded_driver_without_a_backend_links_to_add_backend_for_that_driver(world):
    world.registered[DRIVER] = object

    steps = _by_kind(world.report())

    assert "restart" not in steps
    assert steps["backend.added"].status == "todo"
    assert steps["backend.added"].action.kind == "link"
    assert steps["backend.added"].action.href == "/admin?tab=backends&add=cloud.fake"
    assert "credentials" in steps["backend.added"].description
    assert steps["cloud.models_enabled"].status == "waiting"


def test_turned_off_backend_links_to_that_backend(world):
    world.add_backend(enabled=False)
    world.backends.append(SimpleNamespace(id="other", name="Other", driver="cloud.other", enabled=True))

    steps = _by_kind(world.report())

    assert steps["backend.added"].status == "todo"
    assert steps["backend.added"].action.href == "/admin?tab=backends&backend=b1"
    assert steps["cloud.models_enabled"].status == "waiting"


def test_backend_without_enabled_models_links_to_its_catalog(world):
    world.add_backend()

    steps = _by_kind(world.report())

    assert steps["backend.added"].status == "done"
    assert steps["backend.added"].action is None
    assert "Fake Cloud" in steps["backend.added"].description
    assert steps["cloud.models_enabled"].status == "todo"
    assert steps["cloud.models_enabled"].action.href == "/admin?tab=backends&backend=b1&view=catalog"


def test_installed_but_unassigned_presets_link_to_the_installed_preset(world):
    world.add_backend()
    world.enabled_counts["b1"] = 3
    world.installed = {"p-video", "p-core"}

    steps = _by_kind(world.report())

    assert steps["cloud.models_enabled"].status == "done"
    assert steps["cloud.models_enabled"].description == "3 models turned on."
    assert steps["presets.installed"].status == "done"
    assert steps["presets.installed"].description == "1 of 2 installed."
    assert steps["presets.assigned"].status == "todo"
    assert steps["presets.assigned"].action.href == "/admin?tab=presets&id=p-video"


def test_core_presets_never_count_for_the_plugin(world):
    world.add_backend()
    world.enabled_counts["b1"] = 1
    world.installed = {"p-core"}
    world.group_assignments["p-core"] = 2

    steps = _by_kind(world.report())

    assert steps["presets.installed"].status == "todo"
    assert steps["presets.assigned"].status == "waiting"


@pytest.mark.parametrize("users,groups", [(1, 0), (0, 1)])
def test_everything_in_place_is_complete_with_no_actions(world, users, groups):
    world.add_backend()
    world.enabled_counts["b1"] = 1
    world.installed = {"p-image"}
    world.user_assignments["p-image"] = users
    world.group_assignments["p-image"] = groups

    report = world.report()

    assert report.complete is True
    assert report.remaining == 0
    assert all(step.status == "done" and step.action is None for step in report.steps)


def test_plugin_without_presets_has_nothing_to_install_or_assign(world):
    world.manifest.presets = []

    steps = _by_kind(world.report())

    assert steps["presets.installed"].status == "done"
    assert steps["presets.assigned"].status == "done"


def test_required_settings_must_be_filled(world):
    world.manifest.setup = [{"kind": "plugin.settings"}]
    world.manifest.settings = [
        {"name": "api_key", "label": "API key", "required": True},
        {"name": "region", "label": "Region", "required": True, "default": "eu"},
        {"name": "note", "label": "Note"},
    ]
    world.settings = {"api_key": "  "}

    step = world.report().steps[0]

    assert step.status == "todo"
    assert step.description == "Still empty: API key."
    assert step.action.kind == "settings"

    world.settings = {"api_key": "sk-1"}
    step = world.report().steps[0]
    assert step.status == "done"
    assert step.action is None


def test_manifest_label_and_description_override_the_defaults_until_done(world):
    world.manifest.setup = [{"kind": "presets.installed", "label": "Install the studio", "description": "Pick one."}]

    step = world.report().steps[0]
    assert step.label == "Install the studio"
    assert step.description == "Pick one."

    world.installed = {"p-image"}
    step = world.report().steps[0]
    assert step.label == "Install the studio"
    assert step.description == "1 of 2 installed."


def test_plugin_check_sees_its_settings_and_can_link_somewhere(world):
    seen = {}

    class TokenCheck(SetupCheck):
        def evaluate(self, context):
            seen["context"] = context
            done = bool(context.settings.get("token"))
            return SetupCheckResult(done=done, description="Token", action_label="Get a token", action_href="/admin?tab=docs")

    world.check_class = TokenCheck
    world.manifest.setup = [{"kind": "check", "check": "checks:TokenCheck", "label": "Connect the account"}]

    step = world.report().steps[0]
    assert seen["context"].plugin_id == "fake-provider"
    assert step.label == "Connect the account"
    assert step.status == "todo"
    assert step.action.label == "Get a token"
    assert step.action.href == "/admin?tab=docs"

    world.settings = {"token": "t"}
    step = world.report().steps[0]
    assert step.status == "done"
    assert step.action is None


def test_broken_plugin_check_reports_a_step_still_to_do(world):
    class Broken(SetupCheck):
        def evaluate(self, context):
            raise RuntimeError("boom")

    world.manifest.setup = [{"kind": "check", "check": "checks:Missing", "label": "Missing"}]
    assert world.report().steps[0].status == "todo"

    world.check_class = Broken
    report = world.report()
    assert report.steps[0].status == "todo"
    assert report.complete is False


def test_reports_cover_only_enabled_plugins_that_declare_setup(world):
    reports = world.setup().reports()

    assert [report.plugin_id for report in reports] == ["fake-provider"]


def _manifest(setup):
    return {
        "id": "p", "name": "P", "version": "1", "description": "d", "author": "a",
        "type": "backend-only", "setup": setup,
    }


def test_manifest_accepts_every_setup_kind():
    schema = PluginManifestSchema.model_validate(_manifest([
        {"kind": "plugin.settings"},
        {"kind": "backend.added", "driver": DRIVER},
        {"kind": "cloud.models_enabled", "driver": DRIVER},
        {"kind": "presets.installed"},
        {"kind": "presets.assigned"},
        {"kind": "check", "check": "checks:Thing", "label": "Thing"},
    ]))

    assert [step.kind for step in schema.setup][-1] == "check"


@pytest.mark.parametrize("step", [
    {"kind": "backend.added"},
    {"kind": "cloud.models_enabled"},
    {"kind": "check", "check": "checks:Thing"},
    {"kind": "check", "label": "Thing"},
    {"kind": "backend.ready", "driver": DRIVER},
    {"kind": "presets.installed", "extra": 1},
])
def test_manifest_rejects_incomplete_or_unknown_steps(step):
    with pytest.raises(ValidationError):
        PluginManifestSchema.model_validate(_manifest([step]))
