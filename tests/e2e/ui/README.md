# UI journeys (browser E2E)

A UI journey is a Playwright spec that drives a **real Chromium browser** against
the built frontend, served against a **throwaway backend**. It exists to catch the
class of bug an HTTP-only check can't see: stuck spinners, `$effect` request
loops, controls that never settle, highlight/reactivity regressions. It is the
browser-level companion to the HTTP checks in `tests/e2e/journeys/`.

This is a local tool, not part of CI. Run it yourself before considering a
frontend change done, and keep the evidence (screenshots + clips) it
produces.

## Convention

- **One spec per landed frontend task**, named for what it verifies (not the
  task id) — e.g. `empty-group-tabs.spec.ts`, not `fe47.spec.ts`. Specs live in
  `frontend/tests/e2e/*.spec.ts` (already a tests dir; the Python bridge and
  its artifacts live here under `tests/e2e/ui/`).
- Each spec saves **labeled screenshots of the decisive state** into
  `E2E_ARTIFACTS_DIR/<journey>/<label>.png` (the runner points that env var at
  `tests/e2e/ui/artifacts/<journey>/`). It also records a short **video clip**
  of the whole run (see below). Both are the reviewable evidence, so
  capture the settled/asserted state in screenshots and keep specs focused so
  clips stay short.
- A spec that needs fixture data (a model/preset) not present on a fresh
  instance must **skip with a reason** (`test.skip(condition, reason)`) that
  names exactly what's missing — never fail because a fixture wasn't there. Same
  rule as the HTTP journeys and the `models/tests` depot.
- Specs never touch a real instance. The runner boots a throwaway backend and
  serves a throwaway frontend; the backend port guard (26730/26732/26731 refused)
  and ephemeral ports below make that structural.

## The evidence flow (screenshots + video)

1. `run.py` clears `tests/e2e/ui/artifacts/` and exports its path as
   `E2E_ARTIFACTS_DIR`.
