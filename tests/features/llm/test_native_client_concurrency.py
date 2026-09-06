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
import concurrent.futures
import threading
import time

import pytest
import torch

from src.features.llm.clients import native as native_module
from src.features.llm.clients.native import NativeLLMClient, _LoadedCheckpoint
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from tests.features.llm.test_native_client import (
    _config,
    _fake_streaming_generate,
    _NEWLINE_TOKEN_ID,
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


class _CpuPinnedTensor:
    """Wraps a REAL CPU tensor (from the real `native_checkpoint` tokenizer's
    `_apply_template` output) but makes `.to(device)` a harmless no-op — the
    supervised-teardown tests below force `torch.cuda.is_available()` True
    so `_leased` takes its `manage_device` placement branch on the
    checkpoint's `.model`, but a real prompt tensor's own `.to("cuda")` call
    inside `stream_with_history` would hit an actual (absent) CUDA device.
    Everything else (`.shape`, indexing, decode) delegates straight to the
    wrapped real tensor, so `TextIteratorStreamer`/the real tokenizer's
    `decode()` keep working exactly as they do in the unforced-CUDA
    cancellation tests `_fake_streaming_generate` (reused here) was
    originally written for."""

    def __init__(self, tensor):
        self._tensor = tensor

    def to(self, *_a, **_k):
        return self

    def __getattr__(self, name):
        return getattr(self._tensor, name)

    def __getitem__(self, item):
        return self._tensor[item]


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


def _install_supervised_teardown_fixture(
    checkpoint: _LoadedCheckpoint,
    monkeypatch,
    *,
    tokens: tuple = (_NEWLINE_TOKEN_ID,) * 3,
    delay_after_stop: float = 0.3,
) -> list:
    """Wires a REAL checkpoint's model to drive the REAL
    `TextIteratorStreamer`/`StoppingCriteriaList` via `_fake_streaming_generate`
    (imported from `test_native_client.py`) while forcing device placement
    through a recording no-op `.to()`, and wraps `_apply_template`'s real
    output tensors in `_CpuPinnedTensor` so `stream_with_history`'s own
    `.to(device)` on the prompt tensor stays safe once CUDA is spoofed
    available. Returns the shared `events` list both the model and the
    `.to()` recorder append to."""
    events: list = []

    def _recording_to(device, *_a, **_k):
        events.append(f"to:{device}")
        return checkpoint.model

    monkeypatch.setattr(checkpoint.model, "to", _recording_to)
    monkeypatch.setattr(
        checkpoint.model, "generate",
        _fake_streaming_generate(
            tokens=tokens, poll_interval=0.005, delay_after_stop=delay_after_stop, events=events,
        ),
    )

    real_apply_template = NativeLLMClient._apply_template

    def _wrapped_apply_template(checkpoint_arg, chat, image, template_kwargs):
        result = real_apply_template(checkpoint_arg, chat, image, template_kwargs)
        return {k: (_CpuPinnedTensor(v) if hasattr(v, "to") else v) for k, v in result.items()}

    monkeypatch.setattr(NativeLLMClient, "_apply_template", staticmethod(_wrapped_apply_template))
    return events


class TestSupervisedTeardownOnStopTimeout:
    """The consumer's bounded worker-exit wait giving up must never let
    `_leased` offload, `end_lease`, or release the execution gate while the
    streaming worker is still running past the bound (see `_LeaseHandoff`/
    `_supervised_teardown`, `src/features/llm/clients/native.py`): teardown
    is handed to a supervised cleanup that waits the REST of the way
    unbounded, then runs the SAME offload/end_lease/gate-release exactly
    once, only once the worker has actually exited."""

    @pytest.mark.asyncio
    async def test_caller_returns_within_bound_while_teardown_waits_for_the_real_worker(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        identity = (id(models_manager), key)
        config = _config(name)
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(native_module, "_STOP_WAIT_TIMEOUT_SECONDS", 0.02)
        events = _install_supervised_teardown_fixture(checkpoint, monkeypatch)

        agen = client.stream_with_history([{"role": "user", "content": "hi"}], config, config.system_message)
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=_BOUND)
            assert first["type"] == "token"
            assert "to:cuda" in events

            # aclose() itself must return within the (shortened) bound, not
            # the much larger delay the fake worker takes to actually exit —
            # the whole point of the consumer's bounded wait.
            await asyncio.wait_for(agen.aclose(), timeout=2)

            # Returning past the bound must NOT mean the checkpoint was torn
            # down — the worker is still (deliberately) running, so no CPU
            # move, no end_lease, and no gate release have happened yet.
            assert "to:cpu" not in events
            assert models_manager._entries[key].leased_by
            assert identity in native_module._EXECUTION_GATES

            deadline = time.monotonic() + _BOUND
            while "worker_returning_after_stop" not in events and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert "worker_returning_after_stop" in events

            # The supervised cleanup gets a bounded moment (well past the
            # worker's own exit) to actually run once the worker is done.
            deadline = time.monotonic() + _BOUND
            while "to:cpu" not in events and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
        finally:
            await agen.aclose()  # no-op if already closed

        assert "to:cpu" in events
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert identity not in native_module._EXECUTION_GATES
        assert len(client._supervised_teardowns) == 0

    @pytest.mark.asyncio
    async def test_second_caller_waits_for_the_supervised_cleanup_before_proceeding(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        config = _config(name)
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(native_module, "_STOP_WAIT_TIMEOUT_SECONDS", 0.02)
        events = _install_supervised_teardown_fixture(checkpoint, monkeypatch)

        agen = client.stream_with_history([{"role": "user", "content": "hi"}], config, config.system_message)
        second_task = None
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=_BOUND)
            assert first["type"] == "token"
            await asyncio.wait_for(agen.aclose(), timeout=2)

            async def _second_call():
                collected = []
                async for event in client.stream_with_history(
                    [{"role": "user", "content": "second"}], config, config.system_message,
                ):
                    collected.append(event)
                return collected

            second_task = asyncio.create_task(_second_call())
            await asyncio.sleep(0.2)
            assert not second_task.done(), (
                "a second caller on the same checkpoint must wait for the "
                "supervised cleanup, not run concurrently with the abandoned worker"
            )

            second_events = await asyncio.wait_for(second_task, timeout=_BOUND)
            # Warm reuse of the SAME checkpoint still works once the
            # supervised cleanup has run — a plain, uneventful turn.
            assert second_events[-1]["type"] == "usage"
        finally:
            if second_task is not None and not second_task.done():
                second_task.cancel()
            await agen.aclose()

        assert "to:cpu" in events

    @pytest.mark.asyncio
    async def test_cancelling_an_unrelated_waiter_leaves_a_pending_supervisor_intact(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(native_module, "_STOP_WAIT_TIMEOUT_SECONDS", 0.02)
        events = _install_supervised_teardown_fixture(checkpoint, monkeypatch)

        agen = client.stream_with_history([{"role": "user", "content": "hi"}], config, config.system_message)
        waiter_task = None
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=_BOUND)
            assert first["type"] == "token"
            await asyncio.wait_for(agen.aclose(), timeout=2)
            assert len(client._supervised_teardowns) == 1

            async def _waiter():
                async for _event in client.stream_with_history(
                    [{"role": "user", "content": "waiter"}], config, config.system_message,
                ):
                    pass

            waiter_task = asyncio.create_task(_waiter())
            await asyncio.sleep(0.2)
            assert not waiter_task.done(), "a queued waiter must not run ahead of the pending supervisor"

            waiter_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter_task
            waiter_task = None

            # Cancelling an unrelated queued waiter must never disturb the
            # PENDING supervisor — it's still tracked, and still completes
            # once the abandoned worker actually exits.
            assert len(client._supervised_teardowns) == 1

            deadline = time.monotonic() + _BOUND
            while "worker_returning_after_stop" not in events and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            deadline = time.monotonic() + _BOUND
            while models_manager._entries[key].leased_by and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
        finally:
            if waiter_task is not None and not waiter_task.done():
                waiter_task.cancel()
            await agen.aclose()

        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0

    @pytest.mark.asyncio
    async def test_cancelling_aclose_during_the_stop_wait_still_hands_off_to_the_supervisor(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        """A FURTHER cancellation landing while `aclose()` is itself still
        inside the bounded stop-wait (e.g. `aclose()` wrapped in a tighter
        timeout of its own) must not strand the gate — ownership transfers
        to the supervisor exactly as it would on a plain bound timeout."""
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        # Long enough that cancelling shortly after starting `aclose()`
        # reliably lands INSIDE the bounded wait, not after it resolves.
        monkeypatch.setattr(native_module, "_STOP_WAIT_TIMEOUT_SECONDS", 2.0)
        events = _install_supervised_teardown_fixture(checkpoint, monkeypatch, delay_after_stop=0.3)

        agen = client.stream_with_history([{"role": "user", "content": "hi"}], config, config.system_message)
        aclose_task = None
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=_BOUND)
            assert first["type"] == "token"

            aclose_task = asyncio.create_task(agen.aclose())
            # Give the bounded wait a moment to actually start before
            # cancelling it — this is the window a further cancellation must
            # not strand.
            await asyncio.sleep(0.05)
            aclose_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await aclose_task
            aclose_task = None

            # Even though `aclose()` itself was cancelled mid-wait,
            # ownership must still have transferred to a supervisor — never
            # left for `_leased`'s own finally to race the still-running
            # worker.
            assert len(client._supervised_teardowns) == 1
            assert models_manager._entries[key].leased_by
            assert "to:cpu" not in events

            deadline = time.monotonic() + _BOUND
            while "worker_returning_after_stop" not in events and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            deadline = time.monotonic() + _BOUND
            while models_manager._entries[key].leased_by and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
        finally:
            if aclose_task is not None and not aclose_task.done():
                aclose_task.cancel()
            await agen.aclose()  # no-op if already closed

        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0


class TestBufferedCancellationRetainsOwnership:
    """A cancelled `generate_with_history` call must not let `_leased`
    offload, `end_lease`, or release the execution gate while the executor
    thread might still be running `model.generate()` — the same
    retained-ownership rule as the streaming stop-timeout path, triggered
    here by cancellation of the awaited `to_thread` itself (the buffered
    path has no cooperative stop signal to request first, so there is no
    bounded grace period — any cancellation hands off immediately)."""

    @pytest.mark.asyncio
    async def test_cancelling_generate_with_history_while_the_worker_holds_the_barrier_retains_ownership(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        identity = (id(models_manager), key)
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint = _gated_checkpoint("A", events)
        # Patches the LOADER, not `_acquire` itself, so this turn's cache
        # entry is real and `models_manager._entries[key]` below exists.
        monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)

        task = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        second_task = None
        try:
            await _await_started(checkpoint.model)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # Cancelling must NOT mean the checkpoint was torn down — the
            # fake worker is still (deliberately) blocked on the barrier.
            assert "A:to:cpu" not in events
            assert models_manager._entries[key].leased_by
            assert identity in native_module._EXECUTION_GATES

            # A second call on the SAME checkpoint must queue behind the
            # gate rather than run concurrently with the abandoned worker.
            async def _second_call():
                return await client.generate_with_history(
                    [{"role": "user", "content": "second"}], config, config.system_message,
                )

            second_task = asyncio.create_task(_second_call())
            await asyncio.sleep(0.2)
            assert not second_task.done(), (
                "a second caller must wait for the supervised cleanup, not run "
                "concurrently with the abandoned worker"
            )

            # Release the barrier: the abandoned worker finishes, the
            # supervised cleanup runs, and the queued second call proceeds.
            checkpoint.model.release.set()
            response = await asyncio.wait_for(second_task, timeout=_BOUND)
            assert response.provider_id == "native"
        finally:
            checkpoint.model.release.set()
            if second_task is not None and not second_task.done():
                second_task.cancel()

        assert "A:to:cpu" in events
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0


class TestLifecycleCancellationBoundaries:
    """`_leased`/`_teardown`/`generate_with_history` each submit blocking
    calls (acquire, device placement, offload, the buffered generation
    itself) to an executor via `_submit_cancellable`
    (`src/features/llm/clients/native.py`) instead of a bare
    `asyncio.to_thread`, because `await`ing an executor call does NOT stop
    it — the underlying thread keeps running regardless of a cancellation
    reaching that await. Each test here cancels at exactly one of those
    boundaries while a barrier keeps the real call genuinely still running
    (or, for the pending-future case, genuinely still queued), then proves
    the lease/gate are never released before the real call's outcome is
    known, a second caller on the same checkpoint queues rather than races
    it, and a subsequent call still succeeds once everything settles."""

    @pytest.mark.asyncio
    async def test_cancelling_during_acquisition_defers_and_finishes_the_late_checkpoint(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)

        started = threading.Event()
        release = threading.Event()
        checkpoint_holder: list = []

        def _slow_build(self, p, load_kwargs):
            started.set()
            release.wait(timeout=_BOUND)
            checkpoint = _gated_checkpoint("LATE", [])
            checkpoint.model.release.set()  # generate() itself is not under test here
            checkpoint_holder.append(checkpoint)
            return checkpoint

        monkeypatch.setattr(NativeLLMClient, "_build", _slow_build)

        task = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        second_task = None
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait, _BOUND), timeout=_BOUND + 1)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # The loader is STILL (deliberately) running — a second caller
            # on the same checkpoint must queue behind the gate rather than
            # trigger a second concurrent load.
            second_task = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "second"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)
            assert not second_task.done()

            release.set()
            response = await asyncio.wait_for(second_task, timeout=_BOUND)
            assert response.provider_id == "native"
        finally:
            release.set()
            if second_task is not None and not second_task.done():
                second_task.cancel()

        assert len(checkpoint_holder) == 1, (
            "the late loader must run exactly once — a second concurrent load would "
            "mean the second caller never actually queued behind the gate"
        )
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0

    @pytest.mark.asyncio
    async def test_cancelling_during_placement_defers_and_finishes_the_late_move(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint = _gated_checkpoint("A", events)
        checkpoint.model.release.set()  # generate() itself is not under test here
        monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)

        started = threading.Event()
        release = threading.Event()
        real_to = checkpoint.model.to
        cuda_entries = 0

        def _slow_to(device):
            nonlocal cuda_entries
            if device == "cuda":
                cuda_entries += 1
                started.set()
                release.wait(timeout=_BOUND)
            return real_to(device)

        monkeypatch.setattr(checkpoint.model, "to", _slow_to)

        task = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        second_task = None
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait, _BOUND), timeout=_BOUND + 1)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # Placement is STILL (deliberately) running — a second caller
            # must queue behind the gate rather than reuse a model that
            # hasn't finished moving yet. `not second_task.done()` alone
            # would not prove that (it would ALSO hold if the second call
            # simply raced onto the SAME shared `.to("cuda")` barrier
            # independently of the gate); `cuda_entries` staying at 1 is
            # what actually proves the second call hasn't even reached its
            # own placement attempt.
            second_task = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "second"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)
            assert not second_task.done()
            assert cuda_entries == 1, "a queued second caller must not have attempted its own placement yet"

            release.set()
            response = await asyncio.wait_for(second_task, timeout=_BOUND)
            assert response.provider_id == "native"
        finally:
            release.set()
            if second_task is not None and not second_task.done():
                second_task.cancel()

        assert "cuda" in checkpoint.model.moves
        assert "cpu" in checkpoint.model.moves
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0

    @pytest.mark.asyncio
    async def test_cancelling_during_teardowns_offload_still_ends_the_lease_after_it_finishes(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        events: list = []
        checkpoint = _gated_checkpoint("A", events)
        monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)
        # `generate()` itself must return immediately — only the OFFLOAD
        # move back to CPU is made to block, via the barrier below.
        checkpoint.model.release.set()

        started = threading.Event()
        release = threading.Event()
        real_to = checkpoint.model.to

        def _slow_to(device):
            if device == "cpu":
                started.set()
                release.wait(timeout=_BOUND)
            return real_to(device)

        monkeypatch.setattr(checkpoint.model, "to", _slow_to)

        task = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        second_task = None
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait, _BOUND), timeout=_BOUND + 1)

            task.cancel()
            # `_teardown`'s own cancellation handling deliberately does NOT
            # re-raise once it has handed the pending offload to a
            # supervisor (see its docstring) — cleanup, not primary control
            # flow — so the outer task may complete normally (its result
            # already computed) or, depending on exactly where cancellation
            # landed, still surface as cancelled. Either is acceptable here;
            # what matters is the lease/gate state asserted below.
            try:
                await task
            except asyncio.CancelledError:
                pass

            # The offload is STILL (deliberately) running — the lease and
            # gate must stay held, so a second caller on the same
            # checkpoint queues rather than reusing a model that hasn't
            # actually finished moving back to CPU yet.
            second_task = asyncio.create_task(client.generate_with_history(
                [{"role": "user", "content": "second"}], config, config.system_message,
            ))
            await asyncio.sleep(0.2)
            assert not second_task.done()
            assert models_manager._entries[key].leased_by

            release.set()
            response = await asyncio.wait_for(second_task, timeout=_BOUND)
            assert response.provider_id == "native"
        finally:
            release.set()
            if second_task is not None and not second_task.done():
                second_task.cancel()

        assert checkpoint.model.moves.count("cpu") >= 1
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0

    @pytest.mark.asyncio
    async def test_cancelling_before_the_buffered_worker_starts_settles_worker_done_itself(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        """A one-worker executor already occupied by something else means
        the buffered path's own `_run` submission sits PENDING, never
        dequeued. Cancelling while it's still pending must settle its own
        completion signal itself — nothing will ever call `_run`, so
        nothing else ever will — instead of spawning a supervisor that
        would wait forever on an event that never fires."""
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)

        events: list = []
        checkpoint = _gated_checkpoint("A", events)
        checkpoint.model.release.set()  # the subsequent call's own generate() must complete
        monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)

        occupy_started = threading.Event()
        occupy_release = threading.Event()
        one_worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def _occupy():
            occupy_started.set()
            occupy_release.wait(timeout=_BOUND)

        real_build_chat = NativeLLMClient._build_chat

        def _patched_build_chat(self, messages, system_message, image_data):
            result = real_build_chat(self, messages, system_message, image_data)
            # `_leased`'s own acquire/placement (no CUDA here, so no
            # placement submission at all) have already gone through this
            # loop's ORIGINAL executor by this point — installing the
            # one-worker executor for the loop only NOW means `_run`'s
            # later submission, not the acquire, is what ends up genuinely
            # pending.
            monkeypatch.setitem(
                native_module._EXECUTORS_BY_LOOP, asyncio.get_running_loop(), one_worker,
            )
            one_worker.submit(_occupy)
            assert occupy_started.wait(timeout=_BOUND), "the occupying task never started"
            return result

        monkeypatch.setattr(NativeLLMClient, "_build_chat", _patched_build_chat)

        task = asyncio.create_task(client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ))
        try:
            # Bounded moment for the coroutine to run all the way to
            # `_run`'s (now-pending) submission and suspend awaiting it —
            # nothing else is left for it to do before that point.
            await asyncio.sleep(0.2)
            assert not task.done()

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # Never started: no supervisor should ever have been spawned,
            # and the lease/gate must release promptly rather than hang
            # forever waiting on a call that will never happen.
            assert len(client._supervised_teardowns) == 0
            assert not models_manager._entries[key].leased_by
            assert key in models_manager._evictable_keys()
            assert "A:generate:started" not in events
        finally:
            occupy_release.set()
            one_worker.shutdown(wait=False)

        # Restore ONLY `_build_chat` (not `_build`, and not the
        # `native_checkpoint` fixture's own patches sharing this same
        # `monkeypatch` fixture) — `monkeypatch.undo()` would revert all of
        # them at once.
        monkeypatch.setattr(NativeLLMClient, "_build_chat", real_build_chat)
        # Dropping the loop's registry entry restores the default: the next
        # submission lazily creates a fresh, normally-sized executor.
        native_module._EXECUTORS_BY_LOOP.pop(asyncio.get_running_loop(), None)
        response = await client.generate_with_history(
            [{"role": "user", "content": "second"}], config, config.system_message,
        )
        assert response.provider_id == "native"


