"""The comfyui-backend plugin's OWN presets (`content/plugins/marketplace/comfyui-
backend/presets/**/preset.yml`, declared via manifest `presets: - path:
"presets"`) only ever load when the plugin is enabled -
`PresetTemplateLoader.all_preset_roots()` extends its search roots with
`plugin_preset_roots(registry.get_enabled_plugins())`, and a disabled
plugin's presets are never even read off disk.

Filtering the presets list by `engine == "comfyui"` alone is NOT enough to
test this in general: nothing stops some OTHER plugin from also shipping a
comfyui-engine preset under its own root, in which case that plugin's state
- not this one's - would decide whether it's listed. So this journey reads
the plugin's OWN preset ids straight off disk and checks the API for
exactly those.

This journey doesn't assume which state a fresh instance's plugin starts in;
it asks `GET /api/plugins` what the comfyui-backend plugin's *actual*
`enabled` value is, then asserts the presets list matches that:

  - enabled  -> every one of the plugin's own preset ids is listed
  - disabled/absent -> none of the plugin's own preset ids is listed

No-GPU: this only reads plugin preset.yml files, `GET /api/plugins`, and
`GET /api/presets`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from e2e_harness import JourneyResult, ThrowawayApp, raise_for_status

PLUGIN_ID = "comfyui-backend"
PLUGIN_PRESETS_DIR = (
    Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / PLUGIN_ID / "presets"
)


def _plugin_own_preset_ids() -> list:
    ids = []
    for preset_yml in sorted(PLUGIN_PRESETS_DIR.glob("**/preset.yml")):
        data = yaml.safe_load(preset_yml.read_text()) or {}
        preset_id = data.get("id")
        if preset_id:
            ids.append(preset_id)
    return ids


def run(app: ThrowawayApp) -> JourneyResult:
    own_ids = _plugin_own_preset_ids()
    if not own_ids:
        return JourneyResult.skip(f"no preset.yml files found under {PLUGIN_PRESETS_DIR} to check against")

    plugins_resp = app.client.get("/api/plugins")
    plugins_body = raise_for_status("comfyui-presets", plugins_resp, "List plugins")
    plugins = plugins_body.get("data") or []
    plugin_row = next((p for p in plugins if p.get("id") == PLUGIN_ID), None)

    if plugin_row is None:
        rule = f"'{PLUGIN_ID}' has never been discovered into the plugin table on this fresh instance (no scan has run) - default is absent/disabled"
        expect_present = False
    else:
        rule = f"'{PLUGIN_ID}' is in the plugin table with enabled={plugin_row.get('enabled')}"
        expect_present = bool(plugin_row.get("enabled"))

    presets_resp = app.client.get("/api/presets", params={"include_uninstalled": "true"})
    presets_body = raise_for_status("comfyui-presets", presets_resp, "List presets")
    listed_ids = {p.get("id") for p in (presets_body.get("data") or [])}
    found = [pid for pid in own_ids if pid in listed_ids]

    evidence = [
        f"Discovered rule: {rule}",
        f"Plugin's own preset ids on disk ({len(own_ids)}): {own_ids}",
        f"GET /api/presets?include_uninstalled=true -> {len(listed_ids)} total; {len(found)}/{len(own_ids)} of the plugin's own ids present",
    ]

    if expect_present and len(found) != len(own_ids):
        missing = [pid for pid in own_ids if pid not in listed_ids]
        evidence.append(f"expected every plugin preset listed (plugin enabled) but missing: {missing}")
        return JourneyResult(status="fail", evidence=evidence)
    if not expect_present and found:
        evidence.append(f"expected none of the plugin's presets listed (plugin not enabled) but found: {found}")
        return JourneyResult(status="fail", evidence=evidence)

    return JourneyResult(status="pass", evidence=evidence)
