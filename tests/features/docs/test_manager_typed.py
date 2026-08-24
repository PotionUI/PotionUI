"""Typed-doc behaviour of DocsManager (Docs 2.0): scan, category defaults,
payload meta+refs, tree badging."""

from __future__ import annotations

from pathlib import Path

from src.features.docs.manager import DocsManager


class _Reg:
    def get_enabled_plugins(self):
        return []


_TECH_MD = """---
type: technique
title: FBCache
category_group: Performance
status: stable
families: [all-native]
---
Body.
"""

_MODEL_MD = """---
type: model
title: Flux
family_key: flux
modes: [txt2img]
spec: {arch: MMDiT, latent: 16ch, vae: flux_ae, te: t5, guidance: embedded, shift: 1.15, engine: native}
---
Body.
"""


def _tree(tmp_path: Path, tech=_TECH_MD, model=_MODEL_MD, extra=None):
    docs = tmp_path / "docs"
    (docs / "techniques").mkdir(parents=True)
    (docs / "models").mkdir(parents=True)
    (docs / "techniques" / "fbcache.md").write_text(tech)
    (docs / "models" / "flux.md").write_text(model)
    if extra:
        for rel, content in extra.items():
            p = docs / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return DocsManager(_Reg(), base_docs_path=str(docs))


def test_scans_typed_sections_with_stable_ids(tmp_path):
    m = _tree(tmp_path)
    tree = m.build_tree(is_admin=True)
    dev = next(s for s in tree["sections"] if s["id"] == "developer")
    ids = {it["id"] for it in dev["items"]}
    assert "dev/techniques/fbcache" in ids and "dev/models/flux" in ids


def test_directory_sets_default_category_frontmatter_wins(tmp_path):
    # techniques/ -> "Techniques" by default; a doc's own category overrides.
    custom = _TECH_MD.replace("families: [all-native]", "families: [all-native]\ncategory: Custom")
    m = _tree(tmp_path, tech=custom, extra={"techniques/plain.md": _TECH_MD})
    dev = next(s for s in m.build_tree(True)["sections"] if s["id"] == "developer")
    by_id = {it["id"]: it for it in dev["items"]}
    assert by_id["dev/techniques/plain"]["category"] == "Techniques"   # directory default
    assert by_id["dev/techniques/fbcache"]["category"] == "Custom"     # frontmatter wins


def test_tree_items_carry_doc_type_and_status(tmp_path):
    m = _tree(tmp_path)
    dev = next(s for s in m.build_tree(True)["sections"] if s["id"] == "developer")
    by_id = {it["id"]: it for it in dev["items"]}
    assert by_id["dev/techniques/fbcache"]["doc_type"] == "technique"
    assert by_id["dev/techniques/fbcache"]["status"] == "stable"
    assert by_id["dev/models/flux"]["doc_type"] == "model"


def test_content_payload_has_meta_and_refs(tmp_path):
    m = _tree(tmp_path)
    model = m.get_content("dev/models/flux", is_admin=True)
    assert model["meta"]["family_key"] == "flux"
    assert [t["slug"] for t in model["refs"]["techniques"]] == ["fbcache"]   # all-native match
    tech = m.get_content("dev/techniques/fbcache", is_admin=True)
    assert [mm["family_key"] for mm in tech["refs"]["models"]] == ["flux"]


def test_untyped_doc_payload_unchanged(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "plain.md").write_text("---\ntitle: Plain\n---\nHello.")
    m = DocsManager(_Reg(), base_docs_path=str(docs))
    payload = m.get_content("dev/plain", is_admin=True)
    assert payload == {"id": "dev/plain", "title": "Plain", "markdown": "Hello."}
    assert "meta" not in payload and "refs" not in payload


def test_recursive_one_level_subdir(tmp_path):
    m = _tree(tmp_path, extra={"techniques/perf/deep.md": _TECH_MD})
    dev = next(s for s in m.build_tree(True)["sections"] if s["id"] == "developer")
    assert "dev/techniques/perf/deep" in {it["id"] for it in dev["items"]}
