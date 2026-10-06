import pytest

from scripts import filter_lint
from src.features.filters.lint import lint_roots
from src.features.filters.schema import SOURCE_LOCAL, SOURCE_PLUGIN
from src.platform.imaging.filters import merged_ops
from tests.features.filters.conftest import CUBE_2, REPO_ROOT, write_filter

PLUGIN_OPS = merged_ops()


def lint(root, ops=None):
    return lint_roots([(root, SOURCE_LOCAL, "")], ops or PLUGIN_OPS)


def rules(report, level=None):
    return sorted(
        f"{f.level}:{f.rule}" if level is None else f.rule
        for path, f in (report.errors + report.warnings)
        if level is None or f.level == level
    )


def test_a_clean_filter_passes(tmp_path):
    write_filter(tmp_path, "clean", name="Clean")

    report = lint(tmp_path)

    assert report.ok and report.scanned == [str(tmp_path / "clean")]
    assert report.errors == []


def test_unknown_op(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "foo"}])

    assert rules(lint(tmp_path), "error") == ["op_unknown"]


def test_out_of_range_param(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "tone", "contrast": 101}])

    assert rules(lint(tmp_path), "error") == ["param_range"]


def test_unknown_param(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "tone", "sparkle": 1}])

    assert rules(lint(tmp_path), "error") == ["param_unknown"]


def test_bad_curve_points(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "curves", "master": [[0, 0], [0, 1]]}])

    assert rules(lint(tmp_path), "error") == ["curve_points"]


def test_colour_after_spatial(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "vignette", "amount": 5}, {"op": "tone", "contrast": 5}])

    assert rules(lint(tmp_path), "error") == ["step_order"]


def test_too_many_steps(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "invert"}] * 65)

    assert "step_count" in rules(lint(tmp_path), "error")


def test_id_must_match_the_directory(tmp_path):
    directory = write_filter(tmp_path, "real")
    directory.rename(tmp_path / "other")

    assert rules(lint(tmp_path), "error") == ["id_dir"]


@pytest.mark.parametrize("bad_id", ["mine:abc", "a:b"])
def test_ids_containing_a_colon_are_reserved(tmp_path, bad_id):
    write_filter(tmp_path, bad_id)

    assert rules(lint(tmp_path), "error") == ["reserved_id"]


def test_id_pattern(tmp_path):
    write_filter(tmp_path, "Bad_ID")

    assert rules(lint(tmp_path), "error") == ["schema"]


def test_schema_violations(tmp_path):
    write_filter(tmp_path, "x", intensity=150, surprise=True, description="d" * 241)

    messages = [str(f) for _, f in lint(tmp_path).errors]

    assert any("surprise" in m and "unknown key" in m for m in messages)
    assert any(m.startswith("intensity") for m in messages)
    assert any(m.startswith("description") for m in messages)


def test_unparseable_yaml_and_oversize_are_parse_errors(tmp_path):
    (write_filter(tmp_path, "a") / "filter.yml").write_text("a: [", encoding="utf-8")
    (write_filter(tmp_path, "b") / "filter.yml").write_text("# " + "x" * 70000, encoding="utf-8")

    assert rules(lint(tmp_path), "error") == ["parse", "parse"]


def test_a_lut_without_a_license_is_an_error(tmp_path):
    write_filter(tmp_path, "x", cube=CUBE_2)

    assert rules(lint(tmp_path), "error") == ["lut_license"]


def test_a_lut_with_a_license_passes(tmp_path):
    write_filter(tmp_path, "x", cube=CUBE_2, license="MIT")

    assert lint(tmp_path).ok


def test_missing_lut(tmp_path):
    write_filter(tmp_path, "x", license="MIT", lut="lut.cube")

    assert rules(lint(tmp_path), "error") == ["lut_missing"]


@pytest.mark.parametrize("name", ["../lut.cube", "sub/lut.cube", ".hidden.cube", "lut.txt"])
def test_lut_path_must_be_a_plain_cube_name(tmp_path, name):
    write_filter(tmp_path, "x", license="MIT", lut=name)

    assert rules(lint(tmp_path), "error") == ["lut_path"]


@pytest.mark.parametrize(
    "cube",
    [
        "LUT_3D_SIZE 2\n0 0 0\n",
        "LUT_1D_SIZE 4\n0 0 0\n1 1 1\n",
        "LUT_3D_SIZE 70\n",
        "garbage",
        "LUT_3D_SIZE 2\n" + "0 0 nan\n" * 8,
    ],
)
def test_broken_cube_is_lut_format(tmp_path, cube):
    write_filter(tmp_path, "x", cube=cube, license="MIT")

    assert rules(lint(tmp_path), "error") == ["lut_format"]


def test_oversized_cube_is_lut_format(tmp_path):
    write_filter(tmp_path, "x", cube="# " + "x" * (8 * 1024 * 1024 + 1), license="MIT")

    assert rules(lint(tmp_path), "error") == ["lut_format"]


