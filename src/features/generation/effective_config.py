"""The effective configuration a pipe runs with, computed on the dispatching side.

A processed pipe carries only what its preset actually wrote. The rest of the
configuration it runs with comes from the pipe class itself -
``PipelineExecutor`` deep-merges ``get_default_config()`` *underneath* the
shipped config at execution time (``generation.py``). In-process that is
invisible; for an execution package it is not, because those defaults contain
literal ``models/...`` paths (the detailer family alone contributes 23), so a
package whose ``config`` looks path-free still resolves to host paths on
arrival.

This module computes that merge here instead, so a package carries the complete
effective config and the executing side contributes nothing. Re-running the
executor's merge over the result is a no-op, which is what makes it safe for
that merge to stay where it is.

**What is deliberately NOT folded in.** A backend's ``prepare_pipes`` injects
its own values (the native engine's ``device``/``dtype``/``vram_limit_gb``) and
does so with ``setdefault`` *before* the executor's merge, which puts backend
injection between the pipe defaults and the preset config in precedence. Those
three describe the dispatching host's GPU and must become worker-decided, so
they are not merged here - and folding the defaults in ahead of that injection
would silently demote all three to the pipe's own literals (measured: 36 keys
across the shipped native preset-modes, including ``device`` dropping from the
backend's configured device to a hardcoded ``"cuda"``).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

from src.features.generation.engine import deep_update
from src.pipelines.catalog import PipeCatalog
from src.platform.observability.logger import logger


def merge_pipe_defaults(
    pipes: List[Dict[str, Any]],
    pipe_catalog: PipeCatalog,
) -> List[Dict[str, Any]]:
    """Return *pipes* with each enabled pipe's config merged over its class defaults.

    Pure: neither the pipe dicts nor the pipe classes' default dicts are
    mutated. Disabled pipes are passed through untouched - the executor never
    resolves their class either. A pipe whose class this installation cannot
    resolve is passed through with a warning rather than raising: whether a
    worker can run a given pipe set is a compatibility question, answered by
    the package's required fingerprints, not by config assembly.
    """
    merged: List[Dict[str, Any]] = []

    for pipe in pipes:
        if not pipe.get('enabled'):
            merged.append(dict(pipe))
            continue

        pipe_class = pipe_catalog.get_pipe(pipe['name'])
        if pipe_class is None:
            logger.warning(
                f"[EFFECTIVE_CONFIG] Pipe '{pipe['name']}' is not resolvable on this "
                f"installation; shipping its config without class defaults"
            )
            merged.append(dict(pipe))
            continue

        defaults = copy.deepcopy(pipe_class.get_default_config() or {})
        config = copy.deepcopy(pipe.get('config') or {})

        merged_pipe = dict(pipe)
        merged_pipe['config'] = deep_update(defaults, config)
        merged.append(merged_pipe)

    return merged
