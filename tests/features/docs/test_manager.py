"""Tests for DocsManager (src/core/docs/manager.py)."""

from types import SimpleNamespace

import pytest

from src.features.docs.manager import (
    DocsManager,
    DocForbiddenError,
    DocIsLiveError,
    DocNotFoundError,
)


class FakePluginRegistry:
    """Minimal stand-in for PluginRegistry.get_enabled_plugins()."""

    def __init__(self, enabled_manifests):
        self._enabled = enabled_manifests

    def get_enabled_plugins(self):
        return self._enabled


def make_manifest(plugin_id, plugin_dir, docs):
    return SimpleNamespace(id=plugin_id, plugin_dir=plugin_dir, docs=docs)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestBuildTreeRepoDocs:
    def test_user_docs_form_user_section(self, tmp_path):
        write(tmp_path / "docs" / "user" / "getting-started.md", "# Getting Started\n\nHello.")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)

        assert [s["id"] for s in tree["sections"]] == ["user"]
        user_section = tree["sections"][0]
        assert user_section["title"] == "User Guide"
        assert len(user_section["items"]) == 1
        item = user_section["items"][0]
        assert item["id"] == "user/getting-started"
        assert item["title"] == "Getting Started"
        assert item["type"] == "markdown"
        assert item["source"] == "repo"
        assert item["plugin_id"] is None
        assert item["category"] is None
        assert item["category_order"] is None

    def test_developer_section_omitted_for_non_admin(self, tmp_path):
        write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)

        assert [s["id"] for s in tree["sections"]] == ["user"]

    def test_hidden_sections_report_omitted_developer_docs_for_non_admin(self, tmp_path):
        """A non-admin gets no developer-section content, but the
        tree still says it exists and how many docs it holds, instead of
        silently omitting it with zero explanation.

        Baseline (zero repo markdown) is 6 developer + 1 contributor "live
        reference" entries (`_LIVE_DOCS` - hooks/field-types/pipes/output-types/
        template-functions/icons + frontend-kit) - these are always present,
        so the two markdown docs below land on top of that baseline."""
        write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n")
        write(tmp_path / "docs" / "models" / "sdxl.md", "---\ntype: model\ntitle: SDXL\n"
              "family_key: sdxl\nspec: {arch: a, latent: l, vae: v, te: t, guidance: g}\n---\nBody")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)

        assert [s["id"] for s in tree["sections"]] == ["user"]
        hidden = {h["id"]: h for h in tree["hidden_sections"]}
        assert hidden["developer"]["count"] == 8  # 6 live + ARCHITECTURE.md + models/sdxl.md
        assert hidden["developer"]["title"] == "Developer"
        assert hidden["contributor"]["count"] == 1  # live/frontend-kit

    def test_hidden_sections_report_live_reference_baseline_with_no_repo_docs(self, tmp_path):
        """Even with zero markdown docs, the live reference entries alone make
        the developer/contributor sections non-empty and thus reported."""
        write(tmp_path / "docs" / "user" / "intro.md", "# Intro\n")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)

        hidden = {h["id"]: h for h in tree["hidden_sections"]}
        assert hidden["developer"]["count"] == 6
        assert hidden["contributor"]["count"] == 1

    def test_hidden_sections_always_empty_for_admin(self, tmp_path):
        """Nothing is hidden from an admin - hidden_sections is not just unused, it's empty."""
        write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=True)

        assert tree["hidden_sections"] == []

    def test_developer_section_present_for_admin_with_live_items(self, tmp_path):
        write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=True)

        assert [s["id"] for s in tree["sections"]] == ["user", "developer", "contributor"]
        dev_items = tree["sections"][1]["items"]
        ids = {item["id"] for item in dev_items}
        assert "dev/ARCHITECTURE" in ids
        # Live reference entries always present for admins
        assert {"live/hooks", "live/field-types", "live/pipes", "live/output-types"} <= ids
        live_hooks = next(i for i in dev_items if i["id"] == "live/hooks")
        assert live_hooks["type"] == "live"
        assert live_hooks["live_kind"] == "hooks"
        assert live_hooks["category"] is None
        assert live_hooks["category_order"] is None
        contributor_items = tree["sections"][2]["items"]
        assert [item["id"] for item in contributor_items] == ["live/frontend-kit"]

    def test_user_subdir_not_double_counted_in_developer_section(self, tmp_path):
        write(tmp_path / "docs" / "user" / "intro.md", "# Intro\n")
        write(tmp_path / "docs" / "TOPLEVEL.md", "# Toplevel\n")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=True)

        dev_ids = {item["id"] for item in tree["sections"][1]["items"]}
        assert "user/intro" not in dev_ids
        assert "dev/TOPLEVEL" in dev_ids

    def test_ordering_by_order_then_title(self, tmp_path):
        write(tmp_path / "docs" / "user" / "b.md", "---\ntitle: Bravo\norder: 5\n---\nBody")
        write(tmp_path / "docs" / "user" / "a.md", "---\ntitle: Alpha\norder: 5\n---\nBody")
        write(tmp_path / "docs" / "user" / "z.md", "---\ntitle: Zulu\norder: 1\n---\nBody")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)
        titles = [item["title"] for item in tree["sections"][0]["items"]]

        assert titles == ["Zulu", "Alpha", "Bravo"]

    def test_frontmatter_title_and_order(self, tmp_path):
        write(
            tmp_path / "docs" / "user" / "custom.md",
            "---\ntitle: Custom Title\norder: 42\n---\n# Ignored Heading\nBody text.",
        )
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)
        item = tree["sections"][0]["items"][0]

        assert item["title"] == "Custom Title"
        assert item["order"] == 42

    def test_frontmatter_category_and_category_order(self, tmp_path):
        write(
            tmp_path / "docs" / "model-inference.md",
            (
                "---\n"
                "title: Model Inference\n"
                "category: '  Presets / Models  '\n"
                "category_order: 20\n"
                "---\n"
                "Body"
            ),
        )
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=True)
        item = next(i for i in tree["sections"][1]["items"] if i["id"] == "dev/model-inference")

        assert item["category"] == "Presets / Models"
        assert item["category_order"] == 20

    @pytest.mark.parametrize("category", ["", "   ", None, 123])
    def test_blank_or_non_string_frontmatter_category_is_uncategorized(self, tmp_path, category):
        category_yaml = "null" if category is None else repr(category)
        write(
            tmp_path / "docs" / "user" / "doc.md",
            f"---\ncategory: {category_yaml}\ncategory_order: 2\n---\n# Doc\n",
        )
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        item = manager.build_tree(is_admin=False)["sections"][0]["items"][0]

        assert item["category"] is None
        assert item["category_order"] is None

    def test_categorized_doc_defaults_missing_category_order_to_100(self, tmp_path):
        write(
            tmp_path / "docs" / "user" / "doc.md",
            "---\ncategory: Reference\n---\n# Doc\n",
        )
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        item = manager.build_tree(is_admin=False)["sections"][0]["items"][0]

        assert item["category"] == "Reference"
        assert item["category_order"] == 100

    def test_title_falls_back_to_heading_then_filename(self, tmp_path):
        write(tmp_path / "docs" / "user" / "has-heading.md", "# Real Heading\nBody.")
        write(tmp_path / "docs" / "user" / "no_heading_here.md", "Just some text, no heading.")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)
        by_id = {item["id"]: item["title"] for item in tree["sections"][0]["items"]}

        assert by_id["user/has-heading"] == "Real Heading"
        assert by_id["user/no_heading_here"] == "No Heading Here"


