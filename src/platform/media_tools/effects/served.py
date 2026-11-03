from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

import numpy as np

from src.platform.imaging.filters.cube import Cube
from src.platform.media_tools.effects.looks import empty_env, look_env
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.params import LOOK_PARAM, LUT_PARAM


def lut_options(runtime: EffectRuntime) -> List[dict]:
    files = runtime.list_assets(LUT_PARAM, ".cube")
    return [{"label": "None", "value": ""}] + [
        {"label": Path(ref).stem.replace("_", " "), "value": ref} for ref in files
    ]


def look_options(runtime: EffectRuntime, user_id: Optional[str]) -> List[dict]:
    options = [{"label": "None", "value": "", "env": empty_env(), "revision": ""}]
    for look in runtime.list_looks(user_id):
        options.append({
            "label": look.name,
            "value": look.id,
            "group": look.group,
            "env": look_env(look),
            "revision": look.revision,
        })
    return options


def served_params(params: List[dict], runtime: EffectRuntime, user_id: Optional[str]) -> List[dict]:
    kinds = {param.get("type") for param in params}
    if LUT_PARAM not in kinds and LOOK_PARAM not in kinds:
        return params
    options = {
        LUT_PARAM: lut_options(runtime) if LUT_PARAM in kinds else [],
        LOOK_PARAM: look_options(runtime, user_id) if LOOK_PARAM in kinds else [],
    }
    return [{**param, "options": options[param["type"]]} if param.get("type") in options else param for param in params]


def table_bytes(cube: Cube) -> bytes:
    return np.round(np.clip(cube.data, 0.0, 1.0) * 65535).astype("<u2").tobytes()


def table_key(kind: str, ref: str, revision: Any = "") -> str:
    return f"look:{ref}@{revision}" if kind == LOOK_PARAM else ref
