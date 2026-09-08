"""019 rewrites leaked depot/backend paths out of recorded `model` display parameters.

`ParamGenerationOutputHandler` used to record whatever a preset's `param_emitter`
emitted for the `model` display parameter verbatim - a native path
(`models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors`) or a ComfyUI ref
(`QWEN/Qwen-Image-Lightning-8steps-V2.0.safetensors`), not something a person reading
generation history recognizes. The handler itself is fixed to record a name going
forward (see `src/features/generation/handlers/param_handler.py`); this migration
rewrites the rows that already carry the old, leaked spelling.

WHAT MOVES

`generation_parameters.parameter_value` for every row with `parameter_name = 'model'`
whose JSON-decoded value is a string containing a path separator: reduced to its own
basename. `models/loras/detail.safetensors` -> `detail.safetensors`.

WHAT DELIBERATELY DOES NOT MOVE

A bare filename with no separator (already what the fixed handler would record for an
unresolved value) is left as-is. This migration does not attempt the catalog lookup the
live handler does to recover a nicer display name - `models.filename` at generation
time may since have been renamed, moved to another type, or deleted outright, so a
"resolve then rewrite" migration could quietly attach today's catalog state to a
historical run. Reducing to the basename is the one transformation that is correct
regardless of what the catalog looks like now.

IDEMPOTENT. A value with no separator - including a value this migration already
rewrote - is left untouched by the `WHERE` clause below on a second run.
"""

import json

from src.platform.database.database import db


def _basename(value: str) -> str:
    """`value` reduced to its filename, splitting on either path separator - see
    `src/features/generation/handlers/param_handler.py`'s `_basename` for why not
    `pathlib.Path(value).name`."""
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT id, parameter_value FROM generation_parameters "
            "WHERE parameter_name = 'model' "
            "AND (parameter_value LIKE '%/%' OR parameter_value LIKE '%\\%')"
        )
        rows = cursor.fetchall()

        updates = []
        for row_id, raw in rows:
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, str) or ("/" not in value and "\\" not in value):
                continue
            updates.append((json.dumps(_basename(value)), row_id))

        if updates:
            cursor.executemany(
                "UPDATE generation_parameters SET parameter_value = ? WHERE id = ?",
                updates,
            )

        print(
            f"Migration 019_generation_model_parameter_basename: rewrote "
            f"{len(updates)} leaked 'model' parameter path(s) to their basename"
        )


def down():
    print(
        "Migration 019_generation_model_parameter_basename: no-op (the original "
        "path is not recoverable from the basename alone)"
    )
