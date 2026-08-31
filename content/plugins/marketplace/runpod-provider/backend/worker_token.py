"""Generating the shared secret a provisioned Pod's worker checks on every
route (`POTIONUI_WORKER_TOKEN` - see `src/features/remote_execution/worker/config.py`).

`secrets.token_urlsafe(32)` yields 256 bits of entropy from the OS CSPRNG,
URL-safe so it drops straight into an env var and a Bearer header with no
escaping. Never log this value - it is the only thing standing between the
public RunPod proxy URL and an unauthenticated execution slot.
"""

import secrets

TOKEN_BYTES = 32


def generate_worker_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)
