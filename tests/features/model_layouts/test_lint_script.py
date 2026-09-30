import yaml

from scripts import model_layout_lint
from tests.features.model_layouts.helpers import layout_data, write_layout


def test_clean_directory_passes(tmp_path, capsys):
    write_layout(tmp_path, "good")
    assert model_layout_lint.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "good.yml: OK" in out and "No issues found." in out


def test_invalid_layout_fails_and_lists_issues(tmp_path, capsys):
    (tmp_path / "bad.yml").write_text(
        yaml.safe_dump(layout_data(id="bad", folders=[{"path": "../x", "model_type": "lora"}])), encoding="utf-8"
    )
    assert model_layout_lint.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "folders[0].path" in out and "1 issue(s) found." in out


def test_duplicate_id_across_roots_fails(tmp_path, capsys):
    write_layout(tmp_path / "a", "dup")
    write_layout(tmp_path / "b", "dup")
    assert model_layout_lint.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 1
    assert "Duplicate layout id 'dup'" in capsys.readouterr().out


def test_unparseable_file_fails(tmp_path, capsys):
    (tmp_path / "bad.yml").write_text("a: [", encoding="utf-8")
    assert model_layout_lint.main([str(tmp_path)]) == 1
    assert "Could not parse YAML" in capsys.readouterr().out


def test_no_files_is_not_an_error(tmp_path, capsys):
    assert model_layout_lint.main([str(tmp_path)]) == 0
    assert "No model layout files found." in capsys.readouterr().out


def test_missing_path_is_skipped(tmp_path):
    assert model_layout_lint.main([str(tmp_path / "missing")]) == 0