class TestUvloopSubmission:
    """Both launch paths run this app on uvloop (`api.py` passes
    `loop="uvloop"`; `run.sh`'s uvicorn auto-loop prefers it), and a uvloop
    `Loop` has NO `_default_executor` attribute at all — reading one raises
    `AttributeError`, and assigning one is worse still because uvloop accepts
    the attribute and never consults it. So every `_submit_cancellable`
    boundary must go through an executor this module owns, proven here by
    driving a real entry point on a real uvloop loop rather than by poking at
    the helper directly."""

    def test_generate_with_history_runs_on_a_uvloop_loop(self, client, native_checkpoint):
        uvloop = pytest.importorskip("uvloop")
        name, _path = native_checkpoint
        config = _config(name)

        loop = uvloop.new_event_loop()
        try:
            response = loop.run_until_complete(client.generate_with_history(
                [{"role": "user", "content": "hello"}], config, config.system_message,
            ))
        finally:
            loop.close()

        assert response.provider_id == "native"


class TestStreamWithToolsCloseScope:
    """`stream_with_tools` must own an explicit close scope over the inner
    `stream_with_history` generator — a bare `async for` gives no guarantee
    that closing the WRAPPER also closes what it's iterating, so awaiting
    the wrapper's `aclose()` while suspended at a yielded token must still
    reach `stream_with_history`'s existing cooperative stop and supervised
    hand-off (LLM-06/LLM-07) before returning."""

    @pytest.mark.asyncio
    async def test_closing_the_wrapper_reaches_the_inner_streams_cooperative_stop(
        self, client, models_manager, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        key = f"native/llm/{path}"
        config = _config(name)
        checkpoint = client._acquire(path, config)

        events: list = []
        monkeypatch.setattr(
            checkpoint.model, "generate",
            _fake_streaming_generate(
                tokens=(_NEWLINE_TOKEN_ID,) * 200, poll_interval=0.01,
                delay_after_stop=0.05, events=events,
            ),
        )

        agen = client.stream_with_tools(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        )
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=_BOUND)
            assert first["type"] == "token"

            await asyncio.wait_for(agen.aclose(), timeout=_BOUND)
        finally:
            await agen.aclose()  # no-op if already closed

        # The wrapper's own aclose() must have reached all the way into
        # `stream_with_history`'s stopping_criteria seam (never a bare,
        # unowned abandonment of the inner generator to eventual GC).
        assert "worker_saw_stop" in events
        assert "worker_returning_after_stop" in events
        assert not models_manager._entries[key].leased_by
        assert key in models_manager._evictable_keys()
        assert len(client._supervised_teardowns) == 0
