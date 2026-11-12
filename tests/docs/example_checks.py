from __future__ import annotations

import ast
import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

JSON_TYPE = "application/json"
METHODS_WITH_BODY = ("POST", "PUT", "PATCH")
BASE_PREFIXES = ("${BASE}", "$BASE", "$base", "$($env:POTIONUI_URL)", "$env:POTIONUI_URL", "${POTIONUI_URL}", "$POTIONUI_URL")
HTTP_VERBS = ("get", "post", "put", "patch", "delete")
VARIABLE = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)+|\$env:\w+|\$[A-Za-z_]\w*")
SCOPE_IMPLIED = {"admin": {"read", "generate", "content"}}


@dataclass
class Call:
    language: str
    method: str
    path: str
    line: str
    query: List[str] = field(default_factory=list)
    headers: List[str] = field(default_factory=list)
    content_type: Optional[str] = None
    json_body: Optional[Any] = None
    json_keys: Optional[List[str]] = None
    form_fields: Optional[List[str]] = None
    problems: List[str] = field(default_factory=list)


class RouteTable:
    def __init__(self, data: Dict[str, Any]):
        self.paths = data["openapi"].get("paths") or {}
        self.components = (data["openapi"].get("components") or {}).get("schemas") or {}
        self.scopes = {(row["method"], row["path"]): row["scopes"] for row in data["routes"]}
        self.patterns: List[Tuple[re.Pattern, str, str]] = []
        for (method, path) in self.scopes:
            pattern = re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")
            self.patterns.append((pattern, method, path))
        self.patterns.sort(key=lambda item: -len(re.sub(r"\{[^}]+\}", "", item[2])))

    def find(self, method: str, concrete: str) -> Optional[str]:
        for pattern, route_method, path in self.patterns:
            if route_method == method and pattern.match(concrete):
                return path
        return None

    def operation(self, method: str, path: str) -> Dict[str, Any]:
        return (self.paths.get(path) or {}).get(method.lower()) or {}

    def schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        seen = 0
        while "$ref" in schema and seen < 8:
            schema = self.components.get(schema["$ref"].rsplit("/", 1)[-1], {})
            seen += 1
        return schema

    def body_properties(self, method: str, path: str, content_type: str) -> Optional[Tuple[List[str], List[str]]]:
        body = self.operation(method, path).get("requestBody") or {}
        content = (body.get("content") or {}).get(content_type)
        if content is None:
            return None
        schema = self.schema(content.get("schema") or {})
        return list((schema.get("properties") or {}).keys()), list(schema.get("required") or [])

    def query_names(self, method: str, path: str) -> List[str]:
        return [p["name"] for p in self.operation(method, path).get("parameters") or [] if p.get("in") == "query"]


def concretize(raw: str) -> Tuple[str, List[str]]:
    for prefix in BASE_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = re.sub(r"^https?://[^/]+", "", raw)
    path, _, query = raw.partition("?")
    path = VARIABLE.sub("X", path)
    names = [pair.split("=", 1)[0] for pair in query.split("&") if pair]
    return path, names


def normalise_json(text: str) -> Optional[Any]:
    text = re.sub(r'(?<=: )\$\{?\w+\}?(?=[,}\]])', "{}", text)
    text = VARIABLE.sub("X", text)
    try:
        return json.loads(text)
    except ValueError:
        return None


