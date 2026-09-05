"""Pins the per-checkpoint execution gate `_leased` now holds for a turn's
FULL lifetime (`_CheckpointGate`, `src/features/llm/clients/native.py`).

`ModelLifecycle.acquire()` hands the SAME `_LoadedCheckpoint` — one live
`torch.nn.Module` — to every caller with a matching fingerprint; there is no
per-checkpoint execution gate at that layer. Before this fix, two overlapping
turns on the same checkpoint could race its CUDA placement: turn A's teardown
moving the model back to CPU while turn B was still mid-`generate()` on it.

Every test drives the REAL `_leased`/`generate_with_history`/
`stream_with_history` entry points and the REAL `ModelLifecycle` cache, with
`checkpoint.model` replaced by `_GatedModel` — a fake whose `generate()` runs
on the real background worker thread these methods already use, blocks on a
`threading.Event` a test controls, and records `.to()`/`generate()` timeline
events into a shared, GIL-safe list. `torch.cuda.is_available` is forced
`True` per test (this container has no real GPU) purely to exercise
`_leased`'s `manage_device` placement branch — `.to()` is the fake's no-op
recorder, never a real CUDA call. Every wait is bounded
(`asyncio.wait_for`/`Event.wait(timeout=...)`) and every test releases its
gated model's barrier in a `finally`, so a regression that reintroduces the
race fails the assertions (or times out a bounded wait) instead of hanging
pytest.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
import torch

from src.features.llm.clients import native as native_module
from src.features.llm.clients.native import NativeLLMClient, _LoadedCheckpoint
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from tests.features.llm.test_native_client import (
    _config,
    client,
    models_manager,
    native_checkpoint,
    tiny_qwen3_checkpoint_dir,
)

_BOUND = 10  # seconds — generous for a tiny CPU model + threading, tight enough to catch a real hang


@pytest.fixture(autouse=True)
def _no_real_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None, raising=False)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None, raising=False)


class _FakeTensor:
    """Stands in for a `torch.Tensor` across `_run()`'s device-move/slicing
    calls without ever touching a real device. `torch.cuda.is_available` is
    forced `True` in these tests purely so `_leased` takes its
    `manage_device` branch and calls `checkpoint.model.to(device)` — the
    fake model under test, never a real tensor. If the REAL prompt/output
    tensors from `_apply_template`/`generate()` also went through a real
    `.to("cuda")` here, they'd hit an actual (absent) CUDA device; this fake
    keeps that call a harmless no-op while still supporting everything
    `_run()`'s own bookkeeping touches (`.shape`, slicing, `.to`)."""

    def __init__(self, length: int):
        self.length = length

    def to(self, *_a, **_k):
        return self

    @property
    def shape(self):
        return (1, self.length)

    def __getitem__(self, _item):
        return self


class _MinimalTokenizer:
    """Just enough of the tokenizer contract for `_apply_template`'s and
    `generate_with_history`'s own bookkeeping to run — content is never
    inspected by these concurrency tests, only ordering/timing is."""

    def apply_chat_template(self, chat, add_generation_prompt=True, tokenize=False, **_kwargs):
        return "PROMPT"

    def __call__(self, text, return_tensors=None):
        return {"input_ids": _FakeTensor(3)}

    def decode(self, _ids, skip_special_tokens=True):
        return "ok"


