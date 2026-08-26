"""
Documentation tree aggregation and content resolution.

Post-Manager reference shape (see `src.features.workspaces.operations`): one
concern (aggregating docs from disk/plugins into a role-filtered tree), small
enough for a single module - split it out before it outgrows this rather than
let a second concern move in here. Every function takes the collaborators it
needs (`plugin_registry`, `base_docs_path`) as leading arguments; nothing here
is stored across calls. `DocsController` (`routes.py`) holds the collaborators
and passes them in.

Aggregates the in-app Documentation feature's tree from three sources:

1. Repo markdown - `docs/user/*.md` (user section, visible to all
   authenticated users) and top-level `docs/*.md` (developer section, ADMIN
   only, not recursing into `user/`). Title, order, and optional category
   metadata come from YAML frontmatter; titles otherwise fall back to the first
   `# heading`, then the filename.
2. Plugin docs - the `docs:` manifest section of ENABLED plugins.
3. Live reference - `type: "live"` items the frontend renders from existing
   APIs: hooks catalog, field types, pipes, output types, template functions,
   APIs plus the client-side frontend kit. Developer and Contributor sections,
   ADMIN only.

The tree is rebuilt fresh on every call - docs live on disk and change
independently of the running process, and the scan is cheap (a handful of
small directories plus a few enabled plugin manifests already held in
memory).
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.features.docs.frontmatter import parse_frontmatter
from src.features.docs.typed import parse_typed, refs_for

logger = logging.getLogger(__name__)


class DocNotFoundError(Exception):
    """Raised when a doc id doesn't resolve to anything in the aggregated tree."""


class DocForbiddenError(Exception):
    """Raised when a non-admin requests a doc from the developer section."""


class DocIsLiveError(Exception):
    """Raised when markdown content is requested for a `type: "live"` doc."""


_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)

# Live reference items rendered by the frontend from existing/new APIs.
# Order values are spaced out so markdown docs (default order 100) can be
# interleaved by an admin without renumbering these.
_LIVE_DOCS: List[Dict[str, Any]] = [
    {"live_kind": "hooks", "title": "Hooks Catalog", "order": 10},
    {"live_kind": "field-types", "title": "Field Types", "order": 20},
    {"live_kind": "pipes", "title": "Pipes", "order": 30},
    {"live_kind": "output-types", "title": "Output Types", "order": 40},
    {"live_kind": "template-functions", "title": "Template Functions", "order": 50},
    {"live_kind": "icons", "title": "Icons", "order": 60},
    {"live_kind": "frontend-kit", "title": "Frontend Kit", "order": 10, "audience": "contributor"},
]


