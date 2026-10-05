from pathlib import Path

import pytest
import yaml

PRESETS = Path(__file__).resolve().parents[1] / "presets"
FORMS = sorted(PRESETS.glob("*/standard/modes/*/form.yml"))


def _provider_tab(form_path):
    form = yaml.safe_load(form_path.read_text(encoding="utf-8"))
    tabs = next(field for field in form["fields"] if field["type"] == "tabs")
    return next(tab for tab in tabs["children"] if tab.get("label") == "Provider options")


def test_every_mode_ships_the_provider_options_tab():
    assert len(FORMS) == 4


@pytest.mark.parametrize("form_path", FORMS, ids=lambda path: path.parent.name)
def test_provider_options_follow_the_model_not_the_advanced_view(form_path):
    tab = _provider_tab(form_path)

    assert tab.get("audience", "simple") == "simple"
    assert tab["children"].endswith("/cloud/tabs/provider_options.yml")