2. Each spec writes `<label>.png` files under `artifacts/<journey>/`.
3. Playwright records `video: 'on'` for every test (pass, fail, or skip). After
   the run, `run.py` copies each test's clip to `artifacts/<journey>/<journey>.webm`
   (renamed off Playwright's hash dirs into a sane per-journey name).
4. Keep both the `*.png` screenshots and the `*.webm` clips as evidence that
   the change works as intended.

`artifacts/` is gitignored (see `.gitignore`).

## Serving approach

`npm run build` runs once per `run.py` invocation (retried once after 60s if a
concurrent build left the tree in a transient broken state). After that, the
runner splits the requested specs into **chunks** (see "Chunking" below) and,
**per chunk**, does:

1. Boots one throwaway backend via `e2e_harness.ThrowawayApp` — fresh
   temp SQLite DB + storage, a read-only symlink mirror of `models/tests`, and
   the instance claimed as owner (`e2e-owner`, fixed password so the browser can
   log in). Backend port: auto-picked **>= 8055**.
2. Serves the build with **`vite preview`** on an **ephemeral port >= 4173**
   (never 26731 — the real dev server stays banned — and never 26730/26732).
   `vite preview` serves the SvelteKit SSR output regardless of the configured
   `adapter-auto` (which can't detect a deploy target in this container but does
   not need to). A `preview` block added to `frontend/vite.config.ts` proxies
   `/api`, `/health`, and `/ws` to the throwaway backend, reading its port from
   `E2E_BACKEND_PORT`. That block is **additive** — the dev `server` proxy
   (including `autoRewrite: true`) is untouched.
3. Runs `npx playwright test` for just that chunk's specs, exporting
   `E2E_BASE_URL`, `E2E_BACKEND_URL`, `E2E_USERNAME`, `E2E_PASSWORD`,
   `E2E_ARTIFACTS_DIR`, while a background thread polls the preview process
   (see "Preview death detection" below).
4. Collects that chunk's screenshots + video clips into `artifacts/<journey>/`,
   then tears down preview + backend before starting the next chunk.

## Chunking

Passing ~10+ specs to a single Playwright invocation reliably killed the
`vite preview` process partway through the run: every remaining test then
failed with `net::ERR_CONNECTION_REFUSED` at `/login`, and `preview.log`
recorded nothing (no crash, no stack — it just stopped responding). Running
the same total spec count as several separate `run.py` invocations of 3 specs
each — which restart the backend, the preview server, and the Playwright
process every time — stayed healthy.

`run.py` now performs that known-good pattern itself: it splits the spec list
into chunks of `--chunk-size` (**default 3**, matching the empirically safe
batch size) and boots a **fresh backend + fresh preview + fresh Playwright
process per chunk**, tearing everything down between chunks. A caller running
the full suite (or any list of 10+ specs) in one command can no longer trigger
the death by accident. Pass `--chunk-size 0` to force a single chunk (i.e. the
old, unsafe all-in-one-invocation behavior) — only useful for deliberately
reproducing the failure with host-level diagnostics (`dmesg`/journal) attached.

Trade-off knowingly accepted: specs still share **one** throwaway backend
**within a chunk**, so state can leak between specs in the same chunk (a spec
asserting a global empty state can fail if an earlier spec in the same chunk
installed a preset into the shared instance). Chunking bounds the blast radius
of that leak to `--chunk-size` specs instead of the whole suite, but does not
eliminate it — full per-spec backend isolation was judged too expensive to add
in this pass (a fresh backend boot per *spec* rather than per *chunk of 3*).
If a spec depends on a truly empty/global instance state, either order it
first in its own explicit `run.py <that-spec>` invocation, or run it alone.

## Preview death detection

Because the death is a harness reliability failure, not a spec defect, it must
never look like one. While Playwright runs against a chunk's preview server, a
background thread polls the preview subprocess. If it exits on its own
(distinct from `run.py` intentionally tearing it down after the chunk
finishes):

- Playwright is terminated immediately for that chunk, instead of being left
  to run out the clock on a `net::ERR_CONNECTION_REFUSED` cascade against every
  remaining spec.
- `run.py` prints an unmistakable, clearly-labeled diagnostic block naming the
  chunk that died, the preview process's **own exit status** (e.g. "killed by
  SIGKILL" vs. a clean exit — never captured before this), the preview log
  path for that chunk, and exactly which specs in later chunks never ran.
- `run.py` exits with status **3** (`EXIT_PREVIEW_DIED`), distinct from an
  ordinary test failure (**1**) or a usage error (**2**), so a caller — human
  or scripted — can tell "the harness broke" apart from "a spec failed".
- The chunk's own pass/fail results are called out as untrustworthy and should
  be re-verified in isolation, since we don't know exactly which of that
  chunk's specs completed before the process died.

Each chunk's preview process writes to its own
`artifacts/preview-chunk<N>.log` (rather than one shared `preview.log`), so a
crashed chunk's log survives even once later chunks start their own preview
process.

### Why not the dev server / a reverse proxy

The real dev server on 26731 is banned. `vite preview` on an ephemeral port is
the simplest robust option: it already serves the SSR build and honors
`preview.proxy` for `/api` + `/ws`, so no bespoke reverse proxy is needed. A
hand-rolled proxy was the documented fallback if `vite preview` couldn't proxy;
it can, so we don't.

## Ports

| Role | Port | Rule |
| --- | --- | --- |
| Throwaway backend | auto from 8055 | never 26730/26732/26731 (guarded in `e2e_harness`) |
| Frontend preview | auto from 4173 | ephemeral; never 26731/26730/26732 |

## Deps

Playwright is a `frontend/` **devDependency**, and tests serve the frontend
on an **ephemeral** port (the real dev server stays banned). Added to
`frontend/package.json` devDeps:

- `playwright` and `@playwright/test` (pinned by `npm install -D`, 1.61.1).
- Chromium is installed out-of-tree via `npx playwright install chromium`
  (cached under `~/.cache/ms-playwright`, not committed).
- **One-time container step:** the browser needs system libs to launch. If it
  fails with `libglib-2.0.so.0: cannot open shared object file`, run
  `sudo npx playwright install-deps chromium` once.

## Running

```bash
# every spec (builds the frontend, ~25s, then runs Chromium headless)
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py

# one spec
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py empty-group-tabs

# reuse the last harness build in frontend/.e2e-build (skip the ~25s rebuild)
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --skip-build

# keep the throwaway backend + temp dir on disk for inspection
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --keep

# override the chunk size (default 3 - see "Chunking" above)
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --chunk-size 5
```

## Perf harness (`--perf`)

`run.py --perf` runs the scenarios in `frontend/tests/perf/*.perf.ts` through the same
lock, build, throwaway backend and `vite preview` as the journeys. The scenarios are
invisible to the normal run: the journeys config only matches `*.spec.ts` under
`tests/e2e`, and `playwright.perf.config.ts` only matches `*.perf.ts` under
`tests/perf` (1440x900, device scale factor 1, no video, no trace, no screenshots,
one worker, no retries).

```bash
# all 8 scenarios, 5 measured runs each after one discarded warmup
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --perf --skip-build

# a subset, fewer runs, amplified main-thread cost
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --perf cold-generate slider-drag --perf-runs 3 --perf-throttle 4

# before/after table for two reports (paths, `latest`, or `prev`); no backend, no build
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --perf-compare prev latest
```

| Scenario | What it measures |
| --- | --- |
| `cold-generate` | Fresh context to first usable Generate form: FCP, LCP, time to interactive, requests and JS/CSS/font bytes by resource type, the list of loaded JS files |
| `slider-drag` | 240 mouse moves dragging a slider in a 60-field form (cloned from the real form so field types stay valid) |
| `gen-preview-20` | 20 mocked 1024x1024 `workbench_update` previews at 10 Hz: WS send to decoded and painted |
| `tab-switch` | 3 rounds over 8 tabs, each with the 60-field form: click to the target tab's prompt painted |
| `long-prompt-typing` | 800 characters typed into the prompt editor with 8 tabs open: Event Timing and the tabs localStorage write cost |
| `video-scrub` | 2 passes of 120 moves across the video editor timeline with a 10-step edit stack over a runtime-encoded 1080p clip |
| `image-pan-zoom-brush` | 3840x2160 image in Paint: 60 wheel zooms and 40 pans, 100 brush strokes, JS heap after GC, then an Adjust slider drag |
| `history-marquee` | 96 mocked history records, one marquee drag over the whole grid (asserts all 96 are selected) |

Every run uses a fresh browser context (empty HTTP cache and storage, the owner
`storageState` re-applied), so runs never accumulate state. Metrics come from in-page
observers (long tasks, Event Timing, a `requestAnimationFrame` frame probe, a
`MutationObserver`, a `Storage.setItem` wrapper, capture-phase `seeking`/`seeked`
listeners) and CDP `Performance.getMetrics` deltas; there is no trace parsing.
`--perf-trace` saves a Chromium trace per measured run next to the JSON for manual
inspection and never feeds the numbers. Each metric is stored as `{median, min, max,
runs}`; reports land in `tests/e2e/ui/artifacts/perf/<UTC timestamp>.json` (gitignored)
with `meta` (throttle, viewport, Chromium, CPU, git sha and dirty flag, build mtime).

Rules for numbers you can trust:

- Run on an idle machine, one run at a time (the run lock enforces this). Do not `nice` the
  run when you are recording numbers for a card; `taskset -c 0-3` is fine, note it.
- Compare reports taken with the same `--perf-throttle` and the same viewport; `--perf-compare`
  prints a warning when they differ.
- The compare flag is `noise` unless the change exceeds the larger of 5% of the before median,
  half the before range, and a per-unit floor. `better`/`worse` always means lower/higher.
- Comparisons need a quiet box. Reports record host load per scenario (1-min load average at start and end,
  logical CPU count). `--perf-compare` warns on top and labels changes `unreliable (load)` when either report
  is above 0.25 load per CPU; check the `host load` line of a summary before trusting numbers.
- Each scenario gets a fresh backend (`--chunk-size` defaults to 1 in perf mode).
- Preview images, the 4K image, the thumbnail and the video clip are generated at run time into
  `os.tmpdir()/potionui-perf/<hash>/` (never committed, safe to delete). The clip needs ffmpeg:
  `run.py` resolves it from `imageio_ffmpeg` in the venv, then `PATH`, and exports `PERF_FFMPEG`.
  Without it `video-scrub` reports `status: "skipped"` with the reason.
- A scenario whose precondition is missing (no native image preset, no ffmpeg) is `skipped`
  with a reason, never a silent empty comparison.
- Network is never touched: every non-loopback request is aborted, every API response the
  scenario depends on is mocked or served by the throwaway backend.
- `vite preview` serves gzip, so `js_kb` and `total_kb` are transfer bytes; `extra.compression`
  in the JSON records the encoding.

## Bundle budget

`npm run check:bundle` (in `frontend/`) budgets two static-import closures from
`.svelte-kit/output/client/.vite/manifest.json` against `frontend/bundle-budget.json`: the app shell
(`js_raw`/`css_raw`: start, app and the root layout node, paid by every route) and the `/generate`
route only (`routes["/generate"]`: the leaf node's closure minus what the shell already has).
Dynamic imports are not budgeted. Set `E2E_BUILD_DIR` (or `--dir`) to point it at the harness build
(`frontend/.e2e-build`). The onboarding-smoke workflow runs it after `npm run build`. See
`docs/testing-notes.md`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every spec in every chunk passed. |
| 1 | At least one spec failed (skips don't fail the run), or a harness boot stage failed (build, backend boot, preview never became reachable). |
| 2 | Usage error — unknown spec name(s), or no specs found. |
| 3 | `EXIT_PREVIEW_DIED` — the preview process died mid-chunk. This is a harness failure, not a spec failure; see "Preview death detection" above. Any results already reported for the dying chunk are untrustworthy. |

Evidence lands in `tests/e2e/ui/artifacts/<journey>/` (`.png` + `.webm`).
