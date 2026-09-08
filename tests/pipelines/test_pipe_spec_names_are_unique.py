"""A pipe declares each input, output and configuration name once.

The specs are the pipe's contract with everything that reads it by name:
`validate_pipe_configuration` fills defaults per spec, the pipeline graph
wires `input:` triples by output name, and the live Pipes reference renders
one table row per spec. A name declared twice is at best a copy-paste leftover
(the case that motivated this guard: `prompt_encoder` carried two `quantity`
config specs with different descriptions, and the reference page crashed on
the duplicate row key) and at worst two contradicting declarations of which
only the last survives.

Imports every pipe module on purpose, like the sibling JSON-defaults guard: a
sweep that skipped the pipes it could not load would miss exactly the
families that carry the most specs.
"""

from collections import Counter
from pathlib import Path

import pytest

from src.pipelines.catalog import PipeCatalog

REPO_ROOT = Path(__file__).resolve().parents[2]
MINIMUM_PIPES = 40


@pytest.fixture(scope="module")
def catalog():
    catalog = PipeCatalog(
        str(REPO_ROOT / "src" / "pipelines" / "pipes"),
        str(REPO_ROOT / "pipes" / "custom"),
    )
    catalog.discover_pipes()
    return catalog


def _duplicates(specs):
    return sorted(name for name, count in Counter(spec.name for spec in specs).items() if count > 1)


def test_the_catalog_actually_discovered_pipes(catalog):
    assert len(catalog.pipes) >= MINIMUM_PIPES


@pytest.mark.parametrize("kind", ["configuration", "inputs", "outputs"])
def test_every_pipe_declares_each_spec_name_once(catalog, kind):
    offenders = []
    for name in sorted(catalog.pipes):
        duplicates = _duplicates(getattr(catalog.pipes[name], kind)() or [])
        if duplicates:
            offenders.append(f"{name} {kind}: {', '.join(duplicates)}")

    assert not offenders, "duplicate spec names:\n  " + "\n  ".join(offenders)