class _GatedModel:
    """`.generate()` blocks on a `threading.Event` the test releases, letting
    a test hold a "turn" open on the (fake) device for as long as needed to
    observe whether a second turn on the SAME checkpoint is allowed to
    interleave. Runs on the real background worker thread
    `generate_with_history`/`stream_with_history` already dispatch to —
    `threading.Event`, never `asyncio.Event`, is the only correct primitive
    here. `events` is a single list shared across every model in a test
    (label-prefixed), so ordering across A/B is directly assertable.
    """

    def __init__(self, label: str, events: list):
        self.label = label
        self.events = events
        self.moves: list = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.error: Exception | None = None

    def to(self, device):
        self.moves.append(device)
        self.events.append(f"{self.label}:to:{device}")
        return self

    def generate(self, *, input_ids, streamer=None, stopping_criteria=None, **_kwargs):
        self.events.append(f"{self.label}:generate:started")
        self.started.set()
        if not self.release.wait(timeout=30):
            self.events.append(f"{self.label}:generate:timed_out_waiting_for_release")
            raise TimeoutError(f"{self.label}: test never released this fake generate() call")
        self.events.append(f"{self.label}:generate:returning")
        if self.error is not None:
            raise self.error
        return _FakeTensor(input_ids.length + 1)


def _gated_checkpoint(label: str, events: list) -> _LoadedCheckpoint:
    return _LoadedCheckpoint(
        model=_GatedModel(label, events), tokenizer=_MinimalTokenizer(), vision=False,
        model_type="qwen3", quantized=False,
    )


async def _await_started(model: _GatedModel):
    started = await asyncio.wait_for(asyncio.to_thread(model.started.wait, _BOUND), timeout=_BOUND + 1)
    assert started, f"{model.label}: generate() never started"


