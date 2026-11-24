from pathlib import Path

import pytest
import yaml

from scripts import model_modes_reference as mmr

MAPPING = {"alpha.md": ["Alpha", "Beta"]}


def _preset(root: Path, name: str, modes: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True)
    (pdir / "preset.yml").write_text(yaml.safe_dump({"name": name, "modes": list(modes)}), encoding="utf-8")
    for mode, label in modes.items():
        mdir = pdir / "modes" / mode
        mdir.mkdir(parents=True)
        form = {"name": "custom", "fields": []}
        if label:
            form["mode_label"] = label
        (mdir / "form.yml").write_text(yaml.safe_dump(form), encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    presets = tmp_path / "content" / "presets" / "marketplace"
    _preset(presets, "Alpha", {"txt2img": None, "edit": "Edit image"})
    _preset(presets, "Beta", {"video": "Clip"})
    models = tmp_path / "docs" / "models"
    models.mkdir(parents=True)
    page = models / "alpha.md"
    page.write_text(f"# Alpha\n\n{mmr.START}\nstale\n{mmr.END}\n\nafter\n", encoding="utf-8")
    return tmp_path, page


def test_generation_writes_modes_and_labels(tree):
    root, page = tree
    report = mmr.sync(root / "docs" / "models", root / "content" / "presets" / "marketplace", MAPPING, write=True)
    assert report.ok
    text = page.read_text(encoding="utf-8")
    assert "**Alpha**" in text and "**Beta**" in text
    assert "- `txt2img`: Text to Image" in text
    assert "- `edit`: Edit image" in text
    assert "- `video`: Clip" in text
    assert "stale" not in text
    assert text.startswith("# Alpha") and text.rstrip().endswith("after")


def test_check_passes_when_synced(tree):
    root, _ = tree
    mmr.sync(root / "docs" / "models", root / "content" / "presets" / "marketplace", MAPPING, write=True)
    assert mmr.check(root, MAPPING).ok


def test_check_fails_on_stale_page(tree):
    root, _ = tree
    report = mmr.check(root, MAPPING)
    assert not report.ok
    assert "out of date" in report.errors[0]


def test_check_fails_when_preset_gains_a_mode(tree):
    root, _ = tree
    presets = root / "content" / "presets" / "marketplace"
    mmr.sync(root / "docs" / "models", presets, MAPPING, write=True)
    _preset(presets, "Gamma", {"x": None})
    (presets / "Beta" / "preset.yml").write_text(
        yaml.safe_dump({"name": "Beta", "modes": ["video", "extra"]}), encoding="utf-8"
    )
    assert not mmr.check(root, MAPPING).ok


def test_check_fails_without_markers(tree):
    root, page = tree
    page.write_text("# Alpha\n", encoding="utf-8")
    report = mmr.check(root, MAPPING)
    assert any("markers" in e for e in report.errors)


def test_unmapped_pages_are_skipped(tree):
    root, _ = tree
    (root / "docs" / "models" / "other.md").write_text("no markers\n", encoding="utf-8")
    mmr.sync(root / "docs" / "models", root / "content" / "presets" / "marketplace", MAPPING, write=True)
    assert mmr.check(root, MAPPING).ok


def test_unknown_preset_is_an_error(tree):
    root, _ = tree
    report = mmr.check(root, {"alpha.md": ["Missing"]})
    assert any("not found" in e for e in report.errors)


def test_repo_pages_are_in_sync():
    assert mmr.check().ok
