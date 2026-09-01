"""Migration 006: the built-in LoRA `strength` attribute becomes a range.

A LoRA's author publishes a strength band ("works at 0.7-1.0"), not one number,
so the `strength` attribute definition changes `field_type` from `slider` to
`range` and its values from a scalar to the `[low, high]` pair
`coerce_attribute_value` now produces. A value already recorded as a single
number is the degenerate band `[x, x]`, which is exactly what it always meant.

The definition's scalar `default_value` (1.0) goes away with the field type: a
range attribute distinguishes "no recommendation published" (NULL) from any
band, and the LoRA picker falls back to the preset's own `strength_default`
when nothing is set - so a stand-in default here would claim every LoRA
recommends 1.0.

Config is only rewritten when it still matches what `seeding.py` first wrote,
leaving an admin's own bounds alone. The widened floor (0 -> -2) admits the
negative strengths an inverted LoRA needs.

IDEMPOTENT: values already stored as a pair are left as they are.
"""

import json

from src.platform.database.database import db

_KEY = "strength"
_SEEDED_CONFIG = {"min": 0, "max": 2, "step": 0.05}
_NEW_CONFIG = {"min": -2, "max": 2, "step": 0.05}
_NEW_LABEL = "Recommended strength"
_NEW_DESCRIPTION = "Strength range this LoRA's author recommends; a single value is a 1:1 range"


def _as_pair(value):
    """The `[low, high]` form of an already-stored value, or None when it is
    already a pair (or is nothing this migration can speak for)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return [float(value), float(value)]


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT id, config FROM model_attribute_definitions WHERE key = ? AND source = 'core'",
            (_KEY,),
        )
        definition = cursor.fetchone()
        if definition:
            stored_config = json.loads(definition["config"]) if definition["config"] else {}
            config = _NEW_CONFIG if stored_config == _SEEDED_CONFIG else stored_config
            cursor.execute(
                """
                UPDATE model_attribute_definitions
                SET field_type = 'range', label = ?, description = ?, config = ?,
                    default_value = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (_NEW_LABEL, _NEW_DESCRIPTION, json.dumps(config), definition["id"]),
            )

        shared_converted = 0
        cursor.execute("SELECT id, model_metadata FROM models WHERE model_metadata IS NOT NULL")
        for row in cursor.fetchall():
            metadata = json.loads(row["model_metadata"])
            if not isinstance(metadata, dict) or _KEY not in metadata:
                continue
            pair = _as_pair(metadata[_KEY])
            if pair is None:
                continue
            metadata[_KEY] = pair
            cursor.execute(
                "UPDATE models SET model_metadata = ? WHERE id = ?",
                (json.dumps(metadata), row["id"]),
            )
            shared_converted += 1

        overlay_converted = 0
        cursor.execute(
            "SELECT user_id, model_id, value FROM user_model_attributes WHERE key = ? AND value IS NOT NULL",
            (_KEY,),
        )
        for row in cursor.fetchall():
            pair = _as_pair(json.loads(row["value"]))
            if pair is None:
                continue
            cursor.execute(
                "UPDATE user_model_attributes SET value = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND model_id = ? AND key = ?",
                (json.dumps(pair), row["user_id"], row["model_id"], _KEY),
            )
            overlay_converted += 1

    print(
        f"Migration 006_lora_recommended_strength_range: strength is a range "
        f"({shared_converted} shared values, {overlay_converted} user overrides widened)"
    )


def down():
    print("Migration 006_lora_recommended_strength_range: no-op (a widened range has no single value to restore)")