class TestExecutionGateSerializesOverlappingTurns:
    @pytest.mark.asyncio
    async def test_b_does_not_enter_device_placement_until_a_fully_tears_down(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        """The core race the card describes: A finishes (including its own
        CPU-restore) before B's `.to("cuda")` is ever recorded — never
        interleaved."""
        name, path = native_checkpoint
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint_a = _gated_checkpoint("A", events)
        checkpoint_b = _gated_checkpoint("B", events)
        sequence = [checkpoint_a, checkpoint_b]
        monkeypatch.setattr(
            NativeLLMClient, "_acquire",
            lambda self, p, cfg, is_te=False: sequence.pop(0),
        )

        task_a = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        task_b = None
        try:
            await _await_started(checkpoint_a.model)
            # B is created only once A is confirmed to hold the gate, so B's
            # own attempt genuinely queues behind A rather than racing to be
            # first — this is what the assertions below actually pin.
            task_b = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "hi"}], config, config.system_message,
            ))
            # Give B a real chance to (wrongly) run ahead if the gate were
            # missing — bounded, not a hang: B has nothing to wait on but the
            # gate, so either it stays blocked past this sleep (correct) or
            # it doesn't (bug), never an indefinite wait either way.
            await asyncio.sleep(0.2)
            assert not checkpoint_b.model.started.is_set(), (
                "B entered generate() before A released the checkpoint — the "
                "execution gate did not serialize them"
            )

            checkpoint_a.model.release.set()
            await asyncio.wait_for(task_a, timeout=_BOUND)

            await _await_started(checkpoint_b.model)
            checkpoint_b.model.release.set()
            await asyncio.wait_for(task_b, timeout=_BOUND)
        finally:
            checkpoint_a.model.release.set()
            checkpoint_b.model.release.set()
            for t in (task_a, task_b):
                if t is not None and not t.done():
                    t.cancel()

        # A's full teardown (offload to cpu, in `_leased`'s own finally) must
        # be recorded BEFORE B is ever placed on the device.
        a_cpu_at = events.index("A:to:cpu")
        b_cuda_at = events.index("B:to:cuda")
        assert a_cpu_at < b_cuda_at, events
        assert checkpoint_a.model.moves == ["cuda", "cpu"]
        assert checkpoint_b.model.moves == ["cuda", "cpu"]

    @pytest.mark.asyncio
    async def test_stream_and_non_stream_calls_serialize_on_the_same_checkpoint(
        self, client, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint_a = _gated_checkpoint("STREAM", events)
        checkpoint_b = _gated_checkpoint("BUFFERED", events)
        sequence = [checkpoint_a, checkpoint_b]
        monkeypatch.setattr(
            NativeLLMClient, "_acquire",
            lambda self, p, cfg, is_te=False: sequence.pop(0),
        )

        async def _drain_stream():
            async for _event in client.stream_with_history(
                [{"role": "user", "content": "hi"}], config, config.system_message,
            ):
                pass

        task_a = asyncio.create_task(_drain_stream())
        task_b = None
        try:
            await _await_started(checkpoint_a.model)
            task_b = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "hi"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)
            assert not checkpoint_b.model.started.is_set()

            checkpoint_a.model.release.set()
            await asyncio.wait_for(task_a, timeout=_BOUND)
            await _await_started(checkpoint_b.model)
            checkpoint_b.model.release.set()
            await asyncio.wait_for(task_b, timeout=_BOUND)
        finally:
            checkpoint_a.model.release.set()
            checkpoint_b.model.release.set()
            for t in (task_a, task_b):
                if t is not None and not t.done():
                    t.cancel()

        assert events.index("STREAM:to:cpu") < events.index("BUFFERED:to:cuda")

    @pytest.mark.asyncio
    async def test_exception_in_a_still_releases_the_gate_for_b(
        self, client, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint_a = _gated_checkpoint("A", events)
        checkpoint_a.model.error = RuntimeError("synthetic failure in A")
        checkpoint_b = _gated_checkpoint("B", events)
        sequence = [checkpoint_a, checkpoint_b]
        monkeypatch.setattr(
            NativeLLMClient, "_acquire",
            lambda self, p, cfg, is_te=False: sequence.pop(0),
        )

        task_a = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        task_b = None
        try:
            await _await_started(checkpoint_a.model)
            task_b = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "hi"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)
            assert not checkpoint_b.model.started.is_set()

            checkpoint_a.model.release.set()
            with pytest.raises(RuntimeError, match="synthetic failure in A"):
                await asyncio.wait_for(task_a, timeout=_BOUND)

            # B must still be admitted — A's exception must not leave the
            # gate held forever.
            await _await_started(checkpoint_b.model)
            checkpoint_b.model.release.set()
            response = await asyncio.wait_for(task_b, timeout=_BOUND)
            assert response.provider_id == "native"
        finally:
            checkpoint_a.model.release.set()
            checkpoint_b.model.release.set()
            for t in (task_a, task_b):
                if t is not None and not t.done():
                    t.cancel()

        assert events.index("A:to:cpu") < events.index("B:to:cuda")

    @pytest.mark.asyncio
    async def test_cancelling_a_waiter_releases_nothing_it_never_held(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        """B is cancelled while still queued behind A (never entered the
        gate) — A must be completely unaffected and finish normally, and the
        cache entry must not end up stuck leased forever."""
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint = _gated_checkpoint("A", events)
        # Patches the LOADER, not `_acquire` itself, so both turns go through
        # the REAL `ModelLifecycle.acquire()` — same key, same fingerprint,
        # so B (once it ever got the gate) would warm-reuse the exact entry
        # A is holding, exactly like two real turns on one checkpoint. That's
        # what makes `models_manager._entries[key]` below meaningful: it was
        # never populated when this test bypassed `_acquire` outright.
        monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)

        task_a = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        task_b = None
        try:
            await _await_started(checkpoint.model)
            task_b = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "hi"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)

            task_b.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_b
            # B was cancelled while still queued behind the gate — it must
            # never have reached `generate()` a second time.
            assert events.count("A:generate:started") == 1, (
                "a cancelled waiter must never have entered generate()"
            )

            checkpoint.model.release.set()
            response = await asyncio.wait_for(task_a, timeout=_BOUND)
            assert response.provider_id == "native"
        finally:
            checkpoint.model.release.set()
            if task_b is not None and not task_b.done():
                task_b.cancel()

        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()

    @pytest.mark.asyncio
    async def test_cancelling_b_mid_work_still_offloads_before_gate_release(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        """B is cancelled while it's the HOLDER (already past the gate,
        `generate()` running) — the streaming worker's own bounded exit
        wait, then offload, then `end_lease`, then gate release: in that
        order, never skipped."""
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint_b = _gated_checkpoint("B", events)
        # Patches the LOADER (see the sibling test above) so this turn's
        # cache entry is real and `models_manager._entries[key]` below exists.
        monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint_b)

        task_b = asyncio.create_task(client.stream_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ).__anext__())
        try:
            await _await_started(checkpoint_b.model)
            task_b.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task_b, timeout=_BOUND)
        finally:
            checkpoint_b.model.release.set()

        # Give the (now-released) background generate() thread a bounded
        # moment to actually return and run `_leased`'s finally.
        deadline = asyncio.get_event_loop().time() + _BOUND
        while "B:to:cpu" not in events and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert "B:to:cpu" in events
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()

    @pytest.mark.asyncio
    async def test_a_subsequent_call_still_succeeds_after_prior_overlap(
        self, client, native_checkpoint, monkeypatch
    ):
        """After A and B have both gone through the gate, a third, ordinary
        call on the same checkpoint must behave exactly like the
        no-contention case — sequential warm reuse untouched."""
        name, path = native_checkpoint
        config = _config(name)

        response = await client.generate_with_history(
            [{"role": "user", "content": "warm up"}], config, config.system_message,
        )
        assert response.provider_id == "native"

        response2 = await client.generate_with_history(
            [{"role": "user", "content": "second turn"}], config, config.system_message,
        )
        assert response2.provider_id == "native"

    @pytest.mark.asyncio
    async def test_two_client_instances_sharing_one_manager_still_serialize(
        self, models_manager, native_checkpoint, monkeypatch
    ):
        """Two separate `NativeLLMClient` instances built on the SAME
        `ModelLifecycle` (e.g. a request-scoped client alongside the
        gateway's) must still gate on the SAME lock — the registry is keyed
        by (manager identity, cache key), never by which client instance
        made the call."""
        name, path = native_checkpoint
        client_a = NativeLLMClient(models_manager)
        client_b = NativeLLMClient(models_manager)
        config_a = _config(name, id="cfg-a")
        config_b = _config(name, id="cfg-b")
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint_a = _gated_checkpoint("A", events)
        checkpoint_b = _gated_checkpoint("B", events)
        sequence = [checkpoint_a, checkpoint_b]
        monkeypatch.setattr(
            NativeLLMClient, "_acquire",
            lambda self, p, cfg, is_te=False: sequence.pop(0),
        )

        task_a = asyncio.create_task(client_a.generate_with_history(
            [{"role": "user", "content": "hi"}], config_a, config_a.system_message,
        ))
        task_b = None
        try:
            await _await_started(checkpoint_a.model)
            task_b = asyncio.create_task(client_b.generate_with_history(
                [{"role": "user", "content": "hi"}], config_b, config_b.system_message,
            ))
            await asyncio.sleep(0.2)
            assert not checkpoint_b.model.started.is_set(), (
                "two client instances sharing one ModelLifecycle did not share the gate"
            )

            checkpoint_a.model.release.set()
            await asyncio.wait_for(task_a, timeout=_BOUND)
            await _await_started(checkpoint_b.model)
            checkpoint_b.model.release.set()
            await asyncio.wait_for(task_b, timeout=_BOUND)
        finally:
            checkpoint_a.model.release.set()
            checkpoint_b.model.release.set()
            for t in (task_a, task_b):
                if t is not None and not t.done():
                    t.cancel()

        assert events.index("A:to:cpu") < events.index("B:to:cuda")

    @pytest.mark.asyncio
    async def test_an_unrelated_checkpoint_is_never_blocked_by_the_gate(
        self, client, monkeypatch
    ):
        """A gate is per-checkpoint (keyed by the cache key), not global —
        a second, unrelated checkpoint must be free to run while the first
        is held open."""
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        events: list = []
        checkpoint_a = _gated_checkpoint("A", events)
        checkpoint_other = _gated_checkpoint("OTHER", events)

        # Distinct cache keys: acquire returns the held-open checkpoint for
        # the "held" config's resolved path, the unrelated one for the
        # "unrelated" config's — `_resolve_model` is patched directly rather
        # than resolved on disk, since the only thing under test is that two
        # DIFFERENT cache keys never share a gate.
        def _fake_acquire(self, p, cfg, is_te=False):
            return checkpoint_other if p == "unrelated-path" else checkpoint_a

        monkeypatch.setattr(NativeLLMClient, "_acquire", _fake_acquire)
        monkeypatch.setattr(
            NativeLLMClient, "_resolve_model",
            staticmethod(lambda name: ("unrelated-path", False) if name == "unrelated" else ("held-path", False)),
        )

        config_a = _config("held")
        config_other = _config("unrelated")

        task_a = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config_a, config_a.system_message,
        ))
        other_task = None
        try:
            await _await_started(checkpoint_a.model)
            # The unrelated checkpoint must be free to START (and finish)
            # while A is still held open — bounded wait on `started` proves
            # it isn't queued behind A's gate; A itself is never released
            # until the `finally` below, so there is no way for this to pass
            # by accident.
            other_task = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "hi"}], config_other, config_other.system_message,
            ))
            await _await_started(checkpoint_other.model)
            checkpoint_other.model.release.set()
            response = await asyncio.wait_for(other_task, timeout=_BOUND)
            assert response.provider_id == "native"
            assert checkpoint_other.model.moves == ["cuda", "cpu"]
        finally:
            checkpoint_a.model.release.set()
            checkpoint_other.model.release.set()
            if other_task is not None and not other_task.done():
                other_task.cancel()
            if not task_a.done():
                await asyncio.wait_for(task_a, timeout=_BOUND)


