import json

import pytest

from tests.docs.example_checks import RouteTable, calls_for, validate

START = {
    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Start"}}}},
}
UPLOAD = {
    "requestBody": {"content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/Upload"}}}},
}
HISTORY = {"parameters": [{"name": "limit", "in": "query"}, {"name": "status", "in": "query"}]}

DATA = {
    "openapi": {
        "paths": {
            "/api/generations/start": {"post": START},
            "/api/generations/{generation_id}/status": {"get": {}},
            "/api/generations/history": {"get": HISTORY},
            "/api/media/upload": {"post": UPLOAD},
            "/api/keys": {"get": {}},
        },
        "components": {
            "schemas": {
                "Start": {
                    "properties": {"preset_id": {}, "mode": {}, "prompt": {}, "form_data": {}},
                    "required": ["preset_id"],
                },
                "Upload": {"properties": {"file": {}}, "required": ["file"]},
            }
        },
    },
    "routes": [
        {"method": "POST", "path": "/api/generations/start", "scopes": ["generate"], "source": "marker"},
        {"method": "GET", "path": "/api/generations/{generation_id}/status", "scopes": ["read"], "source": "marker"},
        {"method": "GET", "path": "/api/generations/history", "scopes": ["read"], "source": "marker"},
        {"method": "POST", "path": "/api/media/upload", "scopes": ["generate"], "source": "marker"},
        {"method": "GET", "path": "/api/keys", "scopes": None, "source": "session_only"},
    ],
}
TABLE = RouteTable(DATA)
BOTH = frozenset({"read", "generate"})

BASH = (
    "#!/usr/bin/env bash\n"
    'BASE="${POTIONUI_URL:-http://localhost:26730}"\n'
    'curl -sS -X POST "$BASE/api/generations/start" \\\n'
    '  -H "Authorization: Bearer $POTIONUI_KEY" \\\n'
    '  -H "Content-Type: application/json" \\\n'
    "  -d '{\"preset_id\": \"'\"$POTIONUI_PRESET\"'\", \"mode\": \"txt2img\", \"prompt\": \"a lighthouse\", \"form_data\": '\"$FORM\"'}'\n"
    'curl -sS "$BASE/api/generations/$ID/status" -H "Authorization: Bearer $POTIONUI_KEY" | jq -r \'.data.status\'\n'
    'curl -sS "$BASE/api/generations/history?status=completed&limit=20" -H "Authorization: Bearer $POTIONUI_KEY"\n'
    'curl -sS -X POST "$BASE/api/media/upload" -H "Authorization: Bearer $POTIONUI_KEY" -F "file=@$1"\n'
)

POWERSHELL = (
    '$base = "http://localhost:26730"\n'
    '$headers = @{ Authorization = "Bearer $($env:POTIONUI_KEY)" }\n'
    '$body = @"\n'
    '{"preset_id": "$($env:POTIONUI_PRESET)", "mode": "txt2img", "prompt": "a lighthouse", "form_data": $form}\n'
    '"@\n'
    '$started = Invoke-RestMethod -Method Post -Uri "$base/api/generations/start" -Headers $headers -ContentType "application/json" -Body $body\n'
    '$state = (Invoke-RestMethod -Uri "$base/api/generations/$id/status" -Headers $headers).data\n'
    '$page = (Invoke-RestMethod -Uri "$base/api/generations/history?status=completed&limit=20" -Headers $headers).data\n'
    '$uploaded = curl.exe -sS -X POST "$base/api/media/upload" -H "Authorization: Bearer $($env:POTIONUI_KEY)" -F "file=@$($args[0])" | ConvertFrom-Json\n'
)

PYTHON = (
    "import os\n"
    "import httpx\n"
    "\n"
    'client = httpx.Client(base_url=os.environ["POTIONUI_URL"], headers={"Authorization": "Bearer " + os.environ["POTIONUI_KEY"]})\n'
    'started = client.post("/api/generations/start", json={"preset_id": "x", "mode": "txt2img", "prompt": "a", "form_data": {}})\n'
    'state = client.get(f"/api/generations/{started}/status")\n'
    'page = client.get("/api/generations/history", params={"status": "completed", "limit": 20})\n'
    'uploaded = client.post("/api/media/upload", files={"file": ("a.png", b"", "image/png")})\n'
)