def _title_from_filename(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def _derive_title(body: str, stem: str) -> str:
    heading = _HEADING_RE.search(body)
    if heading:
        return heading.group(1).strip()
    return _title_from_filename(stem)


def _coerce_order(value: Any, default: int = 100) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _category_metadata(
    category_value: Any, order_value: Any
) -> Tuple[Optional[str], Optional[int]]:
    """Normalize optional category metadata for a flat documentation item."""
    category = category_value.strip() if isinstance(category_value, str) else ""
    if not category:
        return None, None
    return category, _coerce_order(order_value)


@dataclass
class _DocRecord:
    """Internal representation of one tree entry, before/regardless of role filtering."""

    id: str
    title: str
    type: str  # "markdown" | "live"
    live_kind: Optional[str]
    source: str  # "repo" | "plugin"
    plugin_id: Optional[str]
    order: int
    audience: str  # "user" | "developer" | "contributor"
    category: Optional[str] = None
    category_order: Optional[int] = None
    file_path: Optional[Path] = None
    # Docs 2.0 typed frontmatter (``type: technique|model``). ``doc_type`` is the
    # frontmatter type (distinct from ``type`` above, which is the RENDER kind
    # markdown/live); ``status`` is a typed doc's lifecycle badge; ``slug`` is the
    # filename stem (cross-reference key); ``meta`` is the validated typed
    # frontmatter dict (None for untyped docs). See ``docs/typed.py``.
    doc_type: Optional[str] = None
    status: Optional[str] = None
    slug: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def to_item(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "live_kind": self.live_kind,
            "source": self.source,
            "plugin_id": self.plugin_id,
            "order": self.order,
            "category": self.category,
            "category_order": self.category_order,
            # Typed-doc badging fields (null for untyped docs). ``doc_type`` is the
            # frontmatter type — kept separate from ``type`` (markdown/live) to not
            # break existing renderers.
            "doc_type": self.doc_type,
            "status": self.status,
        }


# ---------------------------------------------------------------- tree

def build_tree(plugin_registry, base_docs_path: "Path | str", is_admin: bool) -> Dict[str, Any]:
    """Build `{"sections": [...], "hidden_sections": [...]}`.

    The developer/contributor sections (which is where `docs/models/*.md`
    and `docs/techniques/*.md` land - see `_collect_repo_docs` - alongside
    architecture notes and plugin-contributed developer docs) are omitted
    entirely for non-admins: they're native-engine/plugin-authoring
    reference material, not end-user guidance.

    `hidden_sections` signals that omission without exposing any content:
    for a non-admin it lists the omitted section ids/titles and how many
    items each has (empty when nothing is hidden); for an admin it's always
    `[]` since nothing is hidden from them.
    """
    records = _collect_records(plugin_registry, base_docs_path)

    user_items = [r.to_item() for r in records if r.audience == "user"]
    user_items.sort(key=lambda item: (item["order"], item["title"]))
    sections = [{"id": "user", "title": "User Guide", "items": user_items}]

    dev_items = [r.to_item() for r in records if r.audience == "developer"]
    dev_items.sort(key=lambda item: (item["order"], item["title"]))

    contributor_items = [r.to_item() for r in records if r.audience == "contributor"]
    contributor_items.sort(key=lambda item: (item["order"], item["title"]))

    hidden_sections: List[Dict[str, Any]] = []
    if is_admin:
        sections.append({"id": "developer", "title": "Developer", "items": dev_items})
        sections.append({"id": "contributor", "title": "Contributor", "items": contributor_items})
    else:
        if dev_items:
            hidden_sections.append({"id": "developer", "title": "Developer", "count": len(dev_items)})
        if contributor_items:
            hidden_sections.append({"id": "contributor", "title": "Contributor", "count": len(contributor_items)})

    return {"sections": sections, "hidden_sections": hidden_sections}


# ------------------------------------------------------------- content

def get_content(plugin_registry, base_docs_path: "Path | str", doc_id: str, is_admin: bool) -> Dict[str, Any]:
    """
    Resolve a doc id to its raw markdown (frontmatter stripped).

    Raises:
        DocNotFoundError: `doc_id` doesn't exist.
        DocForbiddenError: `doc_id` is in the developer section and `is_admin` is False.
        DocIsLiveError: `doc_id` is a `type: "live"` entry - it has no markdown.
    """
    records = _collect_records(plugin_registry, base_docs_path)
    record = next((r for r in records if r.id == doc_id), None)
    if record is None:
        raise DocNotFoundError(f"Unknown doc id: '{doc_id}'")

    if record.audience != "user" and not is_admin:
        raise DocForbiddenError(f"Doc '{doc_id}' requires admin access")

    if record.type == "live":
        raise DocIsLiveError(f"Doc '{doc_id}' is a live reference and has no markdown content")

    markdown = record.file_path.read_text(encoding="utf-8")
    _frontmatter, markdown = parse_frontmatter(markdown)

    payload: Dict[str, Any] = {"id": record.id, "title": record.title, "markdown": markdown}
    # Typed docs (Docs 2.0) carry their validated frontmatter as ``meta`` plus a
    # cross-linked ``refs`` reverse index (techniques ↔ models by family).
    if record.meta is not None:
        others = [
            (r.doc_type, r.id, r.slug, r.meta)
            for r in records
            if r is not record and r.meta is not None
        ]
        payload["meta"] = record.meta
        payload["refs"] = refs_for(record.doc_type, record.meta, others)
    return payload


# ------------------------------------------------------------ internal

def _collect_records(plugin_registry, base_docs_path) -> List[_DocRecord]:
    base_docs_path = Path(base_docs_path)
    records: List[_DocRecord] = []
    records.extend(_collect_repo_docs(base_docs_path))
    records.extend(_collect_plugin_docs(plugin_registry))
    records.extend(_collect_live_docs())
    return records


def _collect_repo_docs(base_docs_path: Path) -> List[_DocRecord]:
    records: List[_DocRecord] = []

    user_dir = base_docs_path / "user"
    if user_dir.is_dir():
        for md_path in sorted(user_dir.glob("*.md")):
            records.append(_record_from_markdown(md_path, id_prefix="user", audience="user"))

    if base_docs_path.is_dir():
        # Top-level only - `docs/user/*.md` is handled above, not re-included here.
        for md_path in sorted(base_docs_path.glob("*.md")):
            records.append(_record_from_markdown(md_path, id_prefix="dev", audience="developer"))

    # Docs 2.0 typed sections: docs/techniques/*.md and docs/models/*.md
    # (recursive one level). The directory sets a default category; a doc's
    # own frontmatter category still wins.
    for subdir, default_category in (("techniques", "Techniques"), ("models", "Models")):
        section_dir = base_docs_path / subdir
        if not section_dir.is_dir():
            continue
        paths = sorted(set(section_dir.glob("*.md")) | set(section_dir.glob("*/*.md")))
        for md_path in paths:
            rel = md_path.relative_to(base_docs_path).with_suffix("").as_posix()
            records.append(_record_from_markdown(
                md_path, id_prefix="dev", audience="developer",
                doc_id=f"dev/{rel}", default_category=default_category,
            ))

    return records


def _record_from_markdown(
    md_path: Path,
    id_prefix: str,
    audience: str,
    doc_id: Optional[str] = None,
    default_category: Optional[str] = None,
) -> _DocRecord:
    text = md_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)

    # Typed frontmatter (Docs 2.0). A typed doc's title comes from its schema;
    # untyped docs keep the existing title-derivation. Directory default
    # category applies only when the frontmatter doesn't set one.
    doc_type, meta, _errors = parse_typed(frontmatter)

    title = frontmatter.get("title") or _derive_title(body, md_path.stem)
    order = _coerce_order(frontmatter.get("order"))
    category, category_order = _category_metadata(
        frontmatter.get("category"), frontmatter.get("category_order")
    )
    if category is None and default_category is not None:
        category, category_order = default_category, _coerce_order(frontmatter.get("category_order"))

    return _DocRecord(
        id=doc_id or f"{id_prefix}/{md_path.stem}",
        title=str(title),
        type="markdown",
        live_kind=None,
        source="repo",
        plugin_id=None,
        order=order,
        audience=audience,
        category=category,
        category_order=category_order,
        file_path=md_path,
        doc_type=doc_type,
        status=(meta.get("status") if meta else None),
        slug=md_path.stem,
        meta=meta,
    )