def test_lut_symlink_escaping_the_directory_is_lut_path(tmp_path):
    outside = tmp_path / "outside.cube"
    outside.write_text(CUBE_2, encoding="utf-8")
    root = tmp_path / "root"
    directory = write_filter(root, "x", license="MIT", lut="lut.cube")
    (directory / "lut.cube").symlink_to(outside)

    assert rules(lint(root), "error") == ["lut_path"]


def test_uncommon_cube_size_is_only_a_warning(tmp_path):
    write_filter(tmp_path, "x", cube=CUBE_2, license="MIT")

    assert lint(tmp_path).ok
    assert "lut_size" in rules(lint(tmp_path), "warning")


def test_no_effect_warns(tmp_path):
    write_filter(tmp_path, "empty", steps=[])
    write_filter(tmp_path, "defaults", steps=[{"op": "tone"}, {"op": "vignette"}])

    report = lint(tmp_path)

    assert report.ok
    assert rules(report, "warning").count("no_effect") == 2


def test_name_longer_than_24_warns(tmp_path):
    write_filter(tmp_path, "x", name="n" * 30)

    report = lint(tmp_path)

    assert report.ok and "name_len" in rules(report, "warning")


def test_a_group_used_by_nobody_else_warns(tmp_path):
    write_filter(tmp_path, "a", group="Colur")
    write_filter(tmp_path, "b", group="Colour")
    write_filter(tmp_path, "c", group="Pair")
    write_filter(tmp_path, "d", group="Pair")

    report = lint(tmp_path)

    typos = [(p, f) for p, f in report.warnings if f.rule == "group_new"]
    assert [p.endswith("/a") for p, _ in typos] == [True]


def test_a_look_that_clips_much_of_the_cube_warns(tmp_path):
    write_filter(tmp_path, "x", steps=[{"op": "exposure", "stops": 2}])
    write_filter(tmp_path, "y", steps=[{"op": "tone", "contrast": 10}])

    warned = [p for p, f in lint(tmp_path).warnings if f.rule == "preview_gamut"]

    assert [p.endswith("/x") for p in warned] == [True]


def test_duplicate_ids_in_one_namespace_are_an_error(tmp_path):
    write_filter(tmp_path / "a", "dup")
    write_filter(tmp_path / "b", "dup")

    report = lint_roots([(tmp_path / "a", SOURCE_LOCAL, ""), (tmp_path / "b", SOURCE_LOCAL, "")], PLUGIN_OPS)

    assert rules(report, "error") == ["id_taken"]


def test_local_overriding_marketplace_is_only_a_note(tmp_path):
    write_filter(tmp_path / "market", "same")
    write_filter(tmp_path / "mine", "same")

    report = lint_roots(
        [(tmp_path / "market", "builtin", ""), (tmp_path / "mine", SOURCE_LOCAL, "")], PLUGIN_OPS
    )

    assert report.ok
    notes = [f for items in report.findings.values() for f in items if f.level == "note"]
    assert [n.rule for n in notes] == ["id_taken"]


def test_same_id_in_different_plugins_does_not_collide(tmp_path):
    write_filter(tmp_path / "a", "same")
    write_filter(tmp_path / "b", "same")

    report = lint_roots(
        [(tmp_path / "a", SOURCE_PLUGIN, "one"), (tmp_path / "b", SOURCE_PLUGIN, "two")], PLUGIN_OPS
    )

    assert report.ok


def test_plugin_ops_without_python_warn_but_pass(tmp_path):
    from src.platform.imaging.filters import OpSpec

    ops = merged_ops({"demo.edit": OpSpec("demo.edit", "Edit", "colour", (), "plugin", "demo", None)})
    write_filter(tmp_path, "x", steps=[{"op": "demo.edit"}])

    report = lint(tmp_path, ops)

    assert report.ok and "op_no_backend" in rules(report, "warning")


class TestCli:
    def test_clean_directory_exits_zero(self, tmp_path, capsys):
        write_filter(tmp_path, "good")

        assert filter_lint.main([str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "0 error(s)" in out

    def test_errors_exit_nonzero_and_name_the_rule(self, tmp_path, capsys):
        write_filter(tmp_path, "bad", steps=[{"op": "foo"}])

        assert filter_lint.main([str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert "filter.op_unknown" in out and "steps[0].op" in out and "1 error(s)" in out

    def test_warnings_alone_exit_zero(self, tmp_path, capsys):
        write_filter(tmp_path, "x", steps=[])

        assert filter_lint.main([str(tmp_path)]) == 0
        assert "filter.no_effect" in capsys.readouterr().out

    def test_empty_directory_is_not_an_error(self, tmp_path, capsys):
        assert filter_lint.main([str(tmp_path)]) == 0
        assert "No filter directories found." in capsys.readouterr().out

    def test_the_repository_content_lints_clean(self, capsys):
        assert filter_lint.main([]) == 0
        out = capsys.readouterr().out
        assert "0 error(s)" in out
        assert str(REPO_ROOT / "content" / "filters" / "marketplace" / "ember") in out
