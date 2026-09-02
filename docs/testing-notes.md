# Testing notes — environment noise and the browser harness

Detail split out of `CLAUDE.md` so it isn't re-read on every request. Read this
when a test fails in a way you didn't cause, or before running the browser E2E
suite.

## Opt-in markers: GPU, real model files, gc-sensitivity

`tests/conftest.py` declares a `pytest_collection_modifyitems` hook that gates
three categories of test behind explicit env vars, so a plain
`pytest tests/` never touches a GPU or a real model file:

- `@pytest.mark.requires_gpu` — needs a real CUDA device. Skipped unless
  `POTIONUI_GPU_TESTS=1` is set **and** `torch.cuda.is_available()` is true.
  The env var is required even on a CUDA box, because the GPU here is shared
  with the maintainer's own generations (see "Shared GPU etiquette").
- `@pytest.mark.requires_models` — reads a real model checkpoint/header off
  disk. Skipped unless `POTIONUI_MODEL_TESTS=1` is set. **No test may read a
  real model file without this marker** — `models/checkpoints`, `models/vae`,
  `models/diffusion_models` and `models/text_encoders` may be symlinks into a live
  production model depot, and a test that reads them by default
  violates the maintainer's hard rule even when it happens to skip cleanly on
  a host where the file is absent. Most `requires_models` tests keep their
  original `skipif(not <path>.exists(), ...)` alongside the marker as a
  second line of defense for hosts that opt in without the weights present.
- `@pytest.mark.gc_sensitive` — a full-suite-only flake whose count depends on
  the live heap at the time it runs (see the profiler entries below). Not
  skipped locally; CI deselects it with `-m "not gc_sensitive"` so a heap
  perturbation from an unrelated test never fails the build.

Run the opted-in categories explicitly when validating a change to native
detection/loading code against real weights (maintainer/GPU-box only):

```bash
POTIONUI_MODEL_TESTS=1 python -m pytest tests/platform/runtime/native/ -q --no-cov
POTIONUI_GPU_TESTS=1 python -m pytest tests/ -m requires_gpu -q --no-cov
```

## CI

`.github/workflows/backend-tests.yml` runs the real pytest suite (CPU-only,
`-m "not requires_gpu and not requires_models and not gc_sensitive"`) and
`frontend`'s `npm run test:unit`, on every push to `master` and every PR.
`requirements-ci.txt` is a trimmed copy of `requirements.txt` for the
CPU-only runner (see its own header for what's excluded and why);
`constraints.txt` is not used there since it pins the maintainer's
CUDA-specific transitive closure. This is separate from
`.github/workflows/onboarding-smoke.yml`, which only dry-run resolves
dependencies and runs the architecture/setup-feature suites, not the full
test tree.

## Backend: known environment noise

These are container/environment artefacts, not regressions caused by your change.


- The cv2-dependent tests (`tests/pipelines/pipes/test_detailer_sdxl.py`,
  `video_frame_extractor/`, `video_frame_merger/`, `media_loader/`) guard their
  `cv2` import with
  `pytest.importorskip("cv2", reason=..., exc_type=ImportError)`, so a container
  missing `cv2`'s native deps gets clean skips instead of collection-aborting
  errors — no `--ignore` needed either way, and the tests run for real once the
  lib is present. **They run here now** (102 tests): `libglib2.0-0` supplies the
  `libgthread-2.0.so.0` that `import cv2` needs. It arrived as a side effect of
  `npx playwright install-deps chromium` on 2026-07-27; before that these four
  suites silently skipped. If you meet `ImportError: libgthread-2.0.so.0` on a
  fresh container, `sudo apt-get install -y libglib2.0-0` is the fix (run
  `sudo apt-get update` first — the container's package index goes stale after
  a reset and the install fails without it; hit again 2026-07-28), and until
  you run it a change to the detailer/video/media_loader pipes is unverified
  rather than passing.
  **`exc_type=ImportError` is load-bearing:** pytest 9 only skips on
  `ModuleNotFoundError` by default, and cv2 *is* found — it fails later, while
  loading its shared object. Without the argument the `ImportError` escapes,
  collection aborts, and the whole run reports **zero** results rather than a
  green suite. Do not drop it.
- `tests/platform/runtime/native/test_attention.py::test_cross_backend_agreement_if_installed`
  skips if an accelerated attention backend is *listed* as available but its
  compiled kernel fails to import (e.g. this container's `sageattention` wheel
  needs a `libstdc++` newer than Debian bookworm's default repo carries —
  missing symbol `CXXABI_1.3.15`; fixing that means adding backports or a manual
  `.so` swap, not a plain package install, so it's left as a flagged, maintainer-
  decided item rather than fixed unilaterally). **This is not just test noise:**
  on this sm120 card the attention auto-selector picks sage2 for a REAL
  generation and there is no runtime fallback when the kernel fails to load —
  the generation dies mid-sampling (observed 2026-07-28). Until the libstdc++
  issue is resolved, run headless/scripted generations with
  `NATIVE_ATTENTION=sdpa`. **Scope: THIS DEV CONTAINER ONLY** — the
  maintainer's own server runtime has a working sage2 install (confirmed
  2026-08-11); never tell the maintainer to set `NATIVE_ATTENTION=sdpa` for
  their real generations.
