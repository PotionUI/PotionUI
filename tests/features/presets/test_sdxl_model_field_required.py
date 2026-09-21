from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings=Mock()))


@pytest.fixture(scope="module")
def sdxl(loader):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/SDXL")]
    assert len(matches) == 1
    return matches[0]


def _find_field(node, name):
    if isinstance(node, dict):
        if node.get("name") == name and node.get("type") == "model":
            return node
        for value in node.values():
            found = _find_field(value, name)
            if found is not None:
                return found
    if isinstance(node, list):
        for item in node:
            found = _find_field(item, name)
            if found is not None:
                return found
    return None


@pytest.mark.parametrize("mode", ["txt2img", "inpaint"])
def test_sdxl_base_model_field_is_required(sdxl, serializer, mode):
    schema = serializer.process_form_fields(sdxl.modes[mode].forms[0], sdxl.id)
    field = _find_field(schema["properties"], "model")
    assert field is not None
    assert field["type"] == "model"
    assert field.get("required") is True
