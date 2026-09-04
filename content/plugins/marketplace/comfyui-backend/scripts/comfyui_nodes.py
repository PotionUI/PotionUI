#!/usr/bin/env python3
"""In-plugin CLI for the ComfyUI import node catalog
(`backend/preset_import/node_catalog.yml`): how well a workflow's node
classes are covered, a paste-ready catalog entry skeleton for a class that
isn't, a plain listing of what's already there, and a validity check.

Run from the repo root (the `backend` package needs `aiohttp`, which lives in
the project venv, not stdlib):

    PYTHONPATH=./venv/lib/python3.12/site-packages:. \\
        python content/plugins/marketplace/comfyui-backend/scripts/comfyui_nodes.py <command> ...

Commands:
    coverage <workflow.json> [--object-info FILE] [--fail-on-uncatalogued]
        Analyze an exported (API-format) workflow: mode, sampler, sampling
        cluster, a per-class-type coverage table, the default form outline,
        and a final "Uncatalogued classes: ..." line - the to-do list for
        `scaffold`. Exits 0 for any parseable workflow (2 for a UI-format
        export) unless --fail-on-uncatalogued and that list is non-empty.

    scaffold <ClassType> [--object-info FILE | --comfyui-src DIR] [--category CAT]
        Print a `node_catalog.yml` entry skeleton for one node class, read
        from a saved `/object_info` dump (--object-info) or ComfyUI's own
        source (--comfyui-src, default /home/jtyszkiew/projects/ComfyUI) -
        never by importing or running ComfyUI code. `# TODO` markers show
        where a human must confirm a guess.

    list [--category CAT]
        One line per catalogued class: class, category, input roles, link
        kinds.

    check
        Run `validate_catalog` against the shipped catalog; exit 1 on any
        problem.

Adding a node to the catalog is then one YAML entry: run `scaffold`, paste
the output into `node_catalog.yml`, resolve the `# TODO`s, and `check`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from backend.preset_import.defaults import build_default_form  # noqa: E402
from backend.preset_import.node_catalog import get_catalog, validate_catalog  # noqa: E402
from backend.preset_import import node_scaffold  # noqa: E402
from backend.preset_import.parser import WorkflowFormatError, parse_api_workflow  # noqa: E402
from backend.preset_import.schema import ImportForm  # noqa: E402
from backend.preset_import.suggest import suggest_fields  # noqa: E402

_DEFAULT_COMFYUI_SRC = Path("/home/jtyszkiew/projects/ComfyUI")


def _load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_form_outline(form: ImportForm) -> None:
    for tab in form.tabs:
        print(f"  {tab.label}")
        _print_items_outline(tab.items, indent=4)


def _print_items_outline(items: List[Any], indent: int) -> None:
    pad = " " * indent
    for item in items:
        if item.kind == "field":
            print(f"{pad}{item.field_name} ({item.field_type})")
        elif item.kind == "section":
            print(f"{pad}{item.title}")
            _print_items_outline(item.items, indent + 2)
        elif item.kind in ("row", "group"):
            _print_items_outline(item.items, indent)
        elif item.kind == "header":
            continue


def cmd_coverage(args: argparse.Namespace) -> int:
    data = _load_json(args.workflow)
    try:
        workflow = parse_api_workflow(data)
    except WorkflowFormatError as e:
        print(str(e))
        return 2

    object_info = _load_json(args.object_info) if args.object_info else None
    analysis = suggest_fields(workflow, object_info=object_info)
    catalog = get_catalog()

    sampler = workflow.node(analysis.sampler_node_id) if analysis.sampler_node_id else None
    cluster_classes = sorted(
        {n.class_type for nid in analysis.sampling_cluster_node_ids if (n := workflow.node(nid)) is not None}
    )

    print(f"mode: {analysis.mode}")
    print(f"sampler: {analysis.sampler_node_id or '-'} ({sampler.class_type if sampler else '-'})")
    print(f"sampling cluster classes: {', '.join(cluster_classes) if cluster_classes else '-'}")
    print()

    class_counts: Dict[str, int] = {}
    for node in workflow.nodes.values():
        class_counts[node.class_type] = class_counts.get(node.class_type, 0) + 1

    obvious_by_class: Dict[str, int] = {}
    total_by_class: Dict[str, int] = {}
    for c in analysis.candidates:
        total_by_class[c.class_type] = total_by_class.get(c.class_type, 0) + 1
        if c.obvious:
            obvious_by_class[c.class_type] = obvious_by_class.get(c.class_type, 0) + 1

    print(f"{'class':<34}{'count':>6}  {'category':<12}{'obvious/total':>14}")
    uncatalogued: List[str] = []
    for class_type in sorted(class_counts):
        entry = catalog.get(class_type)
        category = entry.category if entry else "-"
        if entry is None:
            uncatalogued.append(class_type)
        obvious = obvious_by_class.get(class_type, 0)
        total = total_by_class.get(class_type, 0)
        print(f"{class_type:<34}{class_counts[class_type]:>6}  {category:<12}{obvious:>6}/{total:<7}")

    print()
    print("default form outline:")
    _print_form_outline(build_default_form(analysis))

    print()
    print(f"Uncatalogued classes: {', '.join(uncatalogued) if uncatalogued else '(none)'}")

    if args.fail_on_uncatalogued and uncatalogued:
        return 1
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    object_info = _load_json(args.object_info) if args.object_info else None
    comfyui_src = Path(args.comfyui_src) if args.comfyui_src else _DEFAULT_COMFYUI_SRC

    node = None
    if object_info is not None:
        node = node_scaffold.scaffold_for_class(args.class_type, object_info=object_info)
        if node is None:
            print(f"'{args.class_type}' not found in {args.object_info}", file=sys.stderr)
            return 1
    elif comfyui_src.is_dir():
        node = node_scaffold.scaffold_for_class(args.class_type, comfyui_src=comfyui_src)
        if node is None:
            print(f"'{args.class_type}' not found under {comfyui_src} (nodes.py / comfy_extras/*.py)", file=sys.stderr)
            return 1
    else:
        print(f"No --object-info given and {comfyui_src} doesn't exist; nothing to read from.", file=sys.stderr)
        return 1

    source = f"{node.file_path}:{node.line_no}" if node.file_path else f"object_info dump ({args.object_info})"
    print(f"# from {source} ({node.style})")
    print(node_scaffold.render_scaffold(node, category_override=args.category))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    catalog = get_catalog()
    for class_type in sorted(catalog.classes):
        entry = catalog.get(class_type)
        if args.category and entry.category != args.category:
            continue
        roles = ", ".join(f"{name}:{spec.role}" for name, spec in entry.inputs.items())
        links = ", ".join(f"{name}:{kind}" for name, kind in entry.links.items())
        print(f"{class_type:<34}{entry.category:<12}inputs=[{roles}] links=[{links}]")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    catalog = get_catalog()
    problems = validate_catalog(catalog)
    if not problems:
        print(f"OK - {len(catalog.classes)} catalogued classes, no problems.")
        return 0
    for problem in problems:
        print(problem)
    print(f"\n{len(problems)} problem(s).")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ComfyUI import node catalog CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_coverage = sub.add_parser("coverage", help="Coverage report for a workflow's node classes.")
    p_coverage.add_argument("workflow")
    p_coverage.add_argument("--object-info")
    p_coverage.add_argument("--fail-on-uncatalogued", action="store_true")
    p_coverage.set_defaults(func=cmd_coverage)

    p_scaffold = sub.add_parser("scaffold", help="Print a catalog entry skeleton for one node class.")
    p_scaffold.add_argument("class_type")
    p_scaffold.add_argument("--object-info")
    p_scaffold.add_argument("--comfyui-src")
    p_scaffold.add_argument("--category")
    p_scaffold.set_defaults(func=cmd_scaffold)

    p_list = sub.add_parser("list", help="List catalogued classes.")
    p_list.add_argument("--category")
    p_list.set_defaults(func=cmd_list)

    p_check = sub.add_parser("check", help="Validate the shipped catalog.")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
