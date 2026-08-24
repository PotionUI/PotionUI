"""Model-write route authz, checked together:

1. `PUT /api/models/{id}/description` and `PUT /api/models/{id}/tags` are
   admin-gated (they were removed outright when they were open to any
   authenticated user, then restored behind `Depends(get_current_admin_user)`).
   A non-admin caller must get 403; the admin owner clears the gate (with a
   fake id the handler answers a 200 envelope carrying `model_not_found`,
   proving the route exists and the gate passed).
2. The model user-assignment endpoints (`GET /api/models/user-assignments/
   {user_id}`, `POST /api/models/user-assignments`, `DELETE /api/models/
   user-assignments/{user_id}/{model_id}`) are admin-gated the same way -
   a non-admin caller is rejected (403, the house pattern for "admin
   privileges required") without needing a real user_id/model_id.

No-GPU: routing/authz checks only, no model needs to exist.
"""

from __future__ import annotations

from e2e_harness import JourneyResult, ThrowawayApp

FAKE_MODEL_ID = "e2e-nonexistent-model-id"
FAKE_USER_ID = "e2e-nonexistent-user-id"

METADATA_WRITES = [
    (f"/api/models/{FAKE_MODEL_ID}/description", {"description": "e2e-should-not-apply"}),
    (f"/api/models/{FAKE_MODEL_ID}/tags", {"tag_ids": []}),
]


def run(app: ThrowawayApp) -> JourneyResult:
    evidence = []
    failures = []

    second = app.create_second_user(account_type="USER")
    evidence.append(f"Created non-admin second user '{second.username}' for the authz checks")

    for path, payload in METADATA_WRITES:
        resp = second.client.put(path, json=payload)
        evidence.append(f"PUT {path} (as non-admin) -> {resp.status_code}")
        if resp.status_code != 403:
            failures.append(f"PUT {path} as non-admin expected 403 (admin gate), got {resp.status_code}")

        resp = app.client.put(path, json=payload)
        evidence.append(f"PUT {path} (as admin) -> {resp.status_code}")
        if resp.status_code in (403, 404, 405):
            failures.append(f"PUT {path} as admin expected to clear the gate, got {resp.status_code}")

    authz_checks = [
        ("GET", f"/api/models/user-assignments/{FAKE_USER_ID}", None),
        ("POST", "/api/models/user-assignments", {"model_id": FAKE_MODEL_ID, "user_id": FAKE_USER_ID}),
        ("DELETE", f"/api/models/user-assignments/{FAKE_USER_ID}/{FAKE_MODEL_ID}", None),
    ]
    for method, path, payload in authz_checks:
        if method == "GET":
            resp = second.client.get(path)
        elif method == "POST":
            resp = second.client.post(path, json=payload)
        else:
            resp = second.client.delete(path)
        evidence.append(f"{method} {path} (as non-admin) -> {resp.status_code}")
        if resp.status_code not in (403, 404):
            failures.append(f"{method} {path} as non-admin expected 403/404, got {resp.status_code}")

    if failures:
        evidence.extend(failures)
        return JourneyResult(status="fail", evidence=evidence)
    return JourneyResult(status="pass", evidence=evidence)
