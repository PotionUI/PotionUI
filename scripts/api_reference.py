from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DOCS_DIR = REPO_ROOT / "docs" / "api"
EXAMPLES_DIR = API_DOCS_DIR / "examples"

H1 = "\x23"
H2 = "\x23" * 2
H3 = "\x23" * 3
FENCE = "`" * 3

HTTP_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4, "WS": 5}
SOURCE_PUBLIC = "public"
SESSION_SOURCES = {"session_only", "default_deny"}
ENVELOPE = "APIResponse"

GROUPS = [
    (
        "generation",
        "Generation and media",
        "Starting, following, cancelling and fetching generations, uploads and media files, saved sessions and media tools.",
        ["Generation", "Media", "Media Tools", "Sessions", "Tab drafts", "Elixirs", "Automations"],
    ),
    (
        "presets-models",
        "Presets, forms and models",
        "What can be generated with: presets and their forms, models, backends and downloads.",
        [
            "Presets", "Forms", "Fields", "Models", "Model Collections", "Model Layouts", "Cloud", "Backends",
            "Downloads", "Providers", "Sampling", "Pipes", "Remote Model Sync", "Model Roots", "Compute Provisioning",
        ],
    ),
    (
        "library",
        "History, library and prompts",
        "Your own content: the library, kits, collections, tags, saved prompts and segments, phrasebook and workspaces.",
        [
            "Library", "Kits", "Collections", "Tags", "Prompts", "Phrasebook", "Segments", "Segment Templates",
            "Segment Categories", "Prompt resources", "Filters", "Formulas", "Workspaces", "Inspirations",
            "Notifications", "Keybindings", "Dictionaries", "Auto-organize",
        ],
    ),
    (
        "account",
        "Account, keys and MCP",
        "Signing in, API keys, MCP and users.",
        ["authentication", "API Keys", "MCP", "users", "user-groups", "Plans"],
    ),
    (
        "assistant",
        "Assistant and language models",
        "The chat assistant and the language-model configuration behind it.",
        ["Chat", "LLM", "LLM Tool Governance"],
    ),
]
SYSTEM_GROUP = ("system", "System", "Health, settings, plugins, recipes, documentation and statistics.")
ADMIN_GROUP = (
    "admin",
    "Administration",
    "Routes that need a key with the admin permission. Only an administrator can create such a key.",
)
SESSION_GROUP = (
    "session",
    "Browser session only",
    "Routes that never accept an API key. A key that calls one gets 403 key_not_allowed.",
)
OVERVIEW_ORDER = 40
CATEGORY = "API"
CATEGORY_ORDER = 90

PROBE_TIMEOUT_SECONDS = 600


