import re

import pytest

from scripts.api_reference import API_DOCS_DIR, EXAMPLES_DIR, example_embed_errors, example_files
from tests.docs.api_example_harness import KEY_SCOPES, ORDER
from tests.docs.example_checks import RouteTable, calls_for, validate

SESSION_ROUTE = ("POST", "/api/sessions/{session_id}/generate")

EXPECTED_OUTPUT = {
    "first_call": r"Fake Studio",
    "generate_and_wait": r"saved \S+\.png",
    "follow_websocket": r"generation_complete",
    "upload_and_use": r"saved \S+\.png",
    "download_history": r"saved history[/\\]\S+\.png",
    "make_kit": r"kits\.mara -> .*sailor with a red scarf",
    "generate_from_session": r"saved \S+\.png",
}
SAVES_FILES = ("generate_and_wait", "upload_and_use", "download_history", "generate_from_session")


def example_result(example_results, name):
    result = example_results[name]
    if "skipped" in result:
        pytest.skip(result["skipped"])
    return result


def example_paths():
    return sorted(example_files(EXAMPLES_DIR))


@pytest.mark.slow
@pytest.mark.parametrize("name", ORDER)
def test_the_python_example_completes_against_the_app(example_results, name):
    result = example_result(example_results, name)

    assert result["ok"], result["error"]
    assert re.search(EXPECTED_OUTPUT[name], result["output"]), result["output"]


@pytest.mark.slow
@pytest.mark.parametrize("name", SAVES_FILES)
def test_the_examples_that_download_leave_a_real_image_on_disk(example_results, name):
    result = example_result(example_results, name)

    assert any(file.endswith(".png") for file in result["files"]), result["files"]


@pytest.mark.slow
def test_the_running_app_serves_interactive_docs_without_login_that_accept_a_key(example_results):
    documentation = example_results["documentation"]

    assert documentation["status"] == {"/docs": 200, "/redoc": 200, "/openapi.json": 200}
    assert "ApiKey" in documentation["schemes"]
    assert {"ApiKey": []} in documentation["start_security"]


def test_the_exercised_examples_are_every_python_example():
    on_disk = {path.stem for path in example_files(EXAMPLES_DIR) if path.suffix == ".py"}

    assert on_disk == set(ORDER)


def test_every_example_has_a_script_for_each_shell():
    names = {path.stem for path in example_files(EXAMPLES_DIR) if path.suffix == ".py"} - {"follow_websocket"}

    for name in names:
        assert (EXAMPLES_DIR / f"{name}.sh").is_file(), name
        assert (EXAMPLES_DIR / f"{name}.ps1").is_file(), name


@pytest.mark.slow
@pytest.mark.parametrize("path", example_paths(), ids=lambda path: path.name)
def test_each_example_calls_only_routes_that_exist_with_fields_the_route_accepts(route_snapshot, path):
    table = RouteTable(route_snapshot)
    if path.stem == "generate_from_session" and table.find(*SESSION_ROUTE) is None:
        pytest.skip("POST /api/sessions/{session_id}/generate is not in this build")
    calls = calls_for(path.name, path.read_text(encoding="utf-8"))

    assert calls, f"{path.name} makes no recognisable request"
    problems = [problem for call in calls for problem in validate(call, table, frozenset(KEY_SCOPES[path.stem]))]
    assert not problems, "\n".join(problems)


def test_every_example_is_shown_verbatim_on_a_page():
    assert example_embed_errors() == []


GUIDE_PAGES = ("overview", "quickstart", "authentication", "generating", "sessions", "content", "mcp")
GUIDE_SCOPES = frozenset({"read", "generate", "content", "mcp"})
FENCED = re.compile(r"^```(\w+)\n(.*?)^```", re.S | re.M)
SHELL_LANGUAGES = {"bash": "block.sh", "powershell": "block.ps1", "python": "block.py"}


def guide_blocks(page):
    text = (API_DOCS_DIR / f"{page}.md").read_text(encoding="utf-8")
    return [(match.group(1), match.group(2)) for match in FENCED.finditer(text) if match.group(1) in SHELL_LANGUAGES]


def guide_cases():
    cases = []
    for page in GUIDE_PAGES:
        blocks = guide_blocks(page)
        first_powershell = next((text for language, text in blocks if language == "powershell"), "")
        for position, (language, text) in enumerate(blocks):
            body = text if language != "powershell" else first_powershell + "\n" + text
            cases.append(pytest.param(page, language, body, id=f"{page}-{position}-{language}"))
    return cases


@pytest.mark.slow
@pytest.mark.parametrize("page,language,text", guide_cases())
def test_every_request_shown_in_the_guide_is_real(route_snapshot, page, language, text):
    table = RouteTable(route_snapshot)
    sessions_built = table.find(*SESSION_ROUTE) is not None
    problems = []
    for call in calls_for(SHELL_LANGUAGES[language], text):
        if call.path == "/api/mcp" or (call.path.startswith("/api/sessions") and not sessions_built):
            continue
        problems.extend(validate(call, table, GUIDE_SCOPES))

    assert not problems, "\n".join(problems)


def test_the_guide_pages_exist_with_the_front_matter_the_docs_index_reads():
    for page in GUIDE_PAGES:
        text = (API_DOCS_DIR / f"{page}.md").read_text(encoding="utf-8")

        assert text.startswith("---\n"), page
        header = text.split("---", 2)[1]
        assert "title:" in header and "order:" in header and "category: API" in header, page
