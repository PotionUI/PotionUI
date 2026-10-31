from pathlib import Path

import yaml

from src.platform.assets import asset_subdir
from src.platform.runtime.depth.video_depth import DEPTH_REPO, WEIGHT_FILENAME, WEIGHT_SHA256

CATALOG = Path(__file__).resolve().parents[4] / "src" / "std" / "models" / "video_depth.yml"


def entries():
    return yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["models"]


def test_the_catalog_lists_only_the_small_checkpoint():
    (entry,) = entries()
    assert entry["url"] == f"https://huggingface.co/{DEPTH_REPO}/resolve/main/{WEIGHT_FILENAME}"
    assert "Base" not in entry["url"] and "Large" not in entry["url"]


def test_the_entry_pins_the_hash_the_runtime_verifies():
    (entry,) = entries()
    assert entry["sha256"] == WEIGHT_SHA256
    assert entry["provider"] == "huggingface" and entry["type"] == "annotator"


def test_the_entry_lands_where_the_asset_fetch_does():
    (entry,) = entries()
    category, folder = asset_subdir("annotators", DEPTH_REPO).split("/")
    assert entry["file_path"] == f"{{{{ path('{category}', '{folder}/{WEIGHT_FILENAME}') }}}}"