class TestPluginDocs:
    def test_enabled_plugin_docs_included(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "sample"
        write(plugin_dir / "docs" / "README.md", "# Sample Plugin\nUsage info.")
        manifest = make_manifest(
            "sample",
            plugin_dir,
            [{"title": "Sample", "path": "docs/README.md", "audience": "user", "order": 10}],
        )
        manager = DocsManager(FakePluginRegistry([manifest]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)
        items = tree["sections"][0]["items"]

        assert len(items) == 1
        assert items[0]["id"] == "plugin/sample/README"
        assert items[0]["source"] == "plugin"
        assert items[0]["plugin_id"] == "sample"
        assert items[0]["title"] == "Sample"
        assert items[0]["category"] is None
        assert items[0]["category_order"] is None

    def test_enabled_plugin_doc_includes_normalized_category_metadata(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "sample"
        write(plugin_dir / "docs" / "README.md", "# Sample Plugin\nUsage info.")
        manifest = make_manifest(
            "sample",
            plugin_dir,
            [
                {
                    "title": "Sample",
                    "path": "docs/README.md",
                    "category": "  Presets / Models ",
                    "category_order": 20,
                }
            ],
        )
        manager = DocsManager(FakePluginRegistry([manifest]), base_docs_path=str(tmp_path / "docs"))

        item = manager.build_tree(is_admin=False)["sections"][0]["items"][0]

        assert item["category"] == "Presets / Models"
        assert item["category_order"] == 20

    def test_disabled_plugin_excluded(self, tmp_path):
        # A registry whose get_enabled_plugins() only returns the enabled
        # manifest simulates a disabled plugin being filtered out upstream -
        # the manager must not somehow surface it.
        enabled_dir = tmp_path / "plugins" / "enabled"
        write(enabled_dir / "docs" / "README.md", "# Enabled\n")
        enabled_manifest = make_manifest(
            "enabled-plugin", enabled_dir, [{"title": "Enabled", "path": "docs/README.md"}]
        )
        manager = DocsManager(FakePluginRegistry([enabled_manifest]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)
        ids = {item["id"] for item in tree["sections"][0]["items"]}

        assert "plugin/enabled-plugin/README" in ids
        assert not any(i.startswith("plugin/disabled-plugin/") for i in ids)

    def test_developer_audience_plugin_doc_in_developer_section(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "sample"
        write(plugin_dir / "internals.md", "# Internals\n")
        manifest = make_manifest(
            "sample", plugin_dir, [{"title": "Internals", "path": "internals.md", "audience": "developer"}]
        )
        manager = DocsManager(FakePluginRegistry([manifest]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=True)
        dev_ids = {item["id"] for item in tree["sections"][1]["items"]}
        user_ids = {item["id"] for item in tree["sections"][0]["items"]}

        assert "plugin/sample/internals" in dev_ids
        assert "plugin/sample/internals" not in user_ids

    def test_contributor_audience_plugin_doc_is_admin_only(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "sample"
        write(plugin_dir / "contributing.md", "# Contributing\n")
        manifest = make_manifest(
            "sample",
            plugin_dir,
            [{"title": "Contributing", "path": "contributing.md", "audience": "contributor"}],
        )
        manager = DocsManager(FakePluginRegistry([manifest]), base_docs_path=str(tmp_path / "docs"))

        admin_tree = manager.build_tree(is_admin=True)
        contributor_ids = {item["id"] for item in admin_tree["sections"][2]["items"]}
        assert "plugin/sample/contributing" in contributor_ids

        user_tree = manager.build_tree(is_admin=False)
        assert all(
            "plugin/sample/contributing" not in {item["id"] for item in section["items"]}
            for section in user_tree["sections"]
        )

    def test_traversal_attack_rejected(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "evil"
        plugin_dir.mkdir(parents=True)
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        manifest = make_manifest(
            "evil", plugin_dir, [{"title": "Escape", "path": "../../secret.txt"}]
        )
        manager = DocsManager(FakePluginRegistry([manifest]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)

        assert tree["sections"][0]["items"] == []

    def test_missing_doc_file_skipped(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "sample"
        plugin_dir.mkdir(parents=True)
        manifest = make_manifest(
            "sample", plugin_dir, [{"title": "Missing", "path": "does/not/exist.md"}]
        )
        manager = DocsManager(FakePluginRegistry([manifest]), base_docs_path=str(tmp_path / "docs"))

        tree = manager.build_tree(is_admin=False)

        assert tree["sections"][0]["items"] == []


class TestGetContent:
    def test_returns_markdown_with_frontmatter_stripped(self, tmp_path):
        write(
            tmp_path / "docs" / "user" / "doc.md",
            "---\ntitle: Doc\norder: 1\n---\n# Doc\nBody text here.",
        )
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        content = manager.get_content("user/doc", is_admin=False)

        assert content["id"] == "user/doc"
        assert content["title"] == "Doc"
        assert "---" not in content["markdown"]
        assert "Body text here." in content["markdown"]

    def test_unknown_id_raises_not_found(self, tmp_path):
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        with pytest.raises(DocNotFoundError):
            manager.get_content("user/does-not-exist", is_admin=False)

    def test_developer_doc_forbidden_for_non_admin(self, tmp_path):
        write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        with pytest.raises(DocForbiddenError):
            manager.get_content("dev/ARCHITECTURE", is_admin=False)

    def test_developer_doc_allowed_for_admin(self, tmp_path):
        write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\nDetails.")
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        content = manager.get_content("dev/ARCHITECTURE", is_admin=True)

        assert content["title"] == "Architecture"
        assert "Details." in content["markdown"]

    def test_live_doc_raises_is_live_error(self, tmp_path):
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        with pytest.raises(DocIsLiveError):
            manager.get_content("live/hooks", is_admin=True)

    def test_live_doc_forbidden_before_live_check_for_non_admin(self, tmp_path):
        manager = DocsManager(FakePluginRegistry([]), base_docs_path=str(tmp_path / "docs"))

        with pytest.raises(DocForbiddenError):
            manager.get_content("live/hooks", is_admin=False)
