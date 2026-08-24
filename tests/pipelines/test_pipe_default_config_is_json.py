"""A pipe's default configuration must be JSON.

`get_default_config()` used to be private to this process: `PipelineExecutor`
merged it under the shipped config at execution time and whatever it contained
went straight into the pipe. It is not private any more - those defaults are
part of the effective configuration an execution package carries, so a value
that cannot be expressed in JSON cannot cross to a worker.

The case that motivated this guard: `detailer/sdxl` defaulted `box_color` to a
Python tuple, which reads as a list once it has been through JSON. Its own
`PipeConfigSpec` already declared the parameter as a `list`, and the sibling
video detailer already used one, so the tuple was the outlier - but nothing
caught it, because the pipes that hold it only import where cv2 does.

This imports every pipe module, which is the point: a sweep that silently
skipped the pipes it could not load is how the tuple survived in the first
place.
"""

import json
from pathlib import Path

import pytest

from src.pipelines.catalog import PipeCatalog

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A floor, not a count - it fails a catalog that silently discovered nothing
#: (which would make every assertion below vacuous) without needing an edit
#: every time a pipe is added.
MINIMUM_PIPES = 40

JSON_SCALARS = (str, int, float, bool, type(None))


def _non_json(value, path, found):
    """Every leaf under `value` that JSON has no representation for."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                found.append(f"{path}: non-string key {key!r} ({type(key).__name__})")
            _non_json(item, f"{path}.{key}", found)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _non_json(item, f"{path}[{index}]", found)
    elif not isinstance(value, JSON_SCALARS):
        found.append(f"{path}: {type(value).__name__} {value!r}")


@pytest.fixture(scope="module")
def catalog():
    catalog = PipeCatalog(
        str(REPO_ROOT / "src" / "pipelines" / "pipes"),
        str(REPO_ROOT / "pipes" / "custom"),
    )
    catalog.discover_pipes()
    return catalog


def test_the_catalog_actually_discovered_pipes(catalog):
    assert len(catalog.pipes) >= MINIMUM_PIPES, (
        f"only {len(catalog.pipes)} pipes discovered - the assertions in this "
        f"module would pass vacuously"
    )


def test_every_pipe_default_config_is_json(catalog):
    offenders = []
    for name in sorted(catalog.pipes):
        found = []
        _non_json(catalog.pipes[name].get_default_config() or {}, "config", found)
        offenders.extend(f"{name} {entry}" for entry in found)

    assert not offenders, (
        "these pipe defaults cannot travel in an execution package:\n  "
        + "\n  ".join(offenders)
    )


def test_every_pipe_config_spec_default_is_json(catalog):
    """The second defaults source: `validate_pipe_configuration` fills a
    spec's default for any parameter the merged config lacks."""
    offenders = []
    for name in sorted(catalog.pipes):
        for spec in catalog.pipes[name].configuration() or []:
            found = []
            _non_json(spec.default, f"spec.{spec.name}", found)
            offenders.extend(f"{name} {entry}" for entry in found)

    assert not offenders, (
        "these pipe configuration-spec defaults cannot travel in an execution "
        "package:\n  " + "\n  ".join(offenders)
    )


def test_defaults_survive_a_json_round_trip_unchanged(catalog):
    """Stronger than serializability: a value that *encodes* but comes back as
    something else (a tuple returning as a list) means the worker runs with a
    different value than this host does."""
    for name in sorted(catalog.pipes):
        defaults = catalog.pipes[name].get_default_config() or {}
        assert json.loads(json.dumps(defaults)) == defaults, name