- `tests/platform/observability/profiling/test_profiler.py::test_census_group_dedups_views_of_one_storage`
  is a known **full-suite-only flake**: it snapshots every live `torch.Tensor`
  in the process via `gc.get_objects()`, so its exact counts can be perturbed by
  whatever other tests leave live in the heap when the full suite runs in one
  process. Passes reliably alone or in any scoped subset. A `gc.collect()`
  immediately before the walk was tried and made it *worse* under full-suite
  load (7 failures instead of 1) — reverted; do not reintroduce without
  validating against a full run, not just a scoped one.
- `tests/platform/observability/profiling/test_profiler.py::test_census_group_separates_pinned_from_unpinned`
  is the same **full-suite-only flake** mechanism as its sibling above (same
  `gc.get_objects()` census, same heap-perturbation cause): observed failure is
  `pinned_flags == {False}` — the pinned-CPU-tensor row didn't show up in that
  scan. Passes reliably alone or in any scoped subset. Do not attempt a
  `gc.collect()` fix here either — see the sibling entry.
- `tests/platform/observability/profiling/test_profiler.py::test_write_tensor_census_dispatches_on_device_kind`
  is the same mechanism again (first observed 2026-08-28 under `pytest -n 4`,
  where which tests share a worker's heap varies per run): the final
  `any(device == "cpu")` assert needs the census walk to surface this test's
  own tensor, and a crowded heap can push it out. Marked `gc_sensitive` like
  its siblings.
- **xdist-only CI failures (quarantined 2026-08-28):**
  `tests/platform/runtime/native/test_engine.py` and
  `tests/platform/runtime/native/arch/test_minimax_music3_ar_loop.py` pass
  sequentially on the GitHub runner but fail under `pytest -n 4` there (with
  or without `OMP_NUM_THREADS=1`): two engine tests stall inside `F.conv2d`
  until the 300s timeout, and the music3 bf16 tolerance (1e-2) breaks at
  0.031. Not reproducible under a `taskset` 4-core simulation of the CI env,
  and no `set_num_threads`/`torch.backends`/default-dtype poisoner exists in
  the tree. The workflow runs these two files as a sequential step in the
  platform shard; fold them back into the parallel step if root-caused.
- **runner-CPU bf16 roulette (deterministic skip added):** part of GitHub's
  hosted runner fleet lacks native bf16 (no AMX/AVX512-BF16); torch's bf16
  conv2d fallback there is 10-20x slower, so a real sample+decode at size
  (`test_engine.py::test_end_to_end_sample_and_decode`) or a real bf16
  forward (`test_minimax_music3_ar_loop.py::TestBf16Compute::
  test_generate_runs_end_to_end_in_bf16`,
  `::test_incremental_step_matches_full_prefill_in_bf16`) can blow through
  even the 300s per-test timeout — identical trees go green/red purely on
  which runner they land on. Two things make a test bf16-heavy: the explicit
  `@pytest.mark.bf16_cpu_heavy` marker, or drawing a real-VAE fixture
  (`vae_path`) — every encode/decode through the engine on CPU is that same
  bf16 conv2d stack, and an unmarked `encode_image` test stalled a runner
  after the marker alone had landed (2026-09-02), so the fixture rule exists
  to make the skip hold by construction rather than by memory.
  `tests/platform/runtime/native/conftest.py`'s `cpu_bf16_is_usable()` times
  a representative bf16 `F.conv2d` workload once per session (cached) and a
  `pytest_collection_modifyitems` hook skips bf16-heavy tests, with
  reason `"runner CPU lacks native bf16; bf16 e2e would time out
  (deterministic skip, see testing-notes)"`, when the probe reports fallback
  AND no CUDA device is present (a GPU host runs these on the fast path
  regardless of the CPU probe). **A skip here means the runner lost the CPU
  roulette, not that coverage vanished from the suite** — a capable runner
  still executes these tests for real. Measured on the dev container this was
  calibrated on (11th-gen Intel, no `avx512_bf16`/`amx_bf16` cpuid flag): the
  probe workload (`(1,128,192,192)` input, `(128,128,3,3)` weight,
  `padding=1`) runs a stable ~41ms/call (<5% spread over 10 reps), and
  `test_end_to_end_sample_and_decode` itself completes in <1s — this
  container's fallback is not the catastrophic one. The threshold
  (`_THRESHOLD_MS = 150.0`) sits ~3.6x above that measurement, with a
  10-20x-slower runner expected at ~410-820ms/call for the same workload —
  wide margin on both sides. `POTIONUI_FORCE_BF16_PROBE=pass|fail` overrides
  the probe result for testing the skip mechanism itself without a slow-bf16
  CPU on hand (combine with `CUDA_VISIBLE_DEVICES=` to also hide a real GPU
  locally, since the hook only skips when none is visible). This is a
  separate mechanism from the xdist-only quarantine entry above — it
  addresses the *timeout* half of that entry, not the music3 tolerance flake,
  which is unrelated and still open.
- `tests/pipelines/pipes/generator/img2vid_wan22/*` can OOM when the GPU is busy
  (shared box — check `nvidia-smi` before assuming a regression).
- The media-editing suite (`tests/features/media/editing/`) skips every video and
  audio test with "ffmpeg is not installed" when the binary is absent — 23 silent
  skips, leaving only the Pillow image path exercised. `sudo apt-get install -y
  ffmpeg` turns them into real round-trips. The backend assumes ffmpeg in
  production (`media_probe` shells out to ffprobe), so a container without it
  reports a green suite for code that has never run.
- The suite should otherwise be green. Judge a change by the **set of failing
  test IDs before vs. after**, not by a raw count: capture the failing IDs on a
  clean tree first, then compare. Report the failure-ID diff honestly.


## Browser E2E (Playwright, local-only — not a CI gate)


`frontend/tests/e2e/*.spec.ts` drives a real Chromium browser against a
throwaway backend + built frontend, run through `tests/e2e/ui/run.py` (see
`tests/e2e/ui/README.md`). It catches the class of bug an HTTP-only check
can't see (stuck spinners, `$effect` request loops, reactivity regressions).
**Nothing runs this suite automatically** — it is not wired into any npm
script or GitHub Actions workflow (`.github/workflows/` has no Playwright
step). Run it yourself before calling a frontend change done:

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py
```

**`run.py` chunks automatically.** Passing ~10+ specs to one Playwright
invocation reliably killed the `vite preview` process partway through:
every remaining test then failed with `net::ERR_CONNECTION_REFUSED` at
`/login`. `run.py` now splits the requested specs into fixed-size chunks
(default 3 — empirically safe; override with `--chunk-size`) and boots a
fresh throwaway backend + preview + Playwright process per chunk, so a
caller can't hit that cliff by passing a large spec list. If the preview
process still dies mid-chunk, `run.py` detects it, aborts that chunk instead
of waiting out the connection-refused cascade, prints an unmistakable
diagnostic including the preview process's own exit status, and exits with a
distinct status (3) so that's never confused with an ordinary test failure
(1). Specs still share **one** throwaway backend **within a chunk**, so
state can leak between specs in the same chunk: a spec asserting a global
empty state passes alone and can fail after a same-chunk sibling installs a
preset — order specs accordingly, or run the affected spec alone.


**`chat-tool-approval.spec.ts` fails before it reaches the dock (pre-existing,
confirmed 2026-08-28).** In this container the scripted fake-LLM turn emits the
`create_phrasebook_category` tool call but the execution comes back **failed**
instead of `pending_approval`, the loop continues into an unscripted turn
("(fake-llm: no scripted turn left)"), and no ApprovalDock ever renders — the
spec then times out. Reproduced identically on a clean fd6239a worktree, i.e.
NOT caused by the 2026-08-28 approval-dock rework. Separately, the spec's
selectors assert the pre-rework dock anatomy (`div.border-warning\/35`,
"from reply") and need updating to the compact-row dock once the underlying
failure is fixed.
