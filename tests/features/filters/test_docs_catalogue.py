import re
from pathlib import Path

from src.platform.imaging.filters import CORE_OPS
from src.platform.imaging.filters.ops import MAX_STEPS

DOC = Path(__file__).resolve().parents[3] / "docs" / "filters.md"


def number(value) -> str:
    return f"{value:g}"


def describe(spec) -> str:
    if not spec.params:
        return "none"
    if all(param.type == "curve" for param in spec.params):
        names = ", ".join(f"`{param.id}`" for param in spec.params)
        return f"{names}: each a curve of 2..16 points (default `[[0,0],[1,1]]`)"
    return ", ".join(
        f"`{param.id}` {number(param.min)}..{number(param.max)} ({number(param.default)})" for param in spec.params
    )


def documented_rows():
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- filter-ops:start -->")[1].split("<!-- filter-ops:end -->")[0]
    rows = {}
    for line in block.strip().splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = (cells[1], cells[2])
    return rows


def test_every_core_op_is_documented_with_its_kind_and_params():
    rows = documented_rows()

    assert set(rows) == set(CORE_OPS)
    for op_id, spec in CORE_OPS.items():
        assert rows[op_id] == (spec.kind, describe(spec)), op_id


def test_documented_limits_match_the_code():
    text = DOC.read_text(encoding="utf-8")

    assert f"at most {MAX_STEPS} steps" in text
    assert re.search(r"`filter.yml` 64 KB, `lut.cube` 8 MB", text)


def test_every_lint_rule_in_the_docs_exists_in_the_linter():
    text = DOC.read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `filter\.([a-z_]+)` \|", text, flags=re.M))
    source = (Path(__file__).resolve().parents[3] / "src" / "features" / "filters" / "schema.py").read_text(encoding="utf-8")
    ops_source = (
        Path(__file__).resolve().parents[3] / "src" / "platform" / "imaging" / "filters" / "ops.py"
    ).read_text(encoding="utf-8")
    lint_source = (Path(__file__).resolve().parents[3] / "src" / "features" / "filters" / "lint.py").read_text(encoding="utf-8")
    catalog_source = (Path(__file__).resolve().parents[3] / "src" / "features" / "filters" / "catalog.py").read_text(encoding="utf-8")
    known = set()
    for body in (source, ops_source, lint_source, catalog_source):
        known |= set(re.findall(r'(?:error|warning)\(\s*"([a-z_]+)"', body))
        known |= set(re.findall(r'RULE_[A-Z_]+ = "([a-z_]+)"', body))
        known |= set(re.findall(r'Finding\(NOTE, "([a-z_]+)"', body))

    assert documented <= known
    assert known <= documented
