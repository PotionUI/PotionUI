from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_FILES = ["requirements.txt", "requirements-cpu.txt", "requirements-ci.txt"]
CONSTRAINTS_FILE = "constraints.txt"
OFFICIAL = "nvidia-ml-py"
ABANDONED = "nvidia-ml-py3"
FIRST_RELEASE_LOADING_SYSTEM32 = Version("10.418.84")
SHIPPED_IN_0_0_18 = Version("13.610.43")


def _requirements(path: Path) -> dict[str, Requirement]:
    found = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        requirement = Requirement(line)
        found[canonicalize_name(requirement.name)] = requirement
    return found


@pytest.mark.parametrize("name", INSTALL_FILES + [CONSTRAINTS_FILE])
def test_the_abandoned_bindings_are_never_installed(name):
    assert ABANDONED not in _requirements(REPO_ROOT / name)


@pytest.mark.parametrize("name", INSTALL_FILES)
def test_the_official_bindings_load_nvml_from_system32(name):
    floor = _floor(name)

    assert floor >= FIRST_RELEASE_LOADING_SYSTEM32


@pytest.mark.parametrize("name", INSTALL_FILES)
def test_the_floor_upgrades_an_existing_install_in_place(name):
    assert _floor(name) > SHIPPED_IN_0_0_18


@pytest.mark.parametrize("name", INSTALL_FILES)
def test_the_constraints_pin_satisfies_the_floor(name):
    pin = _requirements(REPO_ROOT / CONSTRAINTS_FILE)[OFFICIAL]
    pinned = Version(next(iter(pin.specifier)).version)

    assert pinned in _requirements(REPO_ROOT / name)[OFFICIAL].specifier


def _floor(name: str) -> Version:
    requirement = _requirements(REPO_ROOT / name)[OFFICIAL]
    floors = [Version(spec.version) for spec in requirement.specifier if spec.operator == ">="]
    assert floors, f"{name} needs a >= floor on {OFFICIAL}"
    return max(floors)
