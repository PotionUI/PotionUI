import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.features.models.recommendations import format_size, size_text_agrees
from src.platform.runtime.gpu_profile import PRECISIONS

HF_API = "https://huggingface.co/api"
TOKEN_ENV = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
_ITEM_RE = re.compile(r"^(\s*)-\s+([A-Za-z_][A-Za-z0-9_]*):(.*)$")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*):(.*)$")
_ANCHORS = ("size", "ref", "link", "provider", "name")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_RESOLVE_RE = re.compile(r"^https://huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)$")

Fetch = Callable[[str, Dict[str, str]], Any]


def default_fetch(url: str, headers: Dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def token_from_env(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    env = os.environ if env is None else env
    for name in TOKEN_ENV:
        if env.get(name):
            return env[name]
    return None


def infer_precision(filename: str) -> Optional[str]:
    tokens = set(re.split(r"[^a-z0-9]+", filename.lower()))
    found = [p for p in PRECISIONS if p in tokens]
    return found[0] if len(found) == 1 else None


class HubLookup:
    def __init__(self, fetch: Fetch = default_fetch, token: Optional[str] = None):
        self.fetch = fetch
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._trees: Dict[Tuple[str, str, str], Dict[str, Dict[str, Any]]] = {}
        self._models: Dict[str, Dict[str, Any]] = {}

    def _tree(self, repo: str, revision: str, directory: str) -> Dict[str, Dict[str, Any]]:
        key = (repo, revision, directory)
        if key not in self._trees:
            quoted = urllib.parse.quote(directory) if directory else ""
            url = f"{HF_API}/models/{repo}/tree/{urllib.parse.quote(revision)}" + (f"/{quoted}" if quoted else "")
            try:
                entries = self.fetch(url, self.headers) or []
            except Exception as exc:
                print(f"  ! could not list {repo}@{revision}/{directory}: {exc}")
                entries = []
            self._trees[key] = {e.get("path"): e for e in entries if isinstance(e, dict)}
        return self._trees[key]

    def model(self, repo: str) -> Dict[str, Any]:
        if repo not in self._models:
            try:
                self._models[repo] = self.fetch(f"{HF_API}/models/{repo}", self.headers) or {}
            except Exception as exc:
                print(f"  ! could not read model card metadata for {repo}: {exc}")
                self._models[repo] = {}
        return self._models[repo]

    def file(self, repo: str, revision: str, path: str) -> Optional[Dict[str, Any]]:
        directory = str(PurePosixPath(path).parent)
        entry = self._tree(repo, revision, "" if directory == "." else directory).get(path)
        if entry is None:
            return None
        lfs = entry.get("lfs") or {}
        size = lfs.get("size", entry.get("size"))
        oid = lfs.get("oid")
        sha256 = oid.lower() if isinstance(oid, str) and _SHA256_RE.match(oid) else None
        return {"size_bytes": int(size) if isinstance(size, int) else None, "sha256": sha256}


def _hf_location(rec: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    link = rec.get("link")
    if isinstance(link, str) and not rec.get("provider"):
        match = _RESOLVE_RE.match(link)
        if match is None:
            return None
        return match.group(1), urllib.parse.unquote(match.group(2)), urllib.parse.unquote(match.group(3).split("?", 1)[0])
    if rec.get("provider") != "huggingface" or not rec.get("ref"):
        return None
    try:
        parsed = json.loads(rec["ref"])
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed.get("repo") or not parsed.get("file"):
        return None
    return parsed["repo"], parsed.get("revision") or "main", parsed["file"]


def _items(lines: List[str]):
    i = 0
    while i < len(lines):
        match = _ITEM_RE.match(lines[i])
        if match is None:
            i += 1
            continue
        dash_indent = len(match.group(1))
        end = i + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped and not stripped.startswith("#"):
                indent = len(lines[end]) - len(lines[end].lstrip())
                if indent <= dash_indent:
                    break
            end += 1
        yield i, end, dash_indent + 2
        i += 1


def _item_data(lines: List[str], start: int, end: int, key_indent: int) -> Optional[Dict[str, Any]]:
    block = [" " * key_indent + lines[start].split("-", 1)[1].lstrip()] + lines[start + 1:end]
    text = "\n".join(line[key_indent:] if len(line) >= key_indent else line.lstrip() for line in block)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _key_lines(lines: List[str], start: int, end: int, key_indent: int) -> Dict[str, int]:
    keys: Dict[str, int] = {}
    first = _ITEM_RE.match(lines[start])
    keys[first.group(2)] = start
    for index in range(start + 1, end):
        match = _KEY_RE.match(lines[index])
        if match and len(match.group(1)) == key_indent:
            keys.setdefault(match.group(2), index)
    return keys


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)


def fill_text(text: str, lookup: HubLookup, update: bool = False) -> Tuple[str, List[str], List[str]]:
    lines = text.split("\n")
    edits: List[Tuple[int, str, Optional[int]]] = []
    changes: List[str] = []
    skipped: List[str] = []
    for start, end, key_indent in list(_items(lines)):
        data = _item_data(lines, start, end, key_indent)
        if not data or "name" not in data or not ("ref" in data or "link" in data):
            continue
        name = str(data.get("name"))
        if "{{" in name or "{{" in str(data.get("ref", "")):
            skipped.append(f"{name}: templated, fill the vars file by hand")
            continue
        location = _hf_location(data)
        if location is None:
            skipped.append(f"{name}: not a Hugging Face file")
            continue
        repo, revision, path = location
        info = lookup.file(repo, revision, path)
        if info is None:
            skipped.append(f"{name}: {repo}@{revision}/{path} not found on the Hub")
            continue
        wanted: Dict[str, Any] = {}
        if info["size_bytes"] is not None and (update or "size_bytes" not in data):
            wanted["size_bytes"] = info["size_bytes"]
        if info["sha256"] and (update or "sha256" not in data):
            wanted["sha256"] = info["sha256"]
        if "precision" not in data:
            precision = infer_precision(PurePosixPath(path).name)
            if precision:
                wanted["precision"] = precision
        model = lookup.model(repo)
        if model.get("gated") and "gated" not in data:
            wanted["gated"] = True
            if "license_url" not in data:
                wanted["license_url"] = f"https://huggingface.co/{repo}"
        keys = _key_lines(lines, start, end, key_indent)
        size_bytes = wanted.get("size_bytes", data.get("size_bytes"))
        size_text = data.get("size")
        if isinstance(size_bytes, int) and isinstance(size_text, str) and "size" in keys and not size_text_agrees(size_text, size_bytes):
            edits.append((keys["size"], f"{' ' * key_indent}size: {_yaml_scalar(format_size(size_bytes))}", keys["size"]))
            changes.append(f"{name}: size {size_text!r} -> {format_size(size_bytes)!r}")
        anchor = next((keys[k] for k in _ANCHORS if k in keys), start)
        for key, value in wanted.items():
            line = f"{' ' * key_indent}{key}: {_yaml_scalar(value)}"
            if key in keys:
                edits.append((keys[key], line, keys[key]))
            else:
                edits.append((anchor, line, None))
            changes.append(f"{name}: {key} = {value}")
    inserts: Dict[int, List[str]] = {}
    for index, line, replace_at in edits:
        if replace_at is not None:
            lines[replace_at] = line
        else:
            inserts.setdefault(index, []).append(line)
    for index in sorted(inserts, reverse=True):
        lines[index + 1:index + 1] = inserts[index]
    return "\n".join(lines), changes, skipped


def yaml_files(target: Path) -> List[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.yml") if p.is_file())


def resolve_targets(args: List[str]) -> List[Path]:
    paths = []
    for arg in args:
        path = Path(arg)
        if not path.exists():
            for root in (ROOT / "content/presets/marketplace", ROOT / "content/presets/local"):
                if (root / arg).exists():
                    path = root / arg
                    break
        paths.append(path)
    return paths


def main(argv: Optional[List[str]] = None, fetch: Fetch = default_fetch, env: Optional[Dict[str, str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill size_bytes, sha256, precision and gated on Hugging Face recommendations.")
    parser.add_argument("presets", nargs="+", help="preset directory, preset folder name, or a single YAML file")
    parser.add_argument("--dry-run", action="store_true", help="print the changes without writing")
    parser.add_argument("--update", action="store_true", help="overwrite size_bytes and sha256 that are already set")
    args = parser.parse_args(argv)
    lookup = HubLookup(fetch=fetch, token=token_from_env(env))
    status = 0
    for target in resolve_targets(args.presets):
        if not target.exists():
            print(f"{target}: not found")
            status = 1
            continue
        for path in yaml_files(target):
            text = path.read_text(encoding="utf-8")
            updated, changes, skipped = fill_text(text, lookup, update=args.update)
            if not changes and not skipped:
                continue
            print(f"{path}:")
            for change in changes:
                print(f"  + {change}")
            for note in skipped:
                print(f"  - skipped {note}")
            if changes and not args.dry_run and updated != text:
                path.write_text(updated, encoding="utf-8")
    return status


if __name__ == "__main__":
    sys.exit(main())
