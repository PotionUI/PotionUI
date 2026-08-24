import asyncio
import threading

import pytest

from src.features.generation.output_bridge import OutputBridge
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    ImageGenerationOutput,
    ProgressGenerationOutput,
)


def _progress(step: int) -> ProgressGenerationOutput:
    return ProgressGenerationOutput(state=f"step-{step}")


class TestOutputBridgeOrdering:
    @pytest.mark.asyncio
    async def test_ordered_delivery_same_loop(self):
        received = []

        async def on_output(output):
            received.append(output)

        bridge = OutputBridge(on_output=on_output)
        run_task = asyncio.create_task(bridge.run())

        outputs = [_progress(i) for i in range(5)]
        for o in outputs:
            bridge.emit(o)
        bridge.emit(None)

        await run_task

        assert received[:-1] == outputs
        assert received[-1] is None

    @pytest.mark.asyncio
    async def test_threaded_producer_preserves_order(self):
        """emit() called from a background thread must not reorder or drop items."""
        received = []

        async def on_output(output):
            received.append(output)

        bridge = OutputBridge(on_output=on_output)
        run_task = asyncio.create_task(bridge.run())

        n = 200
        outputs = [_progress(i) for i in range(n)]

        def produce():
            for o in outputs:
                bridge.emit(o)
            bridge.emit(None)

        thread = threading.Thread(target=produce)
        thread.start()
        thread.join()

        await run_task

        assert received[:-1] == outputs
        assert received[-1] is None

    @pytest.mark.asyncio
    async def test_emit_never_blocks_calling_thread(self):
        """emit() must return immediately even if the consumer hasn't started yet."""
        async def on_output(_output):
            await asyncio.sleep(0.05)

        bridge = OutputBridge(on_output=on_output)

        done = threading.Event()

        def produce():
            for i in range(50):
                bridge.emit(_progress(i))
            bridge.emit(None)
            done.set()

        thread = threading.Thread(target=produce)
        thread.start()
        thread.join(timeout=1.0)

        assert done.is_set(), "emit() blocked the producer thread"

        run_task = asyncio.create_task(bridge.run())
        await run_task


class TestOutputBridgeBackpressure:
    @pytest.mark.asyncio
    async def test_progress_coalesces_under_full_queue(self):
        """
        When the queue is full, additional progress outputs replace the
        newest queued progress item instead of growing the queue.
        """
        release = asyncio.Event()

        async def on_output(_output):
            # Block the consumer so the queue fills up while we emit.
            await release.wait()

        bridge = OutputBridge(on_output=on_output, maxsize=3)
        run_task = asyncio.create_task(bridge.run())

        # Let the consumer pick up the first item and block on `release`.
        first = _progress(0)
        bridge.emit(first)
        await asyncio.sleep(0.01)

        # Fill the queue past maxsize with progress outputs only.
        for i in range(1, 10):
            bridge.emit(_progress(i))

        # Let the scheduled call_soon_threadsafe callbacks actually run.
        await asyncio.sleep(0.01)

        # Queue should never exceed maxsize for progress-only backlog.
        assert len(bridge._deque) <= bridge._maxsize

        release.set()
        bridge.emit(None)
        await run_task

    @pytest.mark.asyncio
    async def test_media_and_sentinel_always_enqueue(self):
        """Media/artifact outputs and the completion sentinel are never dropped or coalesced."""
        release = asyncio.Event()
        received = []

        async def on_output(output):
            received.append(output)
            if len(received) == 1:
                await release.wait()

        bridge = OutputBridge(on_output=on_output, maxsize=2)
        run_task = asyncio.create_task(bridge.run())

        # First item is consumed and blocks the consumer on `release`.
        bridge.emit(_progress(-1))
        await asyncio.sleep(0.01)

        # Fill past maxsize with progress (coalescable) then push real media.
        for i in range(5):
            bridge.emit(_progress(i))

        gallery = GalleryGenerationOutput(images=[])
        bridge.emit(gallery)

        # Let the scheduled call_soon_threadsafe callbacks actually run.
        await asyncio.sleep(0.01)

        # The queue should have grown past maxsize to fit the media output
        # rather than dropping it.
        assert gallery in bridge._deque

        release.set()
        bridge.emit(None)
        await run_task

        assert gallery in received
        assert received[-1] is None


class TestOutputBridgeCompletion:
    @pytest.mark.asyncio
    async def test_run_exits_after_sentinel(self):
        async def on_output(_output):
            pass

        bridge = OutputBridge(on_output=on_output)
        bridge.emit(None)

        # Should complete without hanging.
        await asyncio.wait_for(bridge.run(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_emit_after_close_is_a_noop(self):
        seen = []

        async def on_output(output):
            seen.append(output)

        bridge = OutputBridge(on_output=on_output)
        run_task = asyncio.create_task(bridge.run())

        bridge.emit(None)
        await run_task

        # Further emits after the sentinel must not raise and must not be
        # delivered (the consumer has already stopped).
        bridge.emit(_progress(0))
        await asyncio.sleep(0.01)

        assert seen == [None]
