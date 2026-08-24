# Feature journeys

A feature journey is a small, importable self-verification check for one
recently landed backend change: it drives the real HTTP API of a throwaway
PotionUI instance and asserts the change actually behaves the way it's
supposed to. This is a local tool, not part of CI - run it yourself before
considering a backend change done.

## Convention

- One journey module per landed task, named for what it verifies (not the
  task id) - e.g. `chat_pre_actions_empty.py`, not `be69_check.py`.
- Every module exposes `run(app: ThrowawayApp) -> JourneyResult` (see
  `tests/e2e/harness/e2e_harness.py`). Use `JourneyResult.ok(*evidence)`,
  `.fail(*evidence)`, or `.skip(reason)` - evidence lines are printed by the
  runner, so put the actual status codes/response shapes you asserted on in
  there, not just "passed"/"failed".
- Default to no-GPU: a journey that would trigger a real generation must
  check `os.environ.get("E2E_ALLOW_GPU") == "1"` and skip with a reason
  otherwise.
- A journey needing a specific model file skips with a reason when that file
  isn't present in the depot - see "The models/tests depot" below. Never
  fail a journey just because a fixture model wasn't there.
- Journeys are read/write against a THROWAWAY instance only - never assume
  or touch a real instance's data. `ThrowawayApp` already enforces the port
  guard (8005/8006/3001 are refused) and gives every journey its own fresh
  temp DB/storage/models mirror.
- If a journey finds an actual backend bug, that's the point - print it in
  the failure evidence and report it. Journeys never patch `src/` themselves.

## The `models/tests` depot

`models/tests/{checkpoints,loras,upscalers,vae}/` is a small, checked-in
fixture models directory - NOT a real production depot. It starts empty
(each subdirectory holds only a `.gitkeep`); an empty depot must be a
bootable state, since most journeys don't need a real model at all.

When a journey genuinely needs a specific model file to exist, someone
copies (never symlinks) that one file into the matching `models/tests/<type>/`
subdirectory as the need comes up - this is a deliberate, manual, sparse
process. Journeys must never populate this directory themselves. If a needed
file is missing, skip with a reason that names the exact path expected, e.g.:

```python
if not (app.instance.models_dir / "checkpoints" / "some.safetensors").exists():
    return JourneyResult.skip("needs models/tests/checkpoints/some.safetensors - not present")
```

## Running

```bash
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/journeys/run.py
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/journeys/run.py chat_pre_actions_empty
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/journeys/run.py --models-dir /path/to/depot
PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/journeys/run.py --keep
```

All journeys given on the command line run against one shared throwaway
instance. Exit code is non-zero if any journey failed (skips don't fail the
run).
