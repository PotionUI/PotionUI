import yaml

from scripts import api_reference as ar

SCHEMAS = {
    "APIResponse": {"properties": {"success": {"type": "boolean"}, "data": {}}, "required": ["success"]},
    "StartRequest": {
        "properties": {
            "preset_id": {"type": "string"},
            "prompt": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": ""},
            "key": {"anyOf": [{"type": "string", "maxLength": 200, "minLength": 1}, {"type": "null"}]},
            "form_data": {"type": "object", "additionalProperties": True, "default": {}},
            "prompts": {"anyOf": [{"type": "array", "items": {"$ref": "#/components/schemas/Pair"}}, {"type": "null"}]},
        },
        "required": ["preset_id"],
    },
    "Pair": {"properties": {"positive": {"type": "string"}}},
    "Mood": {"properties": {"label": {"type": "string", "description": "What it is called."}, "level": {"type": "integer"}}, "required": ["label"]},
}

OPERATIONS = {
    "/api/generations/start": {
        "post": {
            "tags": ["Generation"],
            "summary": "Start Generation",
            "description": "Starts a generation.\n\nSecond paragraph is dropped.",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/StartRequest"}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/APIResponse"}}}}},
        }
    },
    "/api/generations/{generation_id}/status": {
        "get": {
            "tags": ["Generation"],
            "summary": "Get Generation Status",
            "parameters": [{"name": "generation_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/APIResponse"}}}}},
        }
    },
    "/api/media/files/{file_id}": {
        "get": {
            "tags": ["Media"],
            "summary": "Serve File by ID",
            "parameters": [
                {"name": "file_id", "in": "path", "required": True, "schema": {"type": "string"}},
                {"name": "width", "in": "query", "required": False, "schema": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
            ],
            "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
        }
    },
    "/api/users": {"get": {"tags": ["users"], "summary": "List Users", "deprecated": True, "responses": {"200": {"description": "ok"}}}},
    "/api/keys": {
        "get": {"tags": ["API Keys"], "summary": "List keys <script>", "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Mood"}}}}}}
    },
    "/api/admin/backups": {"get": {"tags": ["Backups"], "summary": "List backups", "responses": {"204": {"description": "none"}}}},
    "/health": {"get": {"tags": ["System & Health"], "summary": "Health Check", "responses": {"200": {"content": {"application/json": {"schema": {}}}}}}},
}

ROUTES = [
    {"method": "POST", "path": "/api/generations/start", "scopes": ["generate"], "source": "marker"},
    {"method": "GET", "path": "/api/generations/{generation_id}/status", "scopes": ["read"], "source": "marker"},
    {"method": "GET", "path": "/api/media/files/{file_id}", "scopes": ["read", "mcp"], "source": "marker"},
    {"method": "GET", "path": "/api/users", "scopes": ["admin"], "source": "admin_dependency"},
    {"method": "GET", "path": "/api/keys", "scopes": None, "source": "session_only"},
    {"method": "GET", "path": "/api/admin/backups", "scopes": ["admin"], "source": "admin_dependency"},
    {"method": "GET", "path": "/health", "scopes": None, "source": "public"},
    {"method": "WS", "path": "/ws/generation", "scopes": ["read"], "source": "marker"},
]

DATA = {"openapi": {"paths": OPERATIONS, "components": {"schemas": SCHEMAS}}, "routes": ROUTES}


def rendered():
    return ar.render(DATA)


def test_routes_are_split_into_the_pages_a_reader_looks_for():
    files = rendered()

    assert "### POST /api/generations/start" in files["reference-generation.md"]
    assert "### GET /api/media/files/{file_id}" in files["reference-generation.md"]
    assert "### GET /api/users" in files["reference-admin.md"]
    assert "### GET /api/admin/backups" in files["reference-admin.md"]
    assert "### GET /api/keys" in files["reference-session.md"]
    assert "### GET /health" in files["reference-system.md"]
    assert "reference.md" in files


def test_a_route_with_no_scope_row_is_not_claimed_by_a_group_it_does_not_belong_to():
    files = rendered()

    assert "/api/keys" not in files["reference-generation.md"]
    assert "/api/users" not in files["reference-generation.md"]


def test_each_entry_states_the_key_permission_it_needs():
    text = rendered()["reference-generation.md"]

    assert "Key permission: `generate`." in text
    assert "Key permission: `read`." in text
    assert "Key permission: `read` or `mcp`." in text


def test_a_session_only_route_says_so_and_a_public_one_needs_no_sign_in():
    files = rendered()

    assert "Browser session only. An API key gets `403 key_not_allowed`." in files["reference-session.md"]
    assert "No sign-in needed." in files["reference-system.md"]


def test_a_deprecated_route_is_marked():
    assert "Deprecated. Key permission: `admin`." in rendered()["reference-admin.md"]


