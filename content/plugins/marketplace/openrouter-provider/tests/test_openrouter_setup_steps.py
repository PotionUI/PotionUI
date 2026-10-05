from pathlib import Path

import yaml

from backend.config import OpenRouterConfig

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.yml"


def _setup():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["setup"]


def test_setup_walks_from_backend_to_assigned_presets():
    assert [step["kind"] for step in _setup()] == [
        "backend.added",
        "cloud.models_enabled",
        "presets.installed",
        "presets.assigned",
    ]


def test_setup_steps_name_the_driver_the_backend_registers():
    driver = OpenRouterConfig.model_fields["driver"].default

    assert {step["driver"] for step in _setup() if "driver" in step} == {driver}
