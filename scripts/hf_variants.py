#!/usr/bin/env python3
import argparse
import fnmatch
import json
import sys
import urllib.parse
import urllib.request
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Sequence

import yaml

HF_API = "https://huggingface.co/api/models"
HF_SITE = "https://huggingface.co"

PRECISION_TOKENS = ("bf16", "fp16", "fp8", "int8", "nvfp4")

LABELS = {
    "bf16": "Best quality",
    "fp16": "Best quality",
    "fp8": "Balanced",
    "int8": "Standard",
    "nvfp4": "Low VRAM",
}

Fetch = Callable[[str], Any]


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "potionui-hf-variants"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def precision_of(filename: str) -> Optional[str]:
    tokens = PurePosixPath(filename).stem.lower().replace("-", "_").split("_")
    for precision in PRECISION_TOKENS:
        if precision in tokens:
            return precision
    return None


def _repo_path(repo: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in repo.split("/"))


def collect_variants(
    repo: str,
    patterns: Sequence[str],
    *,
    exclude: Sequence[str] = (),
    revision: str = "main",
    default_id: Optional[str] = None,
    fetch: Fetch = fetch_json,
) -> Dict[str, Any]:
    meta = fetch(f"{HF_API}/{_repo_path(repo)}")
    tree = fetch(f"{HF_API}/{_repo_path(repo)}/tree/{revision}?recursive=1")
    author = meta.get("author") or repo.split("/")[0]
    gated = bool(meta.get("gated"))
    license_name = (meta.get("cardData") or {}).get("license")
    source_url = f"{HF_SITE}/{repo}"

    variants: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for entry in tree:
        if entry.get("type") != "file":
            continue
        path = entry["path"]
        if not any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
            continue
        if any(fnmatch.fnmatch(path, pattern) for pattern in exclude):
            continue
        filename = PurePosixPath(path).name
        precision = precision_of(filename)
        if precision is None:
            skipped.append(path)
            continue
        lfs = entry.get("lfs") or {}
        variant: Dict[str, Any] = {
            "id": PurePosixPath(filename).stem,
            "label": LABELS[precision],
            "precision": precision,
            "filename": filename,
            "size_bytes": int(lfs.get("size") or entry.get("size") or 0),
        }
        if lfs.get("oid"):
            variant["checksum"] = {"algorithm": "sha256", "value": lfs["oid"]}
        variant["provider_hint"] = {
            "source": "huggingface",
            "model_id": repo,
            "version_id": f"{revision}@{path}",
        }
        variant["uploader"] = author
        variant["source_url"] = source_url
        if gated:
            variant["gated"] = True
            variant["license_url"] = source_url
        if default_id and variant["id"] == default_id:
            variant["default"] = True
        variants.append(variant)

    variants.sort(key=lambda v: (PRECISION_TOKENS.index(v["precision"]), -v["size_bytes"]))
    return {"variants": variants, "skipped": skipped, "license": license_name, "gated": gated, "author": author}


def render_yaml(variants: List[Dict[str, Any]]) -> str:
    return yaml.safe_dump({"variants": variants}, sort_keys=False, allow_unicode=True, width=120)


def main(argv: Optional[Sequence[str]] = None, fetch: Fetch = fetch_json) -> int:
    parser = argparse.ArgumentParser(
        description="Print ready-to-paste recipe `variants:` YAML for files in a Hugging Face repo."
    )
    parser.add_argument("repo", help="Hugging Face repo id, e.g. Comfy-Org/Krea-2")
    parser.add_argument("patterns", nargs="+", help="Path globs inside the repo, e.g. 'diffusion_models/krea2_turbo_*'")
    parser.add_argument("--exclude", action="append", default=[], help="Path glob to leave out (repeatable)")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--default", dest="default_id", help="Variant id to mark `default: true`")
    args = parser.parse_args(argv)

    try:
        result = collect_variants(
            args.repo,
            args.patterns,
            exclude=args.exclude,
            revision=args.revision,
            default_id=args.default_id,
            fetch=fetch,
        )
    except Exception as exc:
        print(f"Could not read {args.repo} from Hugging Face: {exc}", file=sys.stderr)
        return 1

    if not result["variants"]:
        print("No files matched.", file=sys.stderr)
        return 1
    for path in result["skipped"]:
        print(f"skipped (unknown precision): {path}", file=sys.stderr)
    print(
        f"repo {args.repo}: uploader={result['author']} gated={result['gated']} license={result['license']}",
        file=sys.stderr,
    )
    if args.default_id and not any(v.get("default") for v in result["variants"]):
        print(f"--default '{args.default_id}' matched no variant", file=sys.stderr)
    sys.stdout.write(render_yaml(result["variants"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
