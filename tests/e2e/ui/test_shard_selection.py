from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_RUN = Path(__file__).resolve().parent / "run.py"
_HARNESS_DIR = _RUN.parents[1] / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

_spec = importlib.util.spec_from_file_location("e2e_ui_run_shards", _RUN)
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def _chunks():
    return run.chunked(run.discover_specs(), run.DEFAULT_CHUNK_SIZE)


@pytest.mark.parametrize("total", [1, 2, 3, 4])
def test_every_spec_lands_in_exactly_one_shard(total):
    seen = []
    for index in range(1, total + 1):
        for _, chunk in run.select_shard(_chunks(), index, total):
            seen.extend(chunk)
    assert sorted(seen) == run.discover_specs()


def test_single_shard_equals_unsharded_run():
    chunks = _chunks()
    assert run.select_shard(chunks, 1, 1) == list(enumerate(chunks, start=1))


def test_selection_is_stable():
    first = [run.select_shard(_chunks(), i, 3, {1: 1.5}) for i in (1, 2, 3)]
    second = [run.select_shard(_chunks(), i, 3, {1: 1.5}) for i in (1, 2, 3)]
    assert first == second


def test_chunk_numbers_are_global_and_unique():
    numbers = [n for i in (1, 2, 3) for n, _ in run.select_shard(_chunks(), i, 3)]
    assert sorted(numbers) == list(range(1, len(_chunks()) + 1))


def test_head_start_moves_work_off_shard_one():
    plain = run.shard_assignment(9, 3)
    loaded = run.shard_assignment(9, 3, {1: 1.5})
    assert plain.count(1) > loaded.count(1)


def test_chunk_size_change_still_covers_every_spec():
    chunks = run.chunked(run.discover_specs(), 5)
    seen = [n for i in (1, 2, 3) for _, c in run.select_shard(chunks, i, 3) for n in c]
    assert sorted(seen) == run.discover_specs()


@pytest.mark.parametrize("bad", ["0/3", "4/3", "1/0", "x", "1-3"])
def test_parse_shard_rejects_malformed(bad):
    with pytest.raises(Exception):
        run.parse_shard(bad)


def test_parse_shard_accepts_i_of_n():
    assert run.parse_shard("2/3") == (2, 3)
