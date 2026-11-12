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
  with live generations (see "Shared GPU etiquette").
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

## Model roots in tests

`ModelScanner`, `ModelLocator` and everything downstream of them take a
`ModelRootResolver`, never a `models_dir` path or setting — there is no
`ModelScanner(models_dir=...)` constructor to fall back to, and monkeypatching the
`models_dir` setting does nothing for code that already resolves through a
resolver. Use `tests/fixtures/model_roots.py`'s `make_roots(tmp_path, types=..., ...)`
for a resolver backed by a real, disposable directory tree (it creates the type
subdirectories on disk and returns a `ModelRootResolver` with its snapshot
pre-populated, no DB and no `ModelRootRepository` involved) — see
`tests/features/downloads/test_queue.py`, `test_download_service.py` and
`test_native_library.py` for the pattern. It also ships a `FakeProbe` for tests
that need to force a root `offline`/`unreadable` without a real filesystem
probe. A test that builds its own resolver by hand instead (a bespoke
`FakeResolver`/`_resolver()` helper, as in `test_indexer_roots.py`,
`test_locator.py` and `test_native_availability_projector.py`) is fine too —
`make_roots` just removes the boilerplate when a plain single-root, every-type
setup is enough.

A test that needs the real DB-backed tables instead (`model_roots`,
`model_root_bindings`, `model_locations`) — a migration test, or anything
exercising `ModelRootsManager`/`root_detection.py` — runs the real migrations on a
scratch `Database` and inserts rows directly; see
`tests/platform/database/test_migration_035_model_roots.py` and
`tests/features/models/test_roots_manager.py` for the pattern. `HOME_ROOT_ID`
(`"home"`) from `src.platform.filesystem.model_roots` is the id every fresh
migration seeds, so a location row against it needs no root of its own.

## CI

