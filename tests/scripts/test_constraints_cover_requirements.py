"""Lint check: every direct dependency in requirements.txt / requirements-dev.txt
has a pinned transitive version in constraints.txt.

This is the reproducibility guard for the `pip install -r requirements.txt
-c constraints.txt` install contract: if someone adds a new
direct dependency to requirements*.txt without re-freezing constraints.txt,
this test fails loudly instead of silently degrading to an unpinned install.

Stdlib-only, no repo imports — mirrors the tests/architecture/ idiom of
parsing project files directly rather than importing runtime code.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REQUIREMENTS_FILES = ["requirements.txt", "requirements-dev.txt"]
CONSTRAINTS_FILE = "constraints.txt"

# Deliberately absent from constraints.txt — see its header comment for why.
EXCLUDED_FROM_CONSTRAINTS = {"sageattention"}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalize(name: str) -> str:
    """PEP 503 normalization: case- and separator-insensitive."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _iter_requirement_lines(path: Path):
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        yield line


def _direct_requirement_names(path: Path) -> set[str]:
    names = set()
    for line in _iter_requirement_lines(path):
        line = line.split(";", 1)[0].strip()  # drop environment markers
        line = line.split("[", 1)[0].strip()  # drop extras, e.g. pydantic[email]
        match = _NAME_RE.match(line)
        assert match, f"Could not parse a package name out of requirement line: {line!r}"
        names.add(_normalize(match.group(0)))
    return names


def _constraints_pins() -> dict[str, str]:
    path = REPO_ROOT / CONSTRAINTS_FILE
    pins: dict[str, str] = {}
    for line in _iter_requirement_lines(path):
        if "@" in line:
            # VCS/URL requirement (none expected — constraints.txt should
            # only ever hold `name==version` pins).
            name = line.split("@", 1)[0].strip()
            pins[_normalize(name)] = line
            continue
        assert "==" in line, f"constraints.txt line is not a `name==version` pin: {line!r}"
        name, version = line.split("==", 1)
        pins[_normalize(name.strip())] = version.strip()
    return pins


def test_constraints_file_exists():
    assert (REPO_ROOT / CONSTRAINTS_FILE).is_file()


def test_every_direct_requirement_is_pinned_in_constraints():
    pins = _constraints_pins()
    missing = []
    for req_file in REQUIREMENTS_FILES:
        for name in _direct_requirement_names(REPO_ROOT / req_file):
            if name in EXCLUDED_FROM_CONSTRAINTS:
                continue
            if name not in pins:
                missing.append((req_file, name))
    assert not missing, (
        "requirements*.txt direct dependencies missing from constraints.txt "
        f"(re-freeze the venv to regenerate): {missing}"
    )


def test_constraints_has_no_duplicate_pins():
    path = REPO_ROOT / CONSTRAINTS_FILE
    seen: dict[str, str] = {}
    duplicates = []
    for line in _iter_requirement_lines(path):
        name = line.split("==", 1)[0].split("@", 1)[0].strip()
        normalized = _normalize(name)
        if normalized in seen and seen[normalized] != line:
            duplicates.append((normalized, seen[normalized], line))
        seen[normalized] = line
    assert not duplicates, f"constraints.txt pins the same package twice: {duplicates}"


def test_excluded_packages_are_actually_absent_from_requirements_direct_deps():
    """Guards the exclusion list itself: if sageattention (or a future
    exclusion) ever becomes a direct requirements*.txt dependency, this
    should fail so the exclusion gets reconsidered rather than silently
    leaving it unpinned."""
    for name in EXCLUDED_FROM_CONSTRAINTS:
        for req_file in REQUIREMENTS_FILES:
            assert name not in _direct_requirement_names(REPO_ROOT / req_file), (
                f"{name!r} is now a direct dependency in {req_file} but is still "
                "in EXCLUDED_FROM_CONSTRAINTS — pin it in constraints.txt and "
                "remove the exclusion."
            )
