import filecmp
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO_ROOT / "content" / "plugins"
MARKETPLACE = PLUGINS_DIR / "marketplace"
SCRIPT = REPO_ROOT / "scripts" / "build-plugins.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (PLUGINS_DIR / "node_modules").is_dir(),
    reason="plugin build toolchain is not installed",
)


def _stage(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    (root / "frontend").mkdir()
    for module in sorted(SCRIPT.parent.glob("*.mjs")):
        shutil.copy(module, root / "scripts" / module.name)
    plugins = root / "content" / "plugins"
    shutil.copytree(PLUGINS_DIR / "sdk", plugins / "sdk")
    shutil.copy(PLUGINS_DIR / "package.json", plugins / "package.json")
    shutil.copytree(PLUGINS_DIR / "node_modules", plugins / "node_modules", symlinks=True)
    for plugin in sorted(MARKETPLACE.iterdir()):
        if (plugin / "frontend" / "src").is_dir() and (plugin / "manifest.yml").is_file():
            target = plugins / "marketplace" / plugin.name
            (target / "frontend").mkdir(parents=True)
            shutil.copy(plugin / "manifest.yml", target / "manifest.yml")
            shutil.copytree(plugin / "frontend" / "src", target / "frontend" / "src")
    return root


def _build(root: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(root / "scripts" / SCRIPT.name), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _dist_files(marketplace: Path) -> dict[str, Path]:
    return {
        str(path.relative_to(marketplace)): path
        for path in marketplace.glob("*/frontend/dist/*")
        if path.is_file()
    }


def test_committed_marketplace_dists_match_a_fresh_build(tmp_path):
    root = _stage(tmp_path)
    result = _build(root, root)
    assert result.returncode == 0, result.stderr

    fresh = _dist_files(root / "content" / "plugins" / "marketplace")
    committed = _dist_files(MARKETPLACE)
    assert set(fresh) == set(committed)
    stale = sorted(name for name, path in fresh.items() if not filecmp.cmp(path, committed[name], shallow=False))
    assert stale == []


def test_build_output_does_not_depend_on_the_working_directory(tmp_path):
    first = _stage(tmp_path / "first")
    second = _stage(tmp_path / "second")
    (second / "elsewhere").mkdir()
    assert _build(first, first).returncode == 0
    assert _build(second, second / "elsewhere").returncode == 0

    built_first = _dist_files(first / "content" / "plugins" / "marketplace")
    built_second = _dist_files(second / "content" / "plugins" / "marketplace")
    assert set(built_first) == set(built_second)
    differing = sorted(name for name in built_first if not filecmp.cmp(built_first[name], built_second[name], shallow=False))
    assert differing == []


def test_failed_plugins_are_named_in_the_summary(tmp_path):
    root = _stage(tmp_path)
    broken = root / "content" / "plugins" / "marketplace" / "example-field" / "frontend" / "src" / "ExampleStarsField.svelte"
    broken.write_text("<script>\n  let { a } = $props();\n  export let b;\n</script>\n", encoding="utf-8")
    result = _build(root, root)
    assert result.returncode == 1
    assert "Failed plugins (1):" in result.stderr
    assert "example-field:" in result.stderr
