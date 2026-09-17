import importlib
import sys
from pathlib import Path

_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider


def test_model_id_only():
    provider = CivitaiProvider()
    assert provider.get_model_page_url("101055") == "https://civitai.com/models/101055"


def test_model_and_version_id():
    provider = CivitaiProvider()
    url = provider.get_model_page_url("101055", "126601")
    assert url == "https://civitai.com/models/101055?modelVersionId=126601"


def test_no_model_id_returns_none():
    provider = CivitaiProvider()
    assert provider.get_model_page_url(None) is None
    assert provider.get_model_page_url("") is None