def _collect_plugin_docs(plugin_registry) -> List[_DocRecord]:
    records: List[_DocRecord] = []

    for manifest in plugin_registry.get_enabled_plugins():
        doc_entries = getattr(manifest, "docs", None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not doc_entries or not plugin_dir:
            continue

        plugin_dir = Path(plugin_dir).resolve()
        seen_ids: Set[str] = set()

        for index, entry in enumerate(doc_entries):
            record = _record_from_plugin_doc(manifest.id, plugin_dir, entry, index, seen_ids)
            if record is not None:
                records.append(record)
                seen_ids.add(record.id)

    return records


def _record_from_plugin_doc(
    plugin_id: str,
    plugin_dir: Path,
    entry: Dict[str, Any],
    index: int,
    seen_ids: Set[str],
) -> Optional[_DocRecord]:
    path_str = entry.get("path")
    if not path_str:
        logger.warning(f"Plugin '{plugin_id}' doc entry missing 'path': {entry}")
        return None

    resolved = (plugin_dir / path_str).resolve()

    # Path-traversal guard - same style as plugin_controller.get_plugin_asset:
    # the resolved path must stay inside the plugin directory.
    try:
        resolved.relative_to(plugin_dir)
    except ValueError:
        logger.warning(f"Plugin '{plugin_id}' doc path escapes plugin dir, skipping: {path_str}")
        return None

    if not resolved.is_file():
        logger.warning(f"Plugin '{plugin_id}' doc file not found, skipping: {resolved}")
        return None

    stem = resolved.stem
    stable_suffix = stem if stem not in seen_ids else str(index)

    audience = entry.get("audience") or "user"
    if audience not in ("user", "developer", "contributor"):
        audience = "user"

    title = entry.get("title") or _title_from_filename(stem)
    order = _coerce_order(entry.get("order"))
    category, category_order = _category_metadata(
        entry.get("category"), entry.get("category_order")
    )

    return _DocRecord(
        id=f"plugin/{plugin_id}/{stable_suffix}",
        title=str(title),
        type="markdown",
        live_kind=None,
        source="plugin",
        plugin_id=plugin_id,
        order=order,
        audience=audience,
        category=category,
        category_order=category_order,
        file_path=resolved,
    )


def _collect_live_docs() -> List[_DocRecord]:
    return [
        _DocRecord(
            id=f"live/{entry['live_kind']}",
            title=entry["title"],
            type="live",
            live_kind=entry["live_kind"],
            source="repo",
            plugin_id=None,
            order=entry["order"],
            audience=entry.get("audience", "developer"),
            file_path=None,
        )
        for entry in _LIVE_DOCS
    ]
