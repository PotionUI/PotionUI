"""Streaming assembly for SeedVR2 *video* upscale.

:mod:`batching` owns the temporal geometry (4n+1 snapping, sliding windows,
reversed padding, the overlap crossfade) but expresses it over COMPLETE frame
lists: ``plan_batches`` needs the total frame count up front and
``stitch_batches`` needs every decoded batch at once. That forces the whole
clip -- source, resized, padded and output -- to be resident simultaneously, so
peak memory grows with clip duration.

This module re-expresses the same geometry as a pull-based pipeline over an
iterator of resized source frames, so a clip of any length runs in memory
bounded by the temporal batch:

  * :func:`prepend_reversed_stream` -- the reversed-head prepend, buffering only
    the first ``count + 1`` frames instead of the whole clip.
  * :func:`iter_windows` -- ``plan_batches``' windows without knowing the total,
    holding at most one window plus one step of look-ahead.
  * :class:`OverlapStitcher` -- ``stitch_batches``' crossfade as a hold-back of
    the last ``overlap`` output frames until the next batch can be faded into
    them.
  * :func:`stream_output_frames` -- the whole chain, yielding finished output
    frames (padding trimmed, overlaps blended, prepend dropped, color-corrected)
    one at a time.

Peak retained frames is ``prepend + 2*batch_size + overlap`` on the input side
(the window buffer plus the source frames still owed to color correction) and
``2*batch_size + overlap`` on the output side (the clip being decoded, the one
being emitted, and the held-back overlap tail) -- a function of the temporal
batch, never of the clip's duration.

Like :mod:`batching` this stays numpy-only (frames are ``(H,W,3)`` uint8 arrays)
so the assembly is unit-testable against the eager reference without torch, a
GPU or a video file.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Deque, Iterable, Iterator, List, Optional, Tuple

import numpy as np

from src.pipelines.pipes.generator.seedvr2.batching import (
    blend_overlap,
    pad_batch,
)
from src.platform.runtime.native.errors import SamplingCancelled

Frame = np.ndarray  # (H, W, 3) uint8


def effective_overlap(batch_size: int, temporal_overlap: int) -> int:
    """The overlap ``plan_batches`` actually applies for this batch size.

    An ``overlap >= batch_size`` is reset to 0 (a window would never advance);
    the window planner and the stitcher must agree on that, so both read it
    from here rather than each re-deriving it.
    """
    batch_size = max(1, int(batch_size))
    overlap = max(0, int(temporal_overlap))
    return 0 if overlap >= batch_size else overlap


def prepend_reversed_stream(frames: Iterable[Frame], count: int) -> Iterator[Frame]:
    """Stream form of ``pad_reversed(frames, count, prepend=True)``.

    Yields the ``count`` reversed head frames followed by the source stream
    itself. Only the first ``count + 1`` frames are buffered: that is all the
    non-overflow branch mirrors. The overflow branch (``count >= total``) does
    need every frame, but there the whole clip is shorter than ``count``, which
    is a config value -- the buffer stays bounded either way.
    """
    count = int(count)
    it = iter(frames)
    if count <= 0:
        yield from it
        return

    head: List[Frame] = []
    for frame in it:
        head.append(frame)
        if len(head) > count:
            break

    if not head:
        return

    if len(head) > count:
        yield from reversed(head[1:count + 1])
        yield from head
        yield from it
        return

    # Stream exhausted with `count >= len(head)`: repeat the boundary frame for
    # the remainder, exactly as `pad_reversed` does.
    total = len(head)
    for _ in range(count - total + 1):
        yield head[0].copy()
    if total > 1:
        yield from reversed(head[1:])
    yield from head


def iter_windows(
    frames: Iterable[Frame], batch_size: int, temporal_overlap: int
) -> Iterator[Tuple[int, List[Frame]]]:
    """Stream form of ``plan_batches``: yield ``(start, window_frames)``.

    Reproduces the planner's stopping rules without a total frame count: a
    window that would only re-cover the overlap region is dropped, and the walk
    ends as soon as a window reaches the end of the stream.
    """
    batch_size = max(1, int(batch_size))
    overlap = effective_overlap(batch_size, temporal_overlap)
    step = batch_size - overlap if overlap > 0 else batch_size

    it = iter(frames)
    buf: List[Frame] = []
    base = 0
    idx = 0
    exhausted = False

    while True:
        while not exhausted and base + len(buf) < idx + batch_size:
            try:
                buf.append(next(it))
            except StopIteration:
                exhausted = True

        lo = idx - base
        window = buf[lo:lo + batch_size]
        if not window:
            return
        end = idx + len(window)
        if idx > 0 and end - idx <= overlap:
            return
        yield idx, window
        if exhausted and end >= base + len(buf):
            return

        idx += step
        if idx > base:
            del buf[:idx - base]
            base = idx


class OverlapStitcher:
    """Stream form of ``stitch_batches``' crossfade.

    The last ``overlap`` frames of a batch are not final until the next batch
    arrives to be faded into them, so :meth:`push` holds them back and returns
    only the frames the join can no longer change; :meth:`flush` releases the
    tail once no further batch is coming.
    """

    def __init__(self, overlap: int):
        self._overlap = max(0, int(overlap))
        self._tail: List[Frame] = []
        self._emitted = 0

    def push(self, batch: List[Frame]) -> List[Frame]:
        if self._overlap <= 0:
            return list(batch)

        total = self._emitted + len(self._tail)
        if total == 0:
            self._tail.extend(batch)
        else:
            k = min(self._overlap, total, len(batch))
            if k > 0:
                cut = len(self._tail) - k
                self._tail[cut:] = blend_overlap(self._tail[cut:], batch[:k], k)
                self._tail.extend(batch[k:])
            else:
                self._tail.extend(batch)
        return self._release()

    def flush(self) -> List[Frame]:
        out = list(self._tail)
        self._tail.clear()
        self._emitted += len(out)
        return out

    def _release(self) -> List[Frame]:
        if len(self._tail) <= self._overlap:
            return []
        cut = len(self._tail) - self._overlap
        out = self._tail[:cut]
        del self._tail[:cut]
        self._emitted += cut
        return out


def stream_output_frames(
    source_frames: Iterable[Frame],
    *,
    batch_size: int,
    temporal_overlap: int,
    prepend_frames: int,
    uniform: bool,
    upscale_clips: Callable[[Iterator[np.ndarray]], Iterable[np.ndarray]],
    prepare_clip: Optional[Callable[[List[Frame]], List[Frame]]] = None,
    correct: Optional[Callable[[List[Frame], List[Frame]], List[Frame]]] = None,
    on_batch: Optional[Callable[[int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Iterator[Frame]:
    """Yield finished output frames for a streamed clip, in source order.

    ``upscale_clips`` receives an iterator of padded uint8 ``(T,H,W,3)`` clips
    and returns an iterable of the matching decoded clips; it must consume and
    produce them one at a time (a generator), which is what keeps a single
    batch's activations live and lets the true-length bookkeeping below stay a
    two-element queue instead of a per-clip list.

    ``correct`` pairs each output frame with the resized SOURCE frame at the
    same output index, clamped to the last source frame -- the eager path's
    ``sources[j] = resized[min(j, n-1)]``. Source frames are held in a ring only
    while they are still owed to it, so ``correct=None`` (color correction off)
    never buffers them at all.
    """
    overlap = effective_overlap(batch_size, temporal_overlap)

    ring: Deque[Frame] = deque()
    last_source: List[Optional[Frame]] = [None]

    def _tap(frames: Iterable[Frame]) -> Iterator[Frame]:
        for frame in frames:
            ring.append(frame)
            yield frame

    def _pop_source() -> Frame:
        if ring:
            last_source[0] = ring.popleft()
        return last_source[0]

    tapped = _tap(source_frames) if correct is not None else source_frames
    seq = prepend_reversed_stream(tapped, prepend_frames)

    true_lens: Deque[int] = deque()

    def _clips() -> Iterator[np.ndarray]:
        for _start, window in iter_windows(seq, batch_size, overlap):
            if is_cancelled is not None and is_cancelled():
                # A plain `return` here would end this generator normally, which
                # `upscale_clips` and the `for decoded in upscaled` loop below
                # cannot tell apart from a genuinely finished clip -- the
                # overlap tail would then get flushed and the caller would
                # publish a cancelled run as a complete one. Cancellation must
                # unwind as the same exception every other sampling loop raises
                # (see `src.platform.runtime.native.errors.SamplingCancelled`),
                # so it can only ever be told apart from success.
                raise SamplingCancelled()
            padded, true_len = pad_batch(window, batch_size, uniform=uniform)
            if prepare_clip is not None:
                padded = prepare_clip(padded)
            true_lens.append(true_len)
            yield np.stack(padded, axis=0)

    stitcher = OverlapStitcher(overlap)
    remaining_prepend = max(0, int(prepend_frames))
    done = 0

    def _finish(frames: List[Frame]) -> List[Frame]:
        nonlocal remaining_prepend
        if remaining_prepend and frames:
            drop = min(remaining_prepend, len(frames))
            remaining_prepend -= drop
            frames = frames[drop:]
        if not frames or correct is None:
            return frames
        return correct(frames, [_pop_source() for _ in frames])

    upscaled = upscale_clips(_clips())
    try:
        for decoded in upscaled:
            true_len = true_lens.popleft()
            batch = [decoded[i] for i in range(min(true_len, int(decoded.shape[0])))]
            done += 1
            if on_batch is not None:
                on_batch(done)
            yield from _finish(stitcher.push(batch))
    finally:
        # An abandoned consumer must still release the DiT/VAE the upscaler holds
        # resident, so close it here rather than waiting on garbage collection.
        close = getattr(upscaled, "close", None)
        if close is not None:
            close()

    yield from _finish(stitcher.flush())
