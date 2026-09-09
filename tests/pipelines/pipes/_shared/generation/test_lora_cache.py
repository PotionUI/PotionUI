"""The bounded RAM cache for parsed LoRA state dicts.

Before it existed, every ``load_lora_stack`` (a stack change on a cached DiT)
and every ``load_windowed_lora_stack`` (once per generation) re-read and
re-parsed the file from disk.
"""

from __future__ import annotations

import os
import types

import pytest
import torch

from src.pipelines.pipes._shared.generation import loader_helpers as lh
from src.pipelines.pipes._shared.generation.lora_cache import (
    LoraStateDictCache,
    file_identity,
    lora_state_dict_cache,
)


@pytest.fixture
def lora_file(tmp_path):
    path = tmp_path / "style.safetensors"
    path.write_bytes(b"x" * 64)
    return str(path)


class _Loader:
    """Stands in for the parse; counts how often it actually ran."""

    def __init__(self, nbytes: int = 100) -> None:
        self.calls = 0
        self._nbytes = nbytes

    def __call__(self):
        self.calls += 1
        return {"w": torch.zeros(2, 2)}, self._nbytes


class TestIdentity:
    def test_a_missing_file_has_no_identity(self, tmp_path):
        assert file_identity(str(tmp_path / "gone.safetensors")) is None

    def test_identity_is_path_mtime_and_size(self, lora_file):
        path, mtime_ns, size = file_identity(lora_file)
        assert path == os.path.realpath(lora_file)
        assert size == 64
        assert mtime_ns == os.stat(lora_file).st_mtime_ns


class TestHitAndMiss:
    def test_an_unchanged_file_is_parsed_once(self, lora_file):
        cache, load = LoraStateDictCache(), _Loader()

        first, _bytes, hit_a = cache.get_or_load(lora_file, load)
        second, _bytes, hit_b = cache.get_or_load(lora_file, load)

        assert (hit_a, hit_b) == (False, True)
        assert second is first
        assert load.calls == 1
        assert (cache.hits, cache.misses) == (1, 1)

    def test_a_rewritten_file_is_reparsed_even_at_the_same_path(self, lora_file):
        """A LoRA being iterated on is rewritten under the same name; serving
        the previous parse for it generates against the old weights."""
        cache, load = LoraStateDictCache(), _Loader()
        cache.get_or_load(lora_file, load)

        with open(lora_file, "wb") as fh:
            fh.write(b"y" * 128)                      # size and mtime both move

        _sd, _bytes, hit = cache.get_or_load(lora_file, load)
        assert hit is False
        assert load.calls == 2

    def test_a_touched_file_of_the_same_size_is_reparsed(self, lora_file):
        cache, load = LoraStateDictCache(), _Loader()
        cache.get_or_load(lora_file, load)
        stat = os.stat(lora_file)
        os.utime(lora_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))

        _sd, _bytes, hit = cache.get_or_load(lora_file, load)
        assert hit is False
        assert load.calls == 2

    def test_a_file_that_cannot_be_stat_d_is_never_stored_or_served(self, tmp_path):
        """No identity means no proof of freshness, so the caller just reads --
        which is also what keeps every existing fake-path test honest."""
        cache, load = LoraStateDictCache(), _Loader()
        missing = str(tmp_path / "gone.safetensors")

        cache.get_or_load(missing, load)
        cache.get_or_load(missing, load)

        assert load.calls == 2
        assert cache.resident_bytes == 0
        assert (cache.hits, cache.misses) == (0, 0)


class TestBounds:
    def _file(self, tmp_path, name, size):
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        return str(path)

    def test_the_least_recently_used_entry_is_dropped_first(self, tmp_path):
        cache = LoraStateDictCache(capacity_bytes=250)
        a = self._file(tmp_path, "a.safetensors", 1)
        b = self._file(tmp_path, "b.safetensors", 2)
        c = self._file(tmp_path, "c.safetensors", 3)
        load = _Loader(nbytes=100)

        cache.get_or_load(a, load)
        cache.get_or_load(b, load)
        cache.get_or_load(a, load)          # a is now the most recent
        cache.get_or_load(c, load)          # evicts b

        assert cache.resident_bytes == 200
        assert cache.get_or_load(a, load)[2] is True
        assert cache.get_or_load(b, load)[2] is False

    def test_a_file_larger_than_the_whole_cache_is_not_admitted(self, tmp_path):
        cache = LoraStateDictCache(capacity_bytes=50)
        huge = self._file(tmp_path, "huge.safetensors", 1)
        load = _Loader(nbytes=500)

        cache.get_or_load(huge, load)
        cache.get_or_load(huge, load)

        assert cache.resident_bytes == 0
        assert load.calls == 2


