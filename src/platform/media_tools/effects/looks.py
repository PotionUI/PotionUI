from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

import numpy as np

from src.platform.imaging.filters.cube import Cube
from src.platform.imaging.filters.lut import compile_lut
from src.platform.imaging.filters.ops import CORE_OPS, OpSpec, resolve_params, split_steps

LIVE_SLOTS = 4
SLOT_KEYS = ("kind", "a", "b", "c")
LIVE_SPATIAL = {"vignette": (1, ("amount", "midpoint", "feather")), "grain": (2, ("amount", "size", "seed"))}
MAX_CACHED_TABLES = 16


@dataclass(frozen=True)
class Look:
    id: str
    name: str
    group: str
    intensity: float
    steps: tuple
    revision: str
    cube: Optional[Cube] = None
    ops: Mapping[str, OpSpec] = field(default_factory=lambda: dict(CORE_OPS))
    impls: Mapping[str, Any] = field(default_factory=dict)


class LookLibrary(Protocol):
    def resolve(self, ref: str, user_id: Optional[str]) -> Optional[Look]: ...

    def list(self, user_id: Optional[str]) -> Sequence[Look]: ...


def empty_env() -> dict:
    env: dict = {"has": 0, "intensity": 0, "live": 1, "spatial": 0}
    for slot in range(LIVE_SLOTS):
        for key in SLOT_KEYS:
            env[f"s{slot}_{key}"] = 0
    return env


def look_env(look: Optional[Look]) -> dict:
    env = empty_env()
    if look is None:
        return env
    _, spatial = split_steps(list(look.steps), look.ops)
    env.update(has=1, intensity=float(look.intensity), spatial=len(spatial))
    if len(spatial) > LIVE_SLOTS:
        env["live"] = 0
    for slot, step in enumerate(spatial[:LIVE_SLOTS]):
        entry = LIVE_SPATIAL.get(step["op"]) if step["op"] in CORE_OPS else None
        if entry is None:
            env["live"] = 0
            continue
        kind, names = entry
        values = resolve_params(look.ops[step["op"]], step)
        env[f"s{slot}_kind"] = kind
        for key, name in zip(SLOT_KEYS[1:], names):
            env[f"s{slot}_{key}"] = float(values[name])
    return env


_tables: "OrderedDict[tuple[str, str], Cube]" = OrderedDict()
_tables_lock = threading.Lock()


def look_table(look: Look) -> Cube:
    key = (look.id, look.revision)
    with _tables_lock:
        cached = _tables.get(key)
        if cached is not None:
            _tables.move_to_end(key)
            return cached
    lut = compile_lut(list(look.steps), look.cube, 100, look.ops, look.impls)
    table = Cube(size=lut.size, data=np.clip(lut.data, 0.0, 1.0).astype(np.float32), title=look.name)
    with _tables_lock:
        _tables[key] = table
        while len(_tables) > MAX_CACHED_TABLES:
            _tables.popitem(last=False)
    return table