`.github/workflows/backend-tests.yml` runs the real pytest suite (CPU-only,
`-m "not requires_gpu and not requires_models and not gc_sensitive"`) and
`frontend`'s `npm run test:unit`, on every push to `master` and every PR.
`requirements-ci.txt` is a trimmed copy of `requirements.txt` for the
CPU-only runner (see its own header for what's excluded and why);
`constraints.txt` is not used there since it pins a
CUDA-specific transitive closure. The Linux venv (`~/venv-ci`) is cached per
interpreter version, requirement hashes and ISO week (requirements are floors,
so the weekly component refreshes versions); a miss falls back to a fresh install.
This is separate from
`.github/workflows/onboarding-smoke.yml`, which only dry-run resolves
dependencies and runs the architecture/setup-feature suites, not the full
test tree.

Marketplace plugins' own `tests/` directories
(`content/plugins/marketplace/*/tests`, outside `pytest.ini`'s `testpaths`)
run inside the `rest` shard, as a separate step via `tests/plugins/run_suites.py`.
At most `POTIONUI_PLUGIN_SUITE_WORKERS` suites run at once (default: the CPU
count) and the suites with the most test files start first. `run_suites.py` runs each plugin's suite in its own
pytest subprocess rather than one shared invocation — plugins that use the
`api: module: "backend/api.py"` convention claim a top-level `backend` module
name at conftest collection time, and pooling two such plugins' tests into
one pytest call leaves the second plugin's `backend` module shadowing the
first's for the whole session. Locally:

```
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/plugins/run_suites.py -q --no-cov
```

## Windows CI

`.github/workflows/windows.yml` runs on `windows-latest`. Treat a red run here as real signal, not flake.

What it proves:

- `doctor` — `potionui.cmd doctor` (human-readable and `--json`) sees a
  healthy toolchain on a fresh Windows checkout.
- `install-smoke-remote` — a real end-to-end boot of the `remote` install
  profile (CPU-only PyTorch) via `tests/install/run.py`: install, backend
  start, `/health`, owner registration, a presets listing, `status`, `stop`,
  and that the process tree is actually gone. This is the actual proof of a
  native Windows boot, not just static checks.
- `backend-tests` — the **full** pytest tree in the same three shards as
  `backend-tests.yml` (`platform` / `features` / `rest`), `-n 4`, under the same
  `not requires_gpu and not requires_models and not gc_sensitive` marker
  filter. A failing shard still uploads its junit/log as an artifact — the
  job is intentionally not `continue-on-error`: the failure is the signal,
  not a green badge. Linux uploads `junit-linux-<shard>[-serial].xml` per shard
  the same way (`backend-tests-linux-<shard>` artifacts, kept 14 days).
- the `rest` shard also runs `tests/plugins/run_suites.py`, one pytest
  subprocess per marketplace plugin, as on Linux.
- `release-gate` — `tests/release/release_gate.py --skip-gpu` (recipe lint,
  layering, setup/recipes suites, preset lint budget).
- `frontend` — `npm run check`, `test:unit`, `test:component`, `build`, then
  `build:plugins` with a `git diff --exit-code` over the committed
  `frontend/dist/` trees: a plugin bundle that builds differently on Windows
  is drift, not noise.
- `e2e` — the HTTP journeys (`tests/e2e/journeys/run.py`) and the Playwright
  UI journeys (`tests/e2e/ui/run.py`, Chromium) against a throwaway backend.
  Linux CI does not run these; Windows is the only place they run unattended.
  Both harnesses spawn with `CREATE_NEW_PROCESS_GROUP` and stop with
  `taskkill /T` on nt (`popen_group_kwargs` / `kill_group` in
  `tests/e2e/harness/e2e_harness.py`), and resolve `npm`/`npx` through
  `shutil.which` because the bare names are `.cmd` shims on Windows.

Every native command in this workflow runs under `shell: cmd`, never `pwsh`:
pwsh's `$ErrorActionPreference = 'stop'` turns a native process's first
stderr line into a step failure, and console-signal quirks kill a pwsh step
silently. Long outputs go to a log file and are `type`d back so a killed
step still uploads what it had.

What it does **not** prove:

- GPU generation. `windows-latest` has no NVIDIA GPU, so the `doctor` job's
  GPU row is an expected non-blocking warning, and no preset ever actually
  renders here. Real GPU validation on Windows needs a report from
  real hardware.
- Anything a real driver changes: triton/`torch.compile` absence, the
  Optimizations panel probe, safetensors mmap file locks while a model is
  loaded. Those need a Windows box with an NVIDIA card.

POSIX-only assertions in the subset are gated, not skipped wholesale:
`tests/platform/security/test_config.py`, `test_secrets.py` and
`test_claim_token.py` assert the `0o600` mode bits of generated secret files
only when `os.name != "nt"` (Windows `chmod` only toggles the read-only
attribute, so there is no equivalent bit pattern), and
`tests/scripts/test_potionui_cli_backup.py::test_backup_exits_one_when_the_storage_tree_is_unusable`
is skipped on Windows because `Path.chmod(0o500)` does not make a directory
unwritable there. A new Windows-only failure in this subset is therefore a
real portability bug until proven otherwise.

`Database` keeps its SQLite connections pooled, and a pooled connection holds
the database file and its `-wal`/`-shm` open. Windows refuses to delete or
move an open file, so a test that removes or replaces its own database (a
`tearDown` that unlinks it, a `TemporaryDirectory` cleanup, a fixture that
deletes its directory) must call `Database.close_pool()` first. The autouse
fixture in `tests/conftest.py` closes the pool after every test, which is
too late for cleanup that runs inside the test itself; on Linux the same
mistake shows up as `Directory not empty` from an `rmdir`.

### Illegal-instruction crashes in bf16 tests (`0xc000001d`)

Symptom: xdist workers die with `Windows fatal exception: code 0xc000001d`
(STATUS_ILLEGAL_INSTRUCTION), with the top frame in `torch.nn.Linear.forward`
(reached through the cast-on-forward layers in `src/platform/runtime/native/kernels/casting.py`) in bf16 or fp8 native-engine
tests, across unrelated files at once. A hosted runner VM can advertise
AVX-512 BF16 or AMX to PyTorch's CPU dispatch without being able to execute
it, so the same commit can pass on a runner with a different CPU. When the
crashing tests share nothing but a bf16 matmul, suspect the runner, not the
change.

`windows.yml` therefore pins `ATEN_CPU_CAPABILITY=avx2` (PyTorch's own
kernel dispatch; `torch.backends.cpu.get_cpu_capability()` reports `AVX2`
with it set) and `ONEDNN_MAX_CPU_ISA=AVX2` (oneDNN's matmul dispatch) for the
whole workflow. The numerics under test are unaffected; only the instruction
set is capped. Do not remove them to "speed up" CI.

## Backend: known environment noise

These are container/environment artefacts, not regressions caused by your change.


- `opencv-python`, `opencv-contrib-python` and `opencv-python-headless` (pulled in by `controlnet-aux`) all install `cv2`, so `constraints.txt` pins the three to one version; keep them pinned together.
- The cv2-dependent tests (`tests/pipelines/pipes/test_detailer_sdxl.py`,
  `media_loader/`) guard their
  `cv2` import with
  `pytest.importorskip("cv2", reason=..., exc_type=ImportError)`, so a container
  missing `cv2`'s native deps gets clean skips instead of collection-aborting
  errors — no `--ignore` needed either way, and the tests run for real once the
  lib is present. **They run here now**: `libglib2.0-0` supplies the
  `libgthread-2.0.so.0` that `import cv2` needs. It arrived as a side effect of
  `npx playwright install-deps chromium` on 2026-07-27; before that these
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
- `tests/platform/runtime/native/kernels/test_dispatch.py::test_cross_backend_agreement_if_installed`
  skips if an accelerated attention backend is *listed* as available but its
  compiled kernel fails to import (e.g. this container's `sageattention` wheel
  needs a `libstdc++` newer than Debian bookworm's default repo carries —
  missing symbol `CXXABI_1.3.15`; fixing that means adding backports or a manual
  `.so` swap, not a plain package install, so it's left as a flagged, maintainer-
  decided item rather than fixed unilaterally). **This is not just test noise:**
  on this sm120 card the attention auto-selector picks sage2 for a REAL
  generation and there is no runtime fallback when the kernel fails to load —
  the generation dies mid-sampling (observed 2026-07-28). Until the libstdc++
  issue is resolved, pin the native backend's attention backend to `sdpa`
  (Admin → Backends → *backend* → Optimizations, setting
  `native_attention_backend`) before headless/scripted
  generations; the old `NATIVE_ATTENTION` env var is no longer read.
  **Scope: THIS DEV CONTAINER ONLY** — a production runtime with a working
  sage2 install must not be pinned to `sdpa`.
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
  features shard (`serial_paths`; the platform shard ignores them); fold them
  back into the parallel step if root-caused.
- **Shard layout (2026-10-21):** `tests/pipelines` runs in the features shard
  and the platform shard keeps `tests/platform` + `tests/features/plugins`,
  so the backend jobs are closer in length (Linux before: platform 309 s,
  features 93 s, rest 241 s). The frontend job (388 s Linux, about 735 s
  Windows) is the critical path, so this evens load rather than shortening the
  workflow. `-n 2` inside `tests/plugins/run_suites.py` was evaluated and not
  adopted: the plugin step is 34 s on Linux and 47 s on Windows inside the
  non-critical `rest` job, and it is startup-bound (Linux already runs the
  suites 10 at a time on 4 vCPUs), so worker-level parallelism saves
  seconds at best.
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
- The media tool suites (`tests/platform/media_tools/`, `tests/features/media_tools/`,
  `tests/pipelines/pipes/media_tool/`, `tests/pipelines/pipes/video_trim/`,
  `tests/pipelines/pipes/video_concat/`) skip every video and audio test with
  "ffmpeg is not available" when no ffmpeg binary can be found (`find_ffmpeg`
  also accepts the one bundled by `imageio-ffmpeg`), leaving only the Pillow
  image path exercised. `sudo apt-get install -y ffmpeg` turns them into real
  round-trips, so a container without ffmpeg reports a green suite for code
  that has never run.
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
(default 8 — empirically safe; override with `--chunk-size`) and boots a
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

**One UI run at a time.** The harness builds and serves from its own gitignored `frontend/.e2e-build`, never `frontend/build` (the live app's folder). Every run shares the harness build folder and the preview
port, so two runs started together corrupt each other (a blank login page,
"Username already exists"). `run.py` therefore takes an exclusive lock,
`tests/e2e/ui/.run.lock` (gitignored, holds the pid and UTC start time),
before it builds or starts any server. A second run prints who holds it and
waits, polling every few seconds, for up to `--lock-timeout` minutes
(default 30), then exits with status 4. A lock whose pid is gone, or whose pid
now belongs to a different process, is stale and is taken over with a message.
The lock is released when the run ends, on Ctrl-C, and on SIGTERM (SIGBREAK
on Windows). A run killed outright (`kill -9`, Task Manager) leaves the file
behind, and the next run takes it over as stale. `--list` never takes the lock.


**`chat-tool-approval.spec.ts` drives the inline action card with `delete_memory`.**
The chat opens on `/models`, so the session runs in the Models assistant mode,
and the scripted tool call has to be one that mode enables *and* that requires
approval. `create_phrasebook_category` is not enabled there (it fails with
"Tool '…' is unavailable; do not call it again" and the fake LLM then runs out of
scripted turns), and `write_memory` saves without approval. Each test seeds its
own global memory note through `POST /api/chat/memory`, scripts
`delete_memory` for that note id, and checks the side effect through
`GET /api/chat/memory`: the note is gone after Approve and still there after
Reject or after replying while the card is pending. Cards are located by
`[data-testid="action-card"]` and their `data-state` attribute (`pending`,
`applied`, `rejected`, `failed`, `stale`). If you switch the spec to a
different tool, first check that the opening route's mode lists it, or the
card never renders.

**`chat-chip-input-interactions.spec.ts` has two failures unrelated to the chat
composer (confirmed 2026-09-04 during the chat-view rework, `ChatChipInput`
untouched).** "the @ suggest dropdown supports Arrow/Enter keyboard navigation
over root namespaces" opens the chat from `/models`, which resolves the
Models-assistant mode, and that mode's root `@` suggestion lists a single
namespace (`models`) in this container — the spec's "at least two namespaces"
assumption is about the mode's resource providers, not the composer. "Delete
immediately before an attached resource chip (forward-delete gap, unhandled)"
documents a known gap and fails by design until forward-delete is handled.


## Chat and tool evaluation pack

`tests/evaluation/chat/` + `tests/e2e/harness/chat_eval.py` — reproducible scenario
fixtures and a replayable report format for comparing chat workflows across
explicitly selected model configurations. Fully offline by default
(`python tests/e2e/harness/chat_eval.py`, no args); see `docs/chat-evaluation.md` for
the layout, the two entry points, and the report schema.

## Install matrix (`./potionui start`; CI runs `remote` for real, `local`/`hybrid`/`worker` stay local-only)

`tests/install/run.py` proves `./potionui start` actually works for each
install profile (`local`, `hybrid`, `remote`) plus the `worker` preset, on a
throwaway checkout — never the maintainer's working tree, venv, or ports
26731/26730. `tests/install/test_run.py` covers its pure logic (directory
exclusion, port picking, the phase-stop-on-failure state machine, report
shape) and runs as part of the normal suite:

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python -m pytest tests/install -q --no-cov
```

CI also runs `--profiles remote` for real on every push/PR
(`install-smoke-remote` in `.github/workflows/onboarding-smoke.yml`).
Locally, the same run is maintainer-triggered only, and defaults to
`--profiles remote` (CPU-only torch, no CUDA download) so it's safe to run
without a GPU:

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/install/run.py --profiles remote --json report.json
```

Per profile it materializes a checkout (`git archive` from `--from-git`,
default `HEAD`; or a filtered copy of a directory via `--from-dir`), runs
`doctor --json`, runs `start`, then probes `/health`, the unclaimed-instance
claim-token file, owner registration + a presets listing (proving the setup
flow and the preset catalog both work on a fresh install), `status`, and
`stop` — confirming the process tree is actually gone afterward. `worker`
follows the same shape through `worker doctor`/`worker start`, and stops
there without installing anything if the box has no GPU (`worker start`
re-runs the same blocking doctor check before touching the network). A
failure at any phase is cleaned up regardless: an unconditional safety-net
`stop` (or worker-process kill) runs even when the phase that failed came
*after* a successful `start` — otherwise a failed run leaves an orphaned
backend/frontend on the box.

`--reuse-venv PATH` symlinks an existing venv into the checkout so `start`
skips its own pip install (confirmed live: a from-scratch `remote` run took
~103s including the CPU-torch install; the same run with `--reuse-venv`
pointed at that install's venv took ~13s). Local/hybrid pull the full CUDA
stack (multi-GB) and `worker` needs a real GPU — exercise those yourself with
`--profiles local,hybrid,worker` when you have the hardware and bandwidth for
it; don't run them on a shared box.

## Fresh install checks

A first-run bug shows up only on an instance that starts from nothing, so these checks never seed a
database and never skip a wizard call. There are three layers.

**Stub engine, no graphics card.** `tests/e2e/harness/stub_engine_app.py` is an app module that builds
the container with a stub pipeline executor (`build_container(generation_engine_factory=...)`) and
hands it to `create_app`. Everything above the pipes is real: routing, the orchestrator, saving,
history and readiness. The stub reads the file named by `E2E_STUB_ENGINE_CONTROL` on every
generation:

| File content | The generation |
|---|---|
| `ok` (or no file) | emits one small saved picture |
| `fail <text>` | emits an error carrying `<text>`, then raises it (try `CUDA out of memory. Tried to allocate 2.00 GiB`) |
| `hang` | waits until it is cancelled |

`ThrowawayApp(app_module="tests.e2e.harness.stub_engine_app:app", extra_env={...})` boots it.
`POTIONUI_PRESETS_DIR` (default `content/presets`) points the preset loader at another tree, the way
`POTIONUI_RECIPES_DIR` does for recipes, so a copy of a preset can pin its downloads to small fake files.
The logic is covered without a server by `tests/e2e/harness/test_stub_engine.py` and
`tests/bootstrap/test_container_seams.py`.

**Journeys.** `tests/e2e/journeys/fresh_setup_stub_engine.py` runs the shipped `sdxl-starter.yml` with only
its artifact size and checksum changed to match a fake payload, through exactly the wizard's calls
(readiness, machine, recipes, create run, poll, grant consent): a run that succeeds; a run whose test
picture fails (the run completes, the test row needs attention, the sentence has no exception text,
`generation_proven` stays not ready until a good picture is made); and a SIGKILL during the download
that the run survives on its own with a Range request. It is skipped unless `E2E_STUB_ENGINE=1`, because
the stub path has not been proven on a machine without a card yet: run it once with
`E2E_STUB_ENGINE=1 python tests/e2e/journeys/run.py fresh_setup_stub_engine`, then drop the opt-in.

**Browser.** `frontend/tests/e2e/fresh-install-guided-setup.spec.ts` clicks the real wizard on an unclaimed
instance; `frontend/tests/e2e/onboarding-guide.spec.ts` covers the small-card confirm, a test that did not
finish (including a reload) and the no-card options with mocked responses.

**By hand.** The real wizard on a truly fresh instance on Linux and on the Windows VM is still the gate for
an onboarding fix; the click-through is in `design-proposals/onboarding-review/click-through.md`.