class TestThroughTheLoader:
    @pytest.fixture(autouse=True)
    def _clear(self):
        lora_state_dict_cache().clear()
        yield
        lora_state_dict_cache().clear()

    def test_a_second_stack_load_of_an_unchanged_file_does_not_re_read(self, monkeypatch, lora_file):
        reads = []

        def fake_load(path, device="cpu"):
            reads.append(path)
            return ({"w": torch.zeros(8, 8)}, {})

        monkeypatch.setattr(lh, "load_torch_file", fake_load)
        entry = [{"file_path": lora_file, "weight": 1.0}]

        _stack, [first] = lh.load_lora_stack_timed(entry)
        _stack, [second] = lh.load_lora_stack_timed(entry)

        assert reads == [lora_file]
        assert (first.cached, second.cached) == (False, True)
        assert second.nbytes == first.nbytes

    def test_the_read_mark_says_whether_it_came_from_the_cache(self, monkeypatch, lora_file):
        marks = []
        monkeypatch.setattr(lh, "get_profiler", lambda: types.SimpleNamespace(
            mark=lambda event, **fields: marks.append((event, fields))))
        monkeypatch.setattr(lh, "load_torch_file",
                            lambda path, device="cpu": ({"w": torch.zeros(8, 8)}, {}))
        entry = [{"file_path": lora_file, "weight": 1.0}]

        lh.load_lora_stack(entry)
        lh.load_lora_stack(entry)

        assert [f["cached"] for e, f in marks if e == "lora.file_read"] == [False, True]

    def test_a_windowed_stack_shares_the_cache_with_a_baked_one(self, monkeypatch, lora_file):
        reads = []

        def fake_load(path, device="cpu"):
            reads.append(path)
            return ({"w": torch.zeros(8, 8)}, {})

        monkeypatch.setattr(lh, "load_torch_file", fake_load)

        lh.load_lora_stack([{"file_path": lora_file, "weight": 1.0}])
        lh.load_windowed_lora_stack(
            [{"file_path": lora_file, "weight": 1.0, "window": _window()}])

        assert reads == [lora_file]


def _window():
    from src.platform.runtime.native.lora import LoraStepWindow
    return LoraStepWindow(1, 2)


class TestTheCachedDictSurvivesRepeatedUse:
    """The cache hands the SAME dict and the SAME tensors to every generation.
    Anything downstream that consumed or mutated them would give a LoRA that
    works on the first generation and degrades on the next."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        lora_state_dict_cache().clear()
        yield
        lora_state_dict_cache().clear()

    def _serve(self, monkeypatch, lora_file, state_dict):
        monkeypatch.setattr(lh, "load_torch_file", lambda path, device="cpu": (state_dict, {}))
        return [{"file_path": lora_file, "weight": 0.8}]

    def test_three_generations_off_one_cached_dict_apply_identically(self, monkeypatch, lora_file):
        from src.platform.runtime.native.lora.apply import apply_loras_with_report
        from tests.platform.runtime.native.lora.test_lora import _build, _kohya_lora

        source = _kohya_lora()
        pristine = {k: v.clone() for k, v in source.items()}
        entry = self._serve(monkeypatch, lora_file, source)

        target = "double_blocks.0.img_attn.qkv"
        patched_counts, patches = [], []
        for _generation in range(3):
            module = _build()   # a fresh, independently randomised base each time
            qkv = dict(module.named_modules())[target]
            base = qkv.weight.detach().clone()
            stack, _reads = lh.load_lora_stack_timed(entry)
            patched, unmatched, [report] = apply_loras_with_report(
                module, stack, names=[lora_file])
            patched_counts.append((patched, len(unmatched), report.matched_params))
            # The DELTA the adapter contributed, not the absolute weight: the
            # base differs per generation, the patch must not.
            patches.append((qkv.weight.detach() - base).clone())

        assert patched_counts[0][0] > 0
        assert patched_counts[1] == patched_counts[0] == patched_counts[2], (
            "a later generation matched a different number of params -- the "
            "mapping consumed or mutated the cached dict")
        assert torch.allclose(patches[1], patches[0], atol=1e-6)
        assert torch.allclose(patches[2], patches[0], atol=1e-6)

    def test_the_cached_dict_is_byte_identical_after_being_applied(self, monkeypatch, lora_file):
        from src.platform.runtime.native.lora.apply import apply_loras_with_report
        from tests.platform.runtime.native.lora.test_lora import _build, _kohya_lora

        source = _kohya_lora()
        pristine = {k: v.clone() for k, v in source.items()}
        entry = self._serve(monkeypatch, lora_file, source)

        stack, _reads = lh.load_lora_stack_timed(entry)
        apply_loras_with_report(_build(), stack, names=[lora_file])

        served, _n, hit = lora_state_dict_cache().get_or_load(lora_file, lambda: (None, 0))
        assert hit is True
        assert sorted(served) == sorted(pristine), "keys were consumed out of the cached dict"
        assert all(torch.equal(served[k], pristine[k]) for k in pristine)

    def test_a_rewritten_file_is_re_read_even_though_the_path_is_unchanged(
        self, monkeypatch, lora_file,
    ):
        """Retraining a character LoRA overwrites it in place. Serving the
        previous parse would generate against the old adapter forever."""
        from tests.platform.runtime.native.lora.test_lora import _kohya_lora

        first, second = _kohya_lora(seed=1), _kohya_lora(seed=2)
        served = {"sd": first}
        monkeypatch.setattr(lh, "load_torch_file", lambda path, device="cpu": (served["sd"], {}))
        entry = [{"file_path": lora_file, "weight": 0.8}]

        [(one, _w)] = lh.load_lora_stack(entry)
        served["sd"] = second
        with open(lora_file, "wb") as fh:
            fh.write(b"z" * 256)
        [(two, _w)] = lh.load_lora_stack(entry)

        key = "lora_unet_double_blocks_0_img_attn_qkv.lora_up.weight"
        assert not torch.equal(one[key], two[key])