def shell_tokens(text: str) -> List[str]:
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if not line.startswith("#!")]
    joined = re.sub(r"\\\n\s*", " ", "\n".join(lines))
    lexer = shlex.shlex(joined, posix=True, punctuation_chars="|;&()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def curl_calls(text: str, language: str) -> List[Call]:
    tokens = shell_tokens(text)
    calls: List[Call] = []
    index = 0
    while index < len(tokens):
        if tokens[index] not in ("curl", "curl.exe"):
            index += 1
            continue
        end = index + 1
        while end < len(tokens) and tokens[end] not in ("curl", "curl.exe") and not re.fullmatch(r"[|;&()<>]+", tokens[end]):
            end += 1
        calls.append(parse_curl(tokens[index + 1:end], language))
        index = end
    return calls


def parse_curl(args: Sequence[str], language: str) -> Call:
    method = None
    headers: List[str] = []
    data: Optional[str] = None
    form: List[str] = []
    url = None
    skip_next = False
    options_with_value = {"-X", "--request", "-H", "--header", "-d", "--data", "--data-binary", "--json", "-F", "--form", "-o", "--output"}
    for position, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        value = args[position + 1] if position + 1 < len(args) else ""
        if arg in ("-X", "--request"):
            method = value.upper()
            skip_next = True
        elif arg in ("-H", "--header"):
            headers.append(value.split(":", 1)[0].strip().lower() + ":" + value.split(":", 1)[-1].strip().lower())
            skip_next = True
        elif arg in ("-d", "--data", "--data-binary", "--json"):
            data = value
            skip_next = True
        elif arg in ("-F", "--form"):
            form.append(value.split("=", 1)[0])
            skip_next = True
        elif arg in options_with_value:
            skip_next = True
        elif not arg.startswith("-") and url is None:
            url = arg
    path, query = concretize(url or "")
    if method is None:
        method = "POST" if (data is not None or form) else "GET"
    call = Call(language, method, path, " ".join(args), query=query, headers=headers)
    if data is not None:
        call.json_body = normalise_json(data)
        if call.json_body is None:
            call.problems.append("the JSON body does not parse")
        elif isinstance(call.json_body, dict):
            call.json_keys = list(call.json_body.keys())
    if form:
        call.form_fields = form
    return call


def balanced(text: str, start: int, opening: str, closing: str) -> str:
    depth = 0
    for position in range(start, len(text)):
        if text[position] == opening:
            depth += 1
        elif text[position] == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1:position]
    return ""


def hashtable_keys(body: str) -> List[str]:
    keys: List[str] = []
    depth = 0
    for chunk in re.split(r"[;\n]", body):
        stripped = chunk.strip()
        if depth == 0:
            match = re.match(r"([A-Za-z_]\w*)\s*=", stripped)
            if match:
                keys.append(match.group(1))
        depth += chunk.count("{") - chunk.count("}")
    return keys


def ps_body(text: str, spec: str) -> Tuple[Optional[Any], Optional[List[str]]]:
    spec = spec.strip()
    if spec.startswith("("):
        inner = balanced(spec, 0, "(", ")")
        brace = inner.find("@{")
        if brace >= 0:
            return None, hashtable_keys(balanced(inner, brace + 1, "{", "}"))
        return None, None
    name = spec.lstrip("$")
    here = re.search(r"\$" + re.escape(name) + r'\s*=\s*@"\n(.*?)\n"@', text, re.S)
    if here:
        parsed = normalise_json(here.group(1))
        return parsed, list(parsed.keys()) if isinstance(parsed, dict) else None
    literal = re.search(r"\$" + re.escape(name) + r"\s*=\s*'([^']*)'", text)
    if literal:
        parsed = normalise_json(literal.group(1))
        return parsed, list(parsed.keys()) if isinstance(parsed, dict) else None
    table = re.search(r"\$" + re.escape(name) + r"\s*=\s*@\{", text)
    if table:
        return None, hashtable_keys(balanced(text, table.end() - 1, "{", "}"))
    return None, None


def body_spec(line: str) -> Optional[str]:
    found = re.search(r"-Body\s+", line)
    if not found:
        return None
    rest = line[found.end():]
    if rest.startswith("("):
        return "(" + balanced(rest, 0, "(", ")") + ")"
    variable = re.match(r"\$\w+", rest)
    return variable.group(0) if variable else None


