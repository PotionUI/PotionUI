"""Tests for the typed-doc linter (src/core/docs/lint.py) + the thin CLI."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.features.docs.lint import lint_docs, known_family_keys

# The thin CLI wrapper (for the exit-code test).
_spec = importlib.util.spec_from_file_location(
    "docs_lint_cli", Path(__file__).resolve().parents[3] / "scripts" / "docs_lint.py"
)
docs_lint_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_lint_cli)


def _docs(tmp_path: Path, techniques=None, models=None) -> Path:
    root = tmp_path / "docs"
    (root / "techniques").mkdir(parents=True)
    (root / "models").mkdir(parents=True)
    for name, body in (techniques or {}).items():
        (root / "techniques" / name).write_text(body)
    for name, body in (models or {}).items():
        (root / "models" / name).write_text(body)
    return root


def _error_msgs(root: Path):
    return [i.message for i in lint_docs(root).errors]


def _warning_msgs(root: Path):
    return [i.message for i in lint_docs(root).warnings]


_VALID_TECH = """---
type: technique
title: FBCache
category_group: Performance
status: stable
families: [all-native]
authors: [team]
paper: {arxiv: "2406.01733"}
---
Body.
"""

_VALID_MODEL = """---
type: model
title: Flux
family_key: flux
modes: [txt2img]
spec: {arch: MMDiT, latent: 16ch, vae: flux_ae, te: t5, guidance: embedded, shift: 1.15, engine: native}
---
Body.
"""


def test_valid_docs_lint_clean(tmp_path):
    root = _docs(tmp_path, {"fbcache.md": _VALID_TECH}, {"flux.md": _VALID_MODEL})
    report = lint_docs(root)
    assert report.ok and report.errors == []


def test_report_to_dict_shape(tmp_path):
    root = _docs(tmp_path, {"bad.md": "---\ntype: technique\ntitle: X\nstatus: stable\n---\nB."})
    d = lint_docs(root).to_dict()
    assert d["total_errors"] >= 1 and "issues" in d
    assert all(set(i) == {"level", "path", "message"} for i in d["issues"])


def test_schema_error_is_reported(tmp_path):
    bad = "---\ntype: technique\ntitle: X\nstatus: stable\n---\nB."  # missing category_group
    root = _docs(tmp_path, {"bad.md": bad})
    assert any("category_group" in e for e in _error_msgs(root))


def test_unknown_family_key_error(tmp_path):
    bad = _VALID_TECH.replace("families: [all-native]", "families: [not_a_family]")
    root = _docs(tmp_path, {"t.md": bad})
    assert any("unknown family_key 'not_a_family'" in e for e in _error_msgs(root))


def test_bad_enum_values_error(tmp_path):
    bad = _VALID_TECH.replace("category_group: Performance", "category_group: Bogus") \
                     .replace("status: stable", "status: nope")
    root = _docs(tmp_path, {"t.md": bad})
    errs = _error_msgs(root)
    assert any("category_group 'Bogus'" in e for e in errs)
    assert any("status 'nope'" in e for e in errs)


def test_broken_related_slug_error(tmp_path):
    bad = _VALID_TECH.replace("families: [all-native]", "families: [all-native]\nrelated: [does-not-exist]")
    root = _docs(tmp_path, {"t.md": bad})
    assert any("related slug 'does-not-exist'" in e for e in _error_msgs(root))


def test_related_slug_resolves_to_existing_technique(tmp_path):
    main = _VALID_TECH.replace("families: [all-native]", "families: [all-native]\nrelated: [other]")
    root = _docs(tmp_path, {"main.md": main, "other.md": _VALID_TECH})
    assert not any("related slug" in e for e in _error_msgs(root))


def test_bad_arxiv_format_error(tmp_path):
    bad = _VALID_TECH.replace('arxiv: "2406.01733"', 'arxiv: "not-an-arxiv-id"')
    root = _docs(tmp_path, {"t.md": bad})
    assert any("not a valid arxiv id" in e for e in _error_msgs(root))


def test_bad_model_engine_error(tmp_path):
    bad = _VALID_MODEL.replace("engine: native", "engine: tensorflow")
    root = _docs(tmp_path, models={"m.md": bad})
    assert any("spec.engine 'tensorflow'" in e for e in _error_msgs(root))


def test_unknown_key_and_missing_recommended_are_warnings(tmp_path):
    minimal = "---\ntype: technique\ntitle: X\ncategory_group: Quality\nstatus: stable\nfamilies: [flux]\nweird_key: 1\n---\nB."
    root = _docs(tmp_path, {"t.md": minimal})
    report = lint_docs(root)
    assert report.ok   # no errors
    warns = [w.message for w in report.warnings]
    assert any("no authors" in w for w in warns)
    assert any("no paper" in w for w in warns)
    assert any("unknown frontmatter key 'weird_key'" in w for w in warns)


def test_known_family_keys_includes_registry_and_sdxl():
    keys = known_family_keys()
    assert "flux" in keys and "wan" in keys and "sdxl" in keys


def test_cli_main_exit_code(tmp_path):
    _docs(tmp_path, {"bad.md": "---\ntype: technique\ntitle: X\nstatus: stable\n---\nB."})
    assert docs_lint_cli.main(["--docs-root", str(tmp_path / "docs")]) == 1
    ok_root = _docs(tmp_path / "ok", {"g.md": _VALID_TECH})
    assert docs_lint_cli.main(["--docs-root", str(ok_root)]) == 0