@dataclass
class CheckReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def probe_environment(scratch: Path) -> Dict[str, str]:
    env = dict(os.environ)
    for sub in ("storage", "models", "recipes", "logs"):
        (scratch / sub).mkdir(parents=True, exist_ok=True)
    env["POTIONUI_DB_PATH"] = str(scratch / "db.sqlite")
    env["POTIONUI_STORAGE_PATH"] = str(scratch / "storage")
    env["POTIONUI_MODELS_DIR"] = str(scratch / "models")
    env["POTIONUI_RECIPES_DIR"] = str(scratch / "recipes")
    env["POTIONUI_LOG_DIR"] = str(scratch / "logs")
    env["HOME"] = str(scratch)
    env["USERPROFILE"] = str(scratch)
    env["POTIONUI_AUTH_SECRET_KEY"] = secrets.token_urlsafe(48)
    env["DEBUG"] = "false"
    env.pop("POTIONUI_EXTRA_PLUGIN_DIRS", None)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(REPO_ROOT), existing) if part)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def collect(repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="api-reference-") as scratch_name:
        scratch = Path(scratch_name)
        target = scratch / "schema.json"
        result = subprocess.run(
            [sys.executable, "-m", "src.bootstrap.api_schema", str(target)],
            cwd=str(repo_root),
            env=probe_environment(scratch),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0 or not target.exists():
            tail = "\n".join((result.stderr or result.stdout or "").strip().splitlines()[-15:])
            raise RuntimeError(f"could not build the app to read its routes:\n{tail}")
        return json.loads(target.read_text(encoding="utf-8"))


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def resolve(schema: Optional[Dict[str, Any]], components: Dict[str, Any]) -> Dict[str, Any]:
    schema = schema or {}
    seen = 0
    while "$ref" in schema and seen < 8:
        schema = components.get(ref_name(schema["$ref"]), {})
        seen += 1
    return schema


def type_of(schema: Optional[Dict[str, Any]]) -> str:
    if not schema:
        return "any"
    if "$ref" in schema:
        return ref_name(schema["$ref"])
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        names: List[str] = []
        for option in options:
            if option.get("type") == "null":
                continue
            name = type_of(option)
            if name not in names:
                names.append(name)
        return " or ".join(names) or "any"
    if "allOf" in schema and schema["allOf"]:
        return type_of(schema["allOf"][0])
    if "enum" in schema:
        return "one of " + ", ".join(str(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "array":
        return f"array of {type_of(schema.get('items'))}"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict) and extra:
            return f"object of {type_of(extra)}"
        return "object"
    if kind == "string" and schema.get("format") == "binary":
        return "file"
    if kind == "string" and schema.get("format"):
        return f"string ({schema['format']})"
    return kind or "any"


def limit_note(schema: Optional[Dict[str, Any]]) -> str:
    candidates = [schema or {}] + [option for option in (schema or {}).get("anyOf") or [] if option.get("type") != "null"]
    for candidate in candidates:
        low, high = candidate.get("minLength"), candidate.get("maxLength")
        if low is not None or high is not None:
            return f", {low if low is not None else 0} to {high} characters" if high is not None else f", at least {low} characters"
        low, high = candidate.get("minimum"), candidate.get("maximum")
        if low is not None and high is not None:
            return f", {low} to {high}"
        if low is not None:
            return f", at least {low}"
        if high is not None:
            return f", at most {high}"
    return ""


def default_note(schema: Optional[Dict[str, Any]]) -> str:
    if schema and "default" in schema:
        value = schema["default"]
        if value is None:
            return ""
        text = json.dumps(value) if not isinstance(value, str) else repr(value)
        if len(text) <= 40:
            return f", default {text}"
    return ""


def clean_text(value: Optional[str]) -> str:
    text = " ".join((value or "").split())
    return text.replace("<", "&lt;").replace(">", "&gt;")


def first_paragraph(value: Optional[str]) -> str:
    if not value:
        return ""
    block = re.split(r"\n\s*\n", value.strip(), maxsplit=1)[0]
    return clean_text(block)


def field_line(name: str, schema: Dict[str, Any], required: bool, description: Optional[str] = None) -> str:
    notes = [type_of(schema)]
    notes.append("required" if required else "optional")
    line = f"`{name}` ({', '.join(notes)}{limit_note(schema)}{default_note(schema)})"
    text = clean_text(description or schema.get("description"))
    return f"{line}: {text}" if text else line


def object_fields(schema: Dict[str, Any], components: Dict[str, Any]) -> List[str]:
    resolved = resolve(schema, components)
    properties = resolved.get("properties") or {}
    required = set(resolved.get("required") or [])
    return [field_line(name, prop, name in required) for name, prop in properties.items()]


def parameter_lines(operation: Dict[str, Any], components: Dict[str, Any], where: str) -> List[str]:
    lines = []
    for parameter in operation.get("parameters") or []:
        if parameter.get("in") != where:
            continue
        schema = parameter.get("schema") or {}
        lines.append(field_line(parameter["name"], schema, bool(parameter.get("required")), parameter.get("description")))
    return lines


def body_section(operation: Dict[str, Any], components: Dict[str, Any]) -> List[str]:
    body = operation.get("requestBody")
    if not body:
        return []
    content = body.get("content") or {}
    kind = next((name for name in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded") if name in content), None)
    if kind is None:
        return []
    schema = content[kind].get("schema") or {}
    label = {"application/json": "JSON body", "multipart/form-data": "Form upload (multipart)", "application/x-www-form-urlencoded": "Form body"}[kind]
    resolved = resolve(schema, components)
    fields = object_fields(schema, components)
    title = ref_name(schema["$ref"]) if "$ref" in schema else ""
    heading = f"{label}" + (f", `{title}`" if title and kind == "application/json" else "")
    required_note = "" if body.get("required", False) else " (optional)"
    if fields:
        return [f"{heading}{required_note}:", ""] + [f"- {line}" for line in fields]
    if resolved.get("type") == "object" or not resolved:
        return [f"{heading}{required_note}: a free-form object."]
    return [f"{heading}{required_note}: {type_of(schema)}."]


def response_section(operation: Dict[str, Any], components: Dict[str, Any]) -> List[str]:
    responses = operation.get("responses") or {}
    codes = sorted(code for code in responses if code.startswith("2"))
    if not codes:
        return []
    code = codes[0]
    response = responses[code]
    content = response.get("content") or {}
    if not content:
        return [f"Response `{code}`: no body."]
    media_type, media = next(iter(content.items()))
    schema = media.get("schema") or {}
    name = ref_name(schema["$ref"]) if "$ref" in schema else None
    if name == ENVELOPE:
        return [f"Response `{code}`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema."]
    if name:
        fields = object_fields(schema, components)
        if fields:
            return [f"Response `{code}`, `{name}`:", ""] + [f"- {line}" for line in fields]
        return [f"Response `{code}`: `{name}`."]
    if schema.get("type") == "array":
        return [f"Response `{code}`: {type_of(schema)}."]
    if schema.get("type") == "object" and schema.get("properties"):
        return [f"Response `{code}`:", ""] + [f"- {line}" for line in object_fields(schema, components)]
    if media_type != "application/json":
        return [f"Response `{code}`: `{media_type}` body."]
    return [f"Response `{code}`: the body is not described in the schema."]


def permission_line(row: Dict[str, Any]) -> str:
    source = row["source"]
    scopes = row["scopes"]
    if source == SOURCE_PUBLIC:
        return "No sign-in needed."
    if source in SESSION_SOURCES or not scopes:
        return "Browser session only. An API key gets `403 key_not_allowed`."
    names = " or ".join(f"`{scope}`" for scope in scopes)
    return f"Key permission: {names}."


def method_label(method: str) -> str:
    return "WS" if method == "WS" else method


@dataclass
class Entry:
    method: str
    path: str
    tag: str
    row: Dict[str, Any]
    operation: Dict[str, Any]


def build_entries(data: Dict[str, Any]) -> List[Entry]:
    paths = data["openapi"].get("paths") or {}
    entries: List[Entry] = []
    for row in data["routes"]:
        method, path = row["method"], row["path"]
        operation = (paths.get(path) or {}).get(method.lower())
        if operation is None:
            if method == "WS":
                operation = {"summary": "Open a WebSocket", "tags": ["WebSocket"]}
            else:
                continue
        tags = operation.get("tags") or ["Other"]
        entries.append(Entry(method, path, tags[0], row, operation))
    return entries


def group_of(entry: Entry) -> str:
    row = entry.row
    if row["source"] in SESSION_SOURCES:
        return SESSION_GROUP[0]
    if row["source"] != SOURCE_PUBLIC and row["scopes"] == ["admin"]:
        return ADMIN_GROUP[0]
    for key, _title, _intro, tags in GROUPS:
        if entry.tag in tags:
            return key
    return SYSTEM_GROUP[0]


def sort_key(entry: Entry):
    return (entry.path, HTTP_METHOD_ORDER.get(entry.method, 9))


def render_entry(entry: Entry, components: Dict[str, Any]) -> List[str]:
    operation = entry.operation
    lines = [f"{H3} {method_label(entry.method)} {entry.path}", ""]
    summary = clean_text(operation.get("summary"))
    detail = first_paragraph(operation.get("description"))
    lines.append(f"**{summary}**" if summary else "**Route**")
    if detail and detail.rstrip(".").lower() != summary.rstrip(".").lower():
        lines.append("")
        lines.append(detail)
    lines.append("")
    meta = [permission_line(entry.row)]
    if operation.get("deprecated"):
        meta.insert(0, "Deprecated.")
    lines.append(" ".join(meta))
    if entry.method == "WS":
        lines.append("")
        lines.append("Redeem a ticket from `POST /api/auth/ws-ticket` or send the key in the `Authorization` header when connecting.")
        lines.append("")
        return lines
    sections = [
        ("Path parameters", parameter_lines(operation, components, "path")),
        ("Query parameters", parameter_lines(operation, components, "query")),
        ("Headers", parameter_lines(operation, components, "header")),
    ]
    for title, items in sections:
        if items:
            lines.append("")
            lines.append(f"{title}:")
            lines.append("")
            lines.extend(f"- {item}" for item in items)
    body = body_section(operation, components)
    if body:
        lines.append("")
        lines.extend(body)
    response = response_section(operation, components)
    if response:
        lines.append("")
        lines.extend(response)
    lines.append("")
    return lines


def frontmatter(title: str, order: int) -> List[str]:
    return ["---", f'title: "{title}"', f"order: {order}", f"category: {CATEGORY}", f"category_order: {CATEGORY_ORDER}", "---", ""]


def page_name(key: str) -> str:
    return f"reference-{key}.md"


def render_group_page(key: str, title: str, intro: str, entries: List[Entry], components: Dict[str, Any], order: int) -> str:
    lines = frontmatter(f"Reference: {title}", order)
    lines += [f"{H1} Reference: {title}", "", intro, "", "Generated from the routes of the running application. Do not edit by hand.", ""]
    by_tag: Dict[str, List[Entry]] = {}
    for entry in sorted(entries, key=sort_key):
        by_tag.setdefault(entry.tag, []).append(entry)
    for tag in sorted(by_tag, key=str.lower):
        lines += [f"{H2} {tag}", ""]
        for entry in by_tag[tag]:
            lines += render_entry(entry, components)
    return "\n".join(lines).rstrip("\n") + "\n"


def render_overview(pages: List[Dict[str, Any]], total: int) -> str:
    lines = frontmatter("Endpoint reference", OVERVIEW_ORDER)
    lines += [
        f"{H1} Endpoint reference",
        "",
        "Every route of the HTTP API, grouped by area, with the key permission it needs, its parameters and its response. Generated from the routes of the running application. Do not edit by hand.",
        "",
        f"{H2} Interactive documentation",
        "",
        "Your instance serves interactive documentation of its own routes on the backend port: `/docs` (Swagger UI) and `/redoc`, with the raw schema at `/openapi.json`. In Swagger UI choose Authorize, paste an API key into the `ApiKey` box (a key starts with `pui_`; the page adds the Bearer prefix) and the Try it out buttons send it. Plugins that add routes show up there when they are enabled.",
        "",
        f"{H2} How to read an entry",
        "",
        "- Key permission: the permission an API key needs for that method and path. `read`, `generate`, `content`, `admin` and `mcp` are described on the Authentication page. `read` or `mcp` means either is enough. A key with `admin` also passes every `read`, `generate` and `content` check.",
        "- Browser session only: the route never accepts an API key. A key gets `403` with the error `key_not_allowed`.",
        "- No sign-in needed: the route answers without any credentials.",
        "- Most routes answer with the standard envelope `{\"success\": true, \"data\": ..., \"message\": null, \"error\": null}`. Where an entry says the shape of `data` is not described, the Generating and Content pages show the real shapes for the routes a script uses.",
        "- A request body that does not match the schema is refused with `422` and `{\"success\": false, \"error\": \"validation_error\", \"message\": \"Request validation failed\", \"detail\": [...]}`.",
        "",
        f"{H2} Pages",
        "",
        f"{total} routes in total.",
        "",
        "| Page | Routes | What is in it |",
        "| --- | --- | --- |",
    ]
    for page in pages:
        lines.append(f"| {page['title']} | {page['count']} | {page['intro']} |")
    lines.append("")
    return "\n".join(lines)


def render(data: Dict[str, Any]) -> Dict[str, str]:
    components = (data["openapi"].get("components") or {}).get("schemas") or {}
    entries = build_entries(data)
    buckets: Dict[str, List[Entry]] = {}
    for entry in entries:
        buckets.setdefault(group_of(entry), []).append(entry)
    ordered = [(key, title, intro) for key, title, intro, _tags in GROUPS]
    ordered += [SYSTEM_GROUP, ADMIN_GROUP, SESSION_GROUP]
    files: Dict[str, str] = {}
    pages: List[Dict[str, Any]] = []
    for index, (key, title, intro) in enumerate(ordered):
        group_entries = buckets.get(key, [])
        if not group_entries:
            continue
        files[page_name(key)] = render_group_page(key, title, intro, group_entries, components, OVERVIEW_ORDER + 1 + index)
        pages.append({"title": f"Reference: {title}", "count": len(group_entries), "intro": intro})
    files["reference.md"] = render_overview(pages, len(entries))
    return files


CAPTION = re.compile(r"^`examples/([A-Za-z0-9_.-]+)`$")


def embedded_examples(markdown: str) -> List[Dict[str, Any]]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    found = []
    index = 0
    while index < len(lines):
        match = CAPTION.match(lines[index].strip())
        if match and index + 1 < len(lines) and lines[index + 1].startswith(FENCE):
            start = index + 2
            end = start
            while end < len(lines) and not lines[end].startswith(FENCE):
                end += 1
            found.append({"file": match.group(1), "text": "\n".join(lines[start:end]), "line": index + 1})
            index = end
        index += 1
    return found


def example_files(examples_dir: Path = EXAMPLES_DIR) -> List[Path]:
    if not examples_dir.is_dir():
        return []
    return sorted(path for path in examples_dir.iterdir() if path.is_file() and path.suffix in (".sh", ".ps1", ".py"))


def example_embed_errors(api_dir: Path = API_DOCS_DIR) -> List[str]:
    errors: List[str] = []
    embedded = set()
    for page in sorted(api_dir.glob("*.md")):
        for block in embedded_examples(page.read_text(encoding="utf-8")):
            source = api_dir / "examples" / block["file"]
            label = f"{page.name}:{block['line']}"
            if not source.is_file():
                errors.append(f"{label}: examples/{block['file']} does not exist")
                continue
            embedded.add(block["file"])
            if source.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n") != block["text"].rstrip("\n"):
                errors.append(f"{label}: the code block no longer matches examples/{block['file']}")
    for path in example_files(api_dir / "examples"):
        if path.name not in embedded:
            errors.append(f"examples/{path.name}: not shown on any page")
    return errors


def display(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def drift_errors(files: Dict[str, str], api_dir: Path = API_DOCS_DIR) -> List[str]:
    errors: List[str] = []
    for name, text in files.items():
        target = api_dir / name
        if not target.exists():
            errors.append(f"{display(target)}: missing - run `python scripts/api_reference.py`")
        elif target.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            errors.append(f"{display(target)}: out of date - run `python scripts/api_reference.py`")
    for stale in sorted(api_dir.glob("reference*.md")):
        if stale.name not in files:
            errors.append(f"{display(stale)}: no longer generated - delete it")
    return errors


def check(repo_root: Path = REPO_ROOT) -> CheckReport:
    report = CheckReport()
    try:
        files = render(collect(repo_root))
    except Exception as exc:
        report.errors.append(f"docs/api: could not generate the endpoint reference: {exc}")
        return report
    report.errors.extend(drift_errors(files, repo_root / "docs" / "api"))
    report.errors.extend(example_embed_errors(repo_root / "docs" / "api"))
    return report


def write(files: Dict[str, str], api_dir: Path = API_DOCS_DIR) -> List[Path]:
    api_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in files.items():
        target = api_dir / name
        target.write_bytes(text.encode("utf-8"))
        written.append(target)
    for stale in api_dir.glob("reference*.md"):
        if stale.name not in files:
            stale.unlink()
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the endpoint reference under docs/api from the app's own routes.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if the committed reference or the embedded examples are out of date.")
    args = parser.parse_args(argv)
    if args.check:
        report = check(REPO_ROOT)
        for warning in report.warnings:
            print(f"WARN {warning}")
        for error in report.errors:
            print(f"ERROR {error}")
        if report.errors:
            return 1
        print("docs/api reference and examples are up to date.")
        return 0
    errors = example_embed_errors()
    for path in write(render(collect(REPO_ROOT))):
        print(f"wrote {display(path)}")
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