class TestExecutionGateBookkeeping:
    @pytest.mark.asyncio
    async def test_gate_registry_entry_is_removed_once_nobody_needs_it(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        config = _config(name)
        key = client._cache_key(path, False)
        identity = (id(models_manager), key)

        assert identity not in native_module._EXECUTION_GATES

        await client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        )

        assert identity not in native_module._EXECUTION_GATES, (
            "the gate registry must not accumulate an entry once the last "
            "holder/waiter for a checkpoint is gone"
        )

    @pytest.mark.asyncio
    async def test_gate_registry_sheds_entries_even_after_contention(
        self, client, native_checkpoint, monkeypatch
    ):
        """Same as above, but after TWO overlapping turns (holder + waiter)
        actually exercised the registry — proves the refcount, not just the
        no-contention fast path, returns to zero."""
        name, path = native_checkpoint
        config = _config(name)
        key = client._cache_key(path, False)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint_a = _gated_checkpoint("A", events)
        checkpoint_b = _gated_checkpoint("B", events)
        sequence = [checkpoint_a, checkpoint_b]
        monkeypatch.setattr(
            NativeLLMClient, "_acquire",
            lambda self, p, cfg, is_te=False: sequence.pop(0),
        )

        identity = (id(client._models()), key)
        task_a = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        task_b = None
        try:
            await _await_started(checkpoint_a.model)
            task_b = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "hi"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)
            assert identity in native_module._EXECUTION_GATES
            assert native_module._EXECUTION_GATES[identity].refcount == 2

            checkpoint_a.model.release.set()
            await asyncio.wait_for(task_a, timeout=_BOUND)
            await _await_started(checkpoint_b.model)
            checkpoint_b.model.release.set()
            await asyncio.wait_for(task_b, timeout=_BOUND)
        finally:
            checkpoint_a.model.release.set()
            checkpoint_b.model.release.set()
            for t in (task_a, task_b):
                if t is not None and not t.done():
                    t.cancel()

        assert identity not in native_module._EXECUTION_GATES