def test_body_fields_show_type_requirement_limits_and_defaults():
    text = rendered()["reference-generation.md"]

    assert "- `preset_id` (string, required)" in text
    assert "- `prompt` (string, optional, default '')" in text
    assert "- `key` (string, optional, 1 to 200 characters)" in text
    assert "- `form_data` (object, optional, default {})" in text
    assert "- `prompts` (array of Pair, optional)" in text


def test_parameters_are_listed_by_where_they_go():
    text = rendered()["reference-generation.md"]

    assert "Path parameters:\n\n- `generation_id` (string, required)" in text
    assert "Query parameters:\n\n- `width` (integer, optional)" in text


def test_the_standard_envelope_is_named_and_an_untyped_body_is_not_invented():
    text = rendered()["reference-generation.md"]

    assert "the standard envelope" in text
    assert "The shape of `data` is not described in the schema." in text
    assert "the body is not described in the schema." in text


def test_a_typed_response_lists_its_fields_with_their_description():
    text = rendered()["reference-session.md"]

    assert "Response `200`, `Mood`:" in text
    assert "- `label` (string, required): What it is called." in text


def test_a_response_without_a_body_is_stated():
    assert "Response `204`: no body." in rendered()["reference-admin.md"]


def test_only_the_first_paragraph_of_a_description_is_kept_and_markup_is_escaped():
    files = rendered()

    assert "Starts a generation." in files["reference-generation.md"]
    assert "Second paragraph" not in files["reference-generation.md"]
    assert "List keys &lt;script&gt;" in files["reference-session.md"]


def test_a_websocket_entry_points_at_the_ticket_route():
    text = rendered()["reference-system.md"]

    assert "### WS /ws/generation" in text
    assert "`POST /api/auth/ws-ticket`" in text


def test_the_overview_counts_the_routes_and_mentions_interactive_docs():
    overview = rendered()["reference.md"]

    assert f"{len(ROUTES)} routes in total." in overview
    assert "`/docs`" in overview and "`/redoc`" in overview and "`/openapi.json`" in overview
    assert "`ApiKey`" in overview


def test_every_page_has_front_matter_the_docs_index_can_read():
    for name, text in rendered().items():
        header = text.split("---", 2)[1]
        meta = yaml.safe_load(header)

        assert meta["category"] == "API", name
        assert isinstance(meta["order"], int), name
        assert meta["title"], name


def test_rendering_is_deterministic():
    assert rendered() == rendered()


def test_drift_is_reported_for_changed_missing_and_stale_files(tmp_path):
    files = {"reference.md": "a\n", "reference-x.md": "b\n"}
    (tmp_path / "reference.md").write_text("changed\n")
    (tmp_path / "reference-old.md").write_text("old\n")

    messages = ar.drift_errors(files, tmp_path)

    assert any("reference.md: out of date" in m for m in messages)
    assert any("reference-x.md: missing" in m for m in messages)
    assert any("reference-old.md: no longer generated" in m for m in messages)


def test_a_clean_tree_has_no_drift(tmp_path):
    files = {"reference.md": "a\n"}
    (tmp_path / "reference.md").write_text("a\n")

    assert ar.drift_errors(files, tmp_path) == []


def write_page(api_dir, body):
    (api_dir / "page.md").write_text(body, encoding="utf-8")


def test_an_embedded_example_that_matches_its_file_is_accepted(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "one.py").write_text("print(1)\nprint(2)\n")
    write_page(tmp_path, "Text\n\n`examples/one.py`\n```python\nprint(1)\nprint(2)\n```\n")

    assert ar.example_embed_errors(tmp_path) == []


def test_an_embedded_example_that_drifted_is_reported(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "one.py").write_text("print(1)\nprint(3)\n")
    write_page(tmp_path, "`examples/one.py`\n```python\nprint(1)\nprint(2)\n```\n")

    errors = ar.example_embed_errors(tmp_path)

    assert len(errors) == 1 and "no longer matches examples/one.py" in errors[0]


def test_an_embedded_example_whose_file_is_gone_is_reported(tmp_path):
    (tmp_path / "examples").mkdir()
    write_page(tmp_path, "`examples/gone.py`\n```python\nprint(1)\n```\n")

    assert "examples/gone.py does not exist" in ar.example_embed_errors(tmp_path)[0]


def test_an_example_file_that_no_page_shows_is_reported(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "lonely.sh").write_text("echo hi\n")
    write_page(tmp_path, "Nothing here.\n")

    assert ar.example_embed_errors(tmp_path) == ["examples/lonely.sh: not shown on any page"]


def test_windows_line_endings_in_either_file_do_not_count_as_drift(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "one.sh").write_bytes(b"echo a\r\necho b\r\n")
    write_page(tmp_path, "`examples/one.sh`\n```bash\necho a\necho b\n```\n")

    assert ar.example_embed_errors(tmp_path) == []
