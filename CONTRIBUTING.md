# Contributing to PotionUI

Thanks for looking at the code. This is a working guide for getting a dev
environment running and checking a change before you send it — for the
architecture itself, see `CLAUDE.md`.

## Dev setup

You need Python 3.12+ and Node.js 18+.

```bash
git clone https://github.com/jtyszkiew/imagine.git potionui
cd potionui
./potionui start      # create the venv, install deps, launch backend + frontend
```

Or set up each side by hand:

```bash
# Backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt

# Frontend
cd frontend && npm install
```

## Running tests

**Backend** — run scoped to the area you touched, not the whole tree, unless
you're preparing a release:

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python -m pytest tests/ -q
```

The test tree mirrors `src/` (`tests/features/`, `tests/platform/`,
`tests/pipelines/`, `tests/architecture/`). Add new tests next to the code
they cover. `tests/architecture/test_layering.py` enforces the package
layering rules below — a change that crosses a boundary the wrong way fails
there before it fails anywhere else.

**Frontend**:

```bash
cd frontend
npm run check       # svelte-check + type-check — baseline is 0 errors, keep it there
npm run test:unit   # vitest
```

**Presets** — if you touch or add a preset:

```bash
python scripts/preset_lint.py
```

## The src/ layout, briefly

`src/` splits into five packages with a strict import direction:
`bootstrap` (the composition root — builds the container, wires routers),
`platform` (the shared substrate: database, filesystem, security, runtime,
plugins), `features` (one directory per domain: generation, presets, models,
chat, …), `pipelines` (the pipe contract and the pipes themselves), and
`plugin_api` (the only surface plugin code may import). `platform` may not
import `features`, `features` may not import `bootstrap`, and `pipelines` may
not import `features` or `bootstrap`. See `CLAUDE.md` for the full contract
and the rest of the architecture.

## Sending a PR

- Include tests with any behavioral change — a fix without a regression test
  that would have failed beforehand is hard to trust.
- Keep `npm run check` at 0 errors; don't introduce new type errors even if
  warnings already exist.
- Keep changes scoped — a PR that mixes an unrelated reformat with a fix is
  harder to review and harder to revert.
- If you're adding a preset, run `scripts/preset_lint.py` before opening the PR.

Found a security issue? See [SECURITY.md](SECURITY.md) instead of opening a
public issue.
