"""`RecipeCatalog` discovery: valid recipes load, broken ones report an issue
without breaking the rest of the catalog, and duplicate ids collide loudly.
Mirrors PresetTemplateLoader's "one broken file never crashes the catalog"
guarantee, and content/presets' marketplace/local two-root convention.
"""

from src.features.setup.recipe_catalog import RecipeCatalog

VALID_YAML = """
schema_version: 1
id: "{recipe_id}"
version: 1
name: "Test Recipe"
engine: "native"
plugins: []
presets:
  - preset_id: "PRESET1"
steps:
  - key: "backend.ensure"
    kind: "backend.ensure"
    title: "Ensure backend"
    params:
      engine: "native"
"""


def _write(base, root, filename, recipe_id):
    root_dir = base / root
    root_dir.mkdir(parents=True, exist_ok=True)
    (root_dir / filename).write_text(VALID_YAML.format(recipe_id=recipe_id))


def test_empty_directory_yields_empty_catalog(tmp_path):
    catalog = RecipeCatalog(str(tmp_path))
    assert catalog.list_recipes() == []
    assert catalog.load_errors == {}


def test_missing_directory_yields_empty_catalog_not_a_crash(tmp_path):
    catalog = RecipeCatalog(str(tmp_path / "does-not-exist"))
    assert catalog.list_recipes() == []


def test_loads_a_valid_recipe(tmp_path):
    _write(tmp_path, "marketplace", "a.yml", "a")
    catalog = RecipeCatalog(str(tmp_path))

    recipes = catalog.list_recipes()
    assert [r.id for r in recipes] == ["a"]
    assert catalog.get_recipe("a") is not None
    assert catalog.get_recipe("a", version=1) is not None
    assert catalog.get_recipe("a", version=2) is None
    assert catalog.get_recipe("nonexistent") is None


def test_invalid_recipe_reports_error_without_crashing_catalog(tmp_path):
    marketplace = tmp_path / "marketplace"
    marketplace.mkdir(parents=True)
    (marketplace / "broken.yml").write_text("id: 123\nsteps: []\n")  # missing required fields
    _write(tmp_path, "marketplace", "good.yml", "good")

    catalog = RecipeCatalog(str(tmp_path))

    assert [r.id for r in catalog.list_recipes()] == ["good"]
    assert len(catalog.load_errors) == 1
    broken_path = str(marketplace / "broken.yml")
    assert broken_path in catalog.load_errors
    assert catalog.load_errors[broken_path]  # non-empty issue list


def test_unparsable_yaml_reports_error(tmp_path):
    marketplace = tmp_path / "marketplace"
    marketplace.mkdir(parents=True)
    (marketplace / "bad.yml").write_text("id: [unterminated\n")
    catalog = RecipeCatalog(str(tmp_path))

    assert catalog.list_recipes() == []
    assert len(catalog.load_errors) == 1


def test_duplicate_recipe_id_across_files_reports_error(tmp_path):
    marketplace = tmp_path / "marketplace"
    _write(tmp_path, "marketplace", "a.yml", "dup")
    _write(tmp_path, "marketplace", "b.yml", "dup")

    catalog = RecipeCatalog(str(tmp_path))

    # One file wins (sorted glob order: a.yml first), the other is reported.
    assert [r.id for r in catalog.list_recipes()] == ["dup"]
    assert len(catalog.load_errors) == 1
    b_path = str(marketplace / "b.yml")
    assert "Duplicate recipe id" in catalog.load_errors[b_path][0]


def test_reload_picks_up_filesystem_changes(tmp_path):
    catalog = RecipeCatalog(str(tmp_path))
    assert catalog.list_recipes() == []

    _write(tmp_path, "marketplace", "a.yml", "a")
    # Without an explicit reload, the lazy-loaded cache is unaware of the new file.
    assert catalog.list_recipes() == []

    catalog.reload()
    assert [r.id for r in catalog.list_recipes()] == ["a"]


def test_scans_both_marketplace_and_local_roots(tmp_path):
    _write(tmp_path, "marketplace", "a.yml", "a")
    _write(tmp_path, "local", "b.yml", "b")

    catalog = RecipeCatalog(str(tmp_path))

    assert [r.id for r in catalog.list_recipes()] == ["a", "b"]
    assert catalog.load_errors == {}


def test_local_recipe_id_colliding_with_marketplace_reports_error_marketplace_wins(tmp_path):
    marketplace = tmp_path / "marketplace"
    _write(tmp_path, "marketplace", "a.yml", "dup")
    _write(tmp_path, "local", "a.yml", "dup")

    catalog = RecipeCatalog(str(tmp_path))

    recipes = catalog.list_recipes()
    assert [r.id for r in recipes] == ["dup"]
    assert recipes[0].source_path == str(marketplace / "a.yml")

    assert len(catalog.load_errors) == 1
    local_path = str(tmp_path / "local" / "a.yml")
    assert local_path in catalog.load_errors
    assert "Duplicate recipe id" in catalog.load_errors[local_path][0]
