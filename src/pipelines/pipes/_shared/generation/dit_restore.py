"""Best-effort DiT-to-VRAM restore after a generation's decode phase.

Some generators (LTX's ``txt2vid_ltx``/``video_ltx``) must offload their DiT
to CPU before VAE decode — the decode buffer and a ~23GB DiT can't co-reside
on a 32GB card (see those pipes' ``generate_one``). That leaves the DiT
parked in host RAM once the generation finishes, so the *next* LTX generation
on the same preset pays a multi-second PCIe re-upload before it can even start
sampling.

:func:`restore_dit_best_effort` moves the DiT back to VRAM right after decode
(+ VAE offload) so the model cache stays warm for the next request, mirroring
Krea-2's stays-resident generator (which never offloads its DiT at all). It
is strictly best-effort in both directions: it never evicts another resident
component to make room, and it never lets an exception (OOM or otherwise)
escape into the caller — a hiccup here must not fail an already-successful
generation.

Every skip/restore/error path emits a ``dit_restore.*`` profiler mark (see
``src.platform.observability.profiling``) instead of only a debug log line, so
a production run's profile shows *why* a restore did or didn't happen.

The fits-check uses
:func:`~src.platform.runtime.native.memory.residency.effective_free_vram_gb`,
not the plain ``mem_get_info`` free figure — right after decode, the caching
allocator can still be holding a large reserved-but-idle pool from the decode's
own buffers that ``mem_get_info`` counts as used even though it's immediately
reclaimable, so the plain query under-reports free VRAM at this call site.
"""

from __future__ import annotations

import logging
from typing import Any

from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.memory.residency import (
    effective_free_vram_gb,
    minimum_inference_memory_gb,
)

logger = logging.getLogger(__name__)


def restore_dit_best_effort(dit: Any, device: str) -> None:
    """Best-effort: move an offloaded ``dit`` back to ``device`` if it fits.

    ``dit`` should already be dereferenced by the caller (e.g. ``bundle.dit``
    read once into a local) since bundle components are held via weak refs
    and may have been collected by the time this runs.

    Skips (never raises) when:
      - ``dit`` is ``None`` or has no module (collected / never loaded).
      - ``dit`` is already GPU-resident (nothing to do).
      - ``dit`` is CURRENTLY placed with partial residency (streaming) —
        checked as ``dit._streamer is not None and dit._streamer.active``,
        the same test ``NativeGenerator._maybe_compile`` uses (engine.py) to
        distinguish "streaming right now" from "has a streamer object".
        ``NativeModel._streamer`` is constructed once, the first time
        ``stream_to()`` is used, and the object itself is never reset back to
        ``None`` — only its ``.active`` flag flips off on
        ``teardown()``/``offload()``. An earlier cut of this function tested
        ``_streamer is not None`` alone, which is a permanent false positive
        once a DiT was EVER streamed even once (e.g. a one-off co-tenant-OOM
        degrade in ``NativeGenerator._move_dit_to_gpu``): every later,
        fully-resident generation would then silently skip restoring forever.
        A currently-active partial residency IS still skipped: eagerly
        re-streaming here on a best-effort background action is not an
        obvious win (it re-pays the per-forward pinned-CPU->GPU copy cost the
        next generation's sampler would pay anyway), and choosing a resident
        budget belongs to the *next* generation's own placement logic, not
        this cleanup step.
      - The DiT's estimated size plus the standard minimum-inference-memory
        reserve does not already fit in the currently-EFFECTIVE-free VRAM
        (see :func:`effective_free_vram_gb`). This deliberately never evicts
        another resident component (that would just move the "who pays the
        reload" cost onto whatever got evicted) — it only claims VRAM that is
        free (or idle-and-reclaimable) right now.
    """
    profiler = get_profiler()
    if dit is None or getattr(dit, "module", None) is None:
        profiler.mark("dit_restore.skip", reason="no_dit")
        return
    try:
        if str(getattr(dit, "device", "")).startswith("cuda"):
            profiler.mark("dit_restore.skip", reason="already_resident")
            return

        streamer = getattr(dit, "_streamer", None)
        if streamer is not None and getattr(streamer, "active", False):
            profiler.mark("dit_restore.skip", reason="partial_residency_active")
            return

        free_gb = effective_free_vram_gb(device)
        if free_gb is None:
            profiler.mark("dit_restore.skip", reason="vram_query_unavailable")
            return

        size_gb = float(getattr(dit, "estimated_vram_gb", None) or 0.0)
        if size_gb <= 0.0:
            profiler.mark("dit_restore.skip", reason="no_size_estimate", free_gb=free_gb)
            return

        reserve_gb = minimum_inference_memory_gb()
        need_gb = size_gb + reserve_gb
        if free_gb < need_gb:
            profiler.mark("dit_restore.skip", reason="insufficient_free_vram",
                          free_gb=free_gb, need_gb=need_gb)
            return

        dit.move_to(device)
        profiler.mark("dit_restore.restored", size_gb=size_gb, free_gb_before=free_gb)
        logger.debug(
            "dit_restore: restored DiT (~%.2fGB) to %s for a warm next generation", size_gb, device,
        )
    except Exception as exc:  # pragma: no cover - best-effort, never fail the generation
        profiler.mark("dit_restore.error", error=repr(exc))
        logger.debug("dit_restore: best-effort restore failed", exc_info=True)
