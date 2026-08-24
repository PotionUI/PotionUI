"""Backend half of the frontend groups-tabs loop bug: a user group with zero
assignments must return a stable, well-formed empty result from its
presets/llms/models endpoints, called twice in a row - not an error, not
`None`, not a shape that flips between calls (which is what fed the
frontend's `$effect` loop).

No-GPU: only group CRUD and the three read endpoints.
"""

from __future__ import annotations

from e2e_harness import JourneyResult, ThrowawayApp, raise_for_status

ENDPOINTS = ("presets", "llms", "models")


def _fetch_twice(app: ThrowawayApp, group_id: str, endpoint: str):
    first_resp = app.client.get(f"/api/user-groups/{group_id}/{endpoint}")
    first_body = raise_for_status("group-tabs", first_resp, f"Get group {endpoint} (call 1)")
    second_resp = app.client.get(f"/api/user-groups/{group_id}/{endpoint}")
    second_body = raise_for_status("group-tabs", second_resp, f"Get group {endpoint} (call 2)")
    return first_body.get("data"), second_body.get("data")


def run(app: ThrowawayApp) -> JourneyResult:
    create_resp = app.client.post("/api/user-groups/", json={"name": "e2e-empty-group", "description": None})
    create_body = raise_for_status("group-tabs", create_resp, "Create empty group")
    group = create_body.get("data") or {}
    group_id = group.get("id")
    if not group_id:
        return JourneyResult.fail(f"group creation returned no id: {create_body}")

    evidence = [f"Created group '{group.get('name')}' (id={group_id}) with zero assignments"]

    try:
        for endpoint in ENDPOINTS:
            first, second = _fetch_twice(app, group_id, endpoint)

            if not isinstance(first, list) or not isinstance(second, list):
                evidence.append(f"{endpoint}: expected a list both calls, got {type(first).__name__} / {type(second).__name__}")
                return JourneyResult(status="fail", evidence=evidence)
            if first != []:
                evidence.append(f"{endpoint}: call 1 expected [] for an unassigned group, got {first}")
                return JourneyResult(status="fail", evidence=evidence)
            if second != []:
                evidence.append(f"{endpoint}: call 2 expected [] for an unassigned group, got {second}")
                return JourneyResult(status="fail", evidence=evidence)
            if first != second:
                evidence.append(f"{endpoint}: shape flipped between call 1 ({first}) and call 2 ({second})")
                return JourneyResult(status="fail", evidence=evidence)

            evidence.append(f"{endpoint}: [] on both calls, stable")

        return JourneyResult(status="pass", evidence=evidence)
    finally:
        delete_resp = app.client.delete(f"/api/user-groups/{group_id}")
        if delete_resp.status_code >= 400:
            evidence.append(f"cleanup: failed to delete group {group_id} ({delete_resp.status_code})")
