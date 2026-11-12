from scripts.api_reference import API_DOCS_DIR, drift_errors, render
from tests.docs.api_example_harness import ROOT

GOLDEN = ROOT / "tests" / "golden" / "api_key_scopes.tsv"
READ_METHODS = ("GET", "WS")


def golden_rows():
    lines = GOLDEN.read_text(encoding="utf-8").replace("\r\n", "\n").strip().split("\n")[1:]
    rows = {}
    for line in lines:
        method, path, read, write, source = line.split("\t")
        cell = read if method in READ_METHODS else write
        rows[(method, path)] = (cell, source)
    return rows


def test_the_reference_agrees_with_the_golden_scope_table(route_snapshot):
    expected = golden_rows()
    seen = {}
    for row in route_snapshot["routes"]:
        cell = "+".join(row["scopes"]) if row["scopes"] else "-"
        seen[(row["method"], row["route_path"])] = (cell, row["source"])

    assert sorted(seen) == sorted(expected)
    assert [key for key in expected if seen[key] != expected[key]] == []


def test_the_committed_reference_is_what_the_app_generates_today(route_snapshot):
    assert drift_errors(render(route_snapshot), API_DOCS_DIR) == []


def test_the_schema_declares_the_key_scheme_so_the_authorize_button_accepts_a_key(route_snapshot):
    schemes = route_snapshot["openapi"]["components"]["securitySchemes"]
    protected = route_snapshot["openapi"]["paths"]["/api/generations/start"]["post"]["security"]

    assert schemes["ApiKey"]["scheme"] == "bearer"
    assert {"ApiKey": []} in protected


def test_the_documentation_pages_open_without_signing_in(route_snapshot):
    public = {row["path"] for row in route_snapshot["routes"] if row["source"] == "public"}

    assert {"/docs", "/redoc", "/openapi.json"} <= public


def test_every_route_a_key_can_use_that_the_schema_describes_appears_in_the_reference(route_snapshot):
    files = render(route_snapshot)
    text = "\n".join(files.values())
    described = route_snapshot["openapi"]["paths"]

    for row in route_snapshot["routes"]:
        if row["scopes"] and row["path"] in described:
            assert f"### {row['method']} {row['path']}\n" in text, (row["method"], row["path"])
