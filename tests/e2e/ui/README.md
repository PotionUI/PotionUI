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
  serves a throwaway frontend; the backend port guard (8005/8006/3001 refused)
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
   (never 3001 — the real dev server stays banned — and never 8005/8006).
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

The real dev server on 3001 is banned. `vite preview` on an ephemeral port is
the simplest robust option: it already serves the SSR build and honors
`preview.proxy` for `/api` + `/ws`, so no bespoke reverse proxy is needed. A
hand-rolled proxy was the documented fallback if `vite preview` couldn't proxy;
it can, so we don't.

## Ports

| Role | Port | Rule |
| --- | --- | --- |
| Throwaway backend | auto from 8055 | never 8005/8006/3001 (guarded in `e2e_harness`) |
| Frontend preview | auto from 4173 | ephemeral; never 3001/8005/8006 |

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

# reuse the last build (skip the ~25s rebuild)
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --skip-build

# keep the throwaway backend + temp dir on disk for inspection
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --keep

# override the chunk size (default 3 - see "Chunking" above)
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py --chunk-size 5
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every spec in every chunk passed. |
| 1 | At least one spec failed (skips don't fail the run), or a harness boot stage failed (build, backend boot, preview never became reachable). |
| 2 | Usage error — unknown spec name(s), or no specs found. |
| 3 | `EXIT_PREVIEW_DIED` — the preview process died mid-chunk. This is a harness failure, not a spec failure; see "Preview death detection" above. Any results already reported for the dying chunk are untrustworthy. |

Evidence lands in `tests/e2e/ui/artifacts/<journey>/` (`.png` + `.webm`).
