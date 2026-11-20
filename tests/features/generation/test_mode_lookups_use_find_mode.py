import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCANNED = ("generation", "forms", "kits", "cloud", "presets", "sessions", "phrasebook", "prompt")
LOOKUP = re.compile(r"\.modes\[|\.modes\.get\(|modes or \{\}\)\.get\(")

ALLOWED = {
    "src/features/presets/loader.py": (
        2, "builds and merges the generation-mode catalogue while loading; editor modes are not in it"
    ),
    "src/features/presets/extensions.py": (1, "plugin extensions copy the named generation modes they target"),
    "src/features/presets/editor_modes.py": (1, "a derived editor mode names the generation mode it derives from"),
    "src/features/presets/overlays.py": (1, "a mirror selects among generation modes by name"),
    "src/features/presets/linter.py": (1, "lint of a plugin extension names a generation mode"),
    "src/features/presets/operations/form_overrides.py": (
        1, "admin overrides are stored per generation mode; an editor mode inherits its from mode's"
    ),
    "src/features/presets/operations/query.py": (
        2, "the pipeline preview alias lookup of a plain mode, which also accepts GenerationMode enum keys"
    ),
}


def scan():
    found = {}
    for package in SCANNED:
        for path in (ROOT / "src" / "features" / package).rglob("*.py"):
            count = len(LOOKUP.findall(path.read_text(encoding="utf-8")))
            if count:
                found[path.relative_to(ROOT).as_posix()] = count
    return found


def test_no_generation_path_module_looks_a_mode_up_by_key_without_find_mode():
    found = scan()
    unexpected = {path: count for path, count in found.items() if path not in ALLOWED or count > ALLOWED[path][0]}
    assert unexpected == {}, f"look the mode up with find_mode(template, key), or allow-list it with a reason: {unexpected}"


def test_the_allow_list_has_no_stale_entries():
    found = scan()
    stale = [path for path, (count, _) in ALLOWED.items() if found.get(path, 0) != count]
    assert stale == []
