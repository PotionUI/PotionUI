from pathlib import Path

import yaml

from src.platform.runtime.pose.dwpose import DETECTOR_FILENAME, DWPOSE_REPO, POSE_FILENAME, WEIGHT_FILES

CATALOG = Path(__file__).resolve().parents[4] / "src" / "std" / "models" / "pose.yml"


def entries():
    return {entry["url"].rsplit("/", 1)[-1]: entry for entry in yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["models"]}


def test_the_catalog_lists_exactly_the_two_runtime_files():
    assert set(entries()) == {DETECTOR_FILENAME, POSE_FILENAME} == set(WEIGHT_FILES)


def test_every_entry_pins_the_hash_the_runtime_verifies():
    for name, entry in entries().items():
        assert entry["sha256"] == WEIGHT_FILES[name][1]


def test_entries_point_at_the_dwpose_repo_and_the_annotator_mirror_folder():
    for name, entry in entries().items():
        assert entry["url"] == f"https://huggingface.co/{DWPOSE_REPO}/resolve/main/{name}"
        assert entry["provider"] == "huggingface"
        assert entry["file_path"].endswith(f"annotators', 'yzd-v-dwpose/{name}') }}}}")