def powershell_calls(text: str) -> List[Call]:
    calls = curl_calls(text, "powershell")
    for line in text.replace("\r\n", "\n").split("\n"):
        if not re.search(r"\b(Invoke-RestMethod|Invoke-WebRequest)\b", line):
            continue
        method = re.search(r"-Method\s+(\w+)", line)
        uri = re.search(r'-Uri\s+"([^"]+)"', line)
        headers = re.search(r"-Headers\s+(\$\w+)", line)
        content_type = re.search(r'-ContentType\s+"([^"]+)"', line)
        body = body_spec(line)
        path, query = concretize(uri.group(1) if uri else "")
        call = Call("powershell", (method.group(1).upper() if method else "GET"), path, line.strip(), query=query)
        if headers:
            definition = re.search(re.escape(headers.group(1)) + r"\s*=\s*@\{([^}]*)\}", text)
            if definition and "Authorization" in definition.group(1):
                call.headers.append("authorization:bearer")
        if content_type:
            call.content_type = content_type.group(1)
        if body:
            call.json_body, call.json_keys = ps_body(text, body)
        calls.append(call)
    return calls


def string_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "X" for part in node.values)
    return None


def dict_keys(node: ast.AST) -> Optional[List[str]]:
    if isinstance(node, ast.Dict):
        return [key.value for key in node.keys if isinstance(key, ast.Constant)]
    return None


def python_calls(source: str) -> List[Call]:
    calls: List[Call] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in HTTP_VERBS):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "client" or not node.args:
            continue
        raw = string_of(node.args[0])
        if raw is None:
            continue
        path, query = concretize(raw)
        call = Call("python", node.func.attr.upper(), path, ast.unparse(node), query=query)
        call.headers.append("authorization:bearer")
        for keyword in node.keywords:
            if keyword.arg == "json":
                call.json_keys = dict_keys(keyword.value)
                call.content_type = JSON_TYPE
            elif keyword.arg == "params":
                call.query.extend(dict_keys(keyword.value) or [])
            elif keyword.arg == "files":
                call.form_fields = dict_keys(keyword.value) or []
        calls.append(call)
    return calls


def calls_for(path_name: str, text: str) -> List[Call]:
    if path_name.endswith(".py"):
        return python_calls(text)
    if path_name.endswith(".ps1"):
        return powershell_calls(text)
    return curl_calls(text, "bash")


def expand(scopes: FrozenSet[str]) -> FrozenSet[str]:
    expanded = set(scopes)
    for scope in list(expanded):
        expanded |= SCOPE_IMPLIED.get(scope, set())
    return frozenset(expanded)


def validate(call: Call, table: RouteTable, key_scopes: FrozenSet[str], source_has_key_header: bool = True) -> List[str]:
    problems = [f"{call.method} {call.path}: {text}" for text in call.problems]
    route = table.find(call.method, call.path)
    if route is None:
        return problems + [f"{call.method} {call.path}: no such route"]
    required = table.scopes.get((call.method, route))
    if required is not None and not (expand(key_scopes) & set(required)):
        problems.append(f"{call.method} {route}: needs {required}, the example's key has {sorted(key_scopes)}")
    if required is None:
        problems.append(f"{call.method} {route}: a key cannot call this route")
    if not any(header.startswith("authorization:") and "bearer" in header for header in call.headers):
        problems.append(f"{call.method} {route}: no Authorization: Bearer header")
    known = table.query_names(call.method, route)
    for name in call.query:
        if name not in known:
            problems.append(f"{call.method} {route}: unknown query parameter {name}")
    if call.method in METHODS_WITH_BODY and call.json_keys is not None:
        sent = call.content_type or next((h.split(":", 1)[1] for h in call.headers if h.startswith("content-type:")), None)
        if sent is None or JSON_TYPE not in sent:
            problems.append(f"{call.method} {route}: a JSON body needs Content-Type: application/json")
        properties = table.body_properties(call.method, route, JSON_TYPE)
        if properties is None:
            problems.append(f"{call.method} {route}: the route takes no JSON body")
        else:
            allowed, required_keys = properties
            for key in call.json_keys:
                if key not in allowed:
                    problems.append(f"{call.method} {route}: unknown body field {key}")
            for key in required_keys:
                if key not in call.json_keys:
                    problems.append(f"{call.method} {route}: missing required body field {key}")
    if call.form_fields is not None:
        properties = table.body_properties(call.method, route, "multipart/form-data")
        if properties is None:
            problems.append(f"{call.method} {route}: the route takes no upload")
        else:
            for name in call.form_fields:
                if name not in properties[0]:
                    problems.append(f"{call.method} {route}: unknown form field {name}")
    return problems
