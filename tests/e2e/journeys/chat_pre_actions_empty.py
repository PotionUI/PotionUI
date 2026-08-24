"""GET /api/chat/pre-actions returns an empty action list on a fresh
instance - the ComfyUI clear-VRAM pre-chat action was removed, and no
core code registers a replacement.

No-GPU. No plugin needs to be enabled for this journey: pre-chat actions are
only ever contributed by a plugin's `chat.pre_actions.register` hook, and a
fresh instance starts with every discoverable plugin disabled (see
`comfyui_presets_plugin_scoped.py` for the fuller argument), so this assumes
nothing about a specific plugin's state - it's just checking core no longer
ships a hardcoded action.
"""

from __future__ import annotations

from e2e_harness import JourneyResult, ThrowawayApp, raise_for_status


def run(app: ThrowawayApp) -> JourneyResult:
    resp = app.client.get("/api/chat/pre-actions")
    body = raise_for_status("chat-pre-actions", resp, "List pre-chat actions")
    data = body.get("data") or {}
    actions = data.get("actions")

    if actions is None:
        return JourneyResult.fail(f"response had no 'actions' key: {body}")
    if actions:
        ids = [a.get("id") for a in actions]
        return JourneyResult.fail(f"expected an empty action list, got {len(actions)}: {ids}")

    return JourneyResult.ok(
        f"GET /api/chat/pre-actions -> 200, data.actions == [] (status {resp.status_code})",
    )