SAMPLES = {"sample.sh": BASH, "sample.ps1": POWERSHELL, "sample.py": PYTHON}


def problems_of(name, text, scopes=BOTH):
    return [problem for call in calls_for(name, text) for problem in validate(call, TABLE, scopes)]


@pytest.mark.parametrize("name", SAMPLES)
def test_a_correct_example_has_four_recognised_requests_and_no_problems(name):
    calls = calls_for(name, SAMPLES[name])

    assert sorted((call.method, call.path) for call in calls) == sorted([
        ("POST", "/api/generations/start"),
        ("GET", "/api/generations/X/status"),
        ("GET", "/api/generations/history"),
        ("POST", "/api/media/upload"),
    ])
    assert problems_of(name, SAMPLES[name]) == []


@pytest.mark.parametrize("name", SAMPLES)
def test_a_changed_path_is_reported_as_a_route_that_does_not_exist(name):
    broken = SAMPLES[name].replace("/api/generations/start", "/api/generations/begin")

    assert any("no such route" in problem for problem in problems_of(name, broken))


@pytest.mark.parametrize("name", SAMPLES)
def test_a_body_field_the_route_does_not_know_is_reported(name):
    broken = SAMPLES[name].replace('"prompt"', '"promt"', 1)

    assert any("unknown body field promt" in problem for problem in problems_of(name, broken))


@pytest.mark.parametrize("name", SAMPLES)
def test_a_missing_required_body_field_is_reported(name):
    broken = SAMPLES[name].replace('"preset_id"', '"preset"', 1)

    assert any("missing required body field preset_id" in problem for problem in problems_of(name, broken))


@pytest.mark.parametrize("name", SAMPLES)
def test_an_unknown_query_parameter_is_reported(name):
    broken = SAMPLES[name].replace("limit=20", "page_size=20").replace('"limit"', '"page_size"')

    assert any("unknown query parameter page_size" in problem for problem in problems_of(name, broken))


@pytest.mark.parametrize("name", SAMPLES)
def test_a_key_that_lacks_the_permission_is_reported(name):
    assert any("needs" in problem for problem in problems_of(name, SAMPLES[name], frozenset({"read"})))


@pytest.mark.parametrize("name", ["sample.sh", "sample.ps1"])
def test_a_request_without_the_bearer_header_is_reported(name):
    broken = SAMPLES[name].replace("Authorization", "X-Other")

    assert any("no Authorization: Bearer header" in problem for problem in problems_of(name, broken))


def test_a_route_a_key_cannot_call_is_reported():
    text = 'curl -sS "$BASE/api/keys" -H "Authorization: Bearer $POTIONUI_KEY"\n'

    assert any("a key cannot call this route" in problem for problem in problems_of("keys.sh", text, frozenset({"admin"})))


def test_a_wrong_http_method_is_reported_as_no_such_route():
    broken = BASH.replace('-X POST "$BASE/api/generations/start"', '-X PUT "$BASE/api/generations/start"')

    assert any("no such route" in problem for problem in problems_of("sample.sh", broken))


def test_a_json_body_without_the_content_type_is_reported():
    broken = BASH.replace('  -H "Content-Type: application/json" \\\n', "")

    assert any("Content-Type" in problem for problem in problems_of("sample.sh", broken))


def test_an_upload_field_the_route_does_not_know_is_reported():
    broken = BASH.replace('-F "file=@$1"', '-F "image=@$1"')

    assert any("unknown form field image" in problem for problem in problems_of("sample.sh", broken))


def test_the_body_of_a_bash_example_is_read_as_json():
    call = calls_for("sample.sh", BASH)[0]

    assert call.json_body["mode"] == "txt2img"
    assert call.json_body["form_data"] == {}
    assert json.dumps(call.json_keys)
