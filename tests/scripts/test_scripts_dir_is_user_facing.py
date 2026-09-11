"""Guard: scripts/ stays user-facing only.

scripts/ is documented (CLAUDE.md's "Scripts" section) as the set of
commands an end user of PotionUI is expected to run directly. Developer
tooling (chat scenario replay, the per-plugin pytest runner, ...) belongs
under tests/ instead, next to the code it exercises. This test enforces an
explicit allowlist rather than a heuristic, so adding a new user-facing
script is a one-line addition here, and anything else dropped into
scripts/ fails the build instead of silently becoming a second home for
developer tooling.

Stdlib-only, no repo imports — mirrors the tests/architecture/ idiom of
parsing project files directly rather than importing runtime code.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

ALLOWED_BASENAMES = {
    "potionui_cli.py",
    "reset_password.py",
    "rotate_secret_key.py",
    "profile_report.py",
    "preset_lint.py",
    "preset_new.py",
    "preset_render.py",
    "preset_test_suite.py",
    "preset_styles_render.py",
    "recipe_lint.py",
    "docs_lint.py",
    "pipes_reference.py",
    "build-plugins.mjs",
    "h3_extract_time_embedder.py",
}

_IGNORED_BASENAMES_PREFIXES = ("README",)
_CHECKED_SUFFIXES = (".py", ".sh", ".mjs")


def _scripts_entries():
    for entry in SCRIPTS_DIR.iterdir():
        if entry.name == "__pycache__" or entry.name.startswith(_IGNORED_BASENAMES_PREFIXES):
            continue
        if entry.is_file() and entry.suffix in _CHECKED_SUFFIXES:
            yield entry


def test_scripts_dir_exists():
    assert SCRIPTS_DIR.is_dir()


def test_scripts_dir_contains_only_allowed_user_facing_scripts():
    found = {entry.name for entry in _scripts_entries()}
    unexpected = sorted(found - ALLOWED_BASENAMES)
    assert not unexpected, (
        "scripts/ contains file(s) not on the user-facing allowlist: "
        f"{unexpected}. Developer tooling belongs under tests/, not scripts/ — "
        "move it, or add it to ALLOWED_BASENAMES here if it is genuinely "
        "user-facing."
    )


def test_allowlist_entries_all_still_exist():
    """Guards the allowlist itself: an entry that no longer exists in
    scripts/ should be removed rather than silently going stale."""
    missing = sorted(name for name in ALLOWED_BASENAMES if not (SCRIPTS_DIR / name).is_file())
    assert not missing, f"ALLOWED_BASENAMES names file(s) no longer in scripts/: {missing}"
