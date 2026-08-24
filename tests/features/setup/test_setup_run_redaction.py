"""Redaction gate for setup-run safe payloads.

Whatever reaches `safe_input`/`safe_output` must be a whitelist of plain fields
with no secret-looking keys and no serialized objects. Pure-function tests, no
DB needed.
"""

from src.features.setup.run_dto import redact_safe_dict, redact_safe_payload


def test_strips_secret_looking_keys():
    cleaned = redact_safe_payload(
        {
            "device": "cuda",
            "api_key": "sk-123",
            "hf_token": "hf_xxx",
            "password": "hunter2",
            "authorization": "Bearer abc",
            "aws_secret_access_key": "zzz",
            "session_id": "s-1",
        }
    )
    assert cleaned == {"device": "cuda"}


def test_keeps_plain_scalars_and_nested_containers():
    payload = {
        "recipe_id": "native-image-starter",
        "version": 1,
        "enabled": True,
        "ratio": 0.5,
        "steps": ["host.inspect", "backend.ensure"],
        "nested": {"free_disk_gb": 42, "token": "leak"},
    }
    cleaned = redact_safe_payload(payload)
    assert cleaned == {
        "recipe_id": "native-image-starter",
        "version": 1,
        "enabled": True,
        "ratio": 0.5,
        "steps": ["host.inspect", "backend.ensure"],
        "nested": {"free_disk_gb": 42},  # secret key stripped even when nested
    }


def test_drops_non_plain_values():
    class Service:
        pass

    cleaned = redact_safe_payload(
        {"ok": "yes", "svc": Service(), "blob": b"bytes", "fn": lambda: 1}
    )
    assert cleaned == {"ok": "yes"}


def test_bool_not_coerced_to_int():
    cleaned = redact_safe_payload({"flag": True, "count": 3})
    assert cleaned["flag"] is True
    assert cleaned["count"] == 3


def test_redact_safe_dict_always_returns_dict():
    assert redact_safe_dict(None) == {}
    assert redact_safe_dict({}) == {}
    assert redact_safe_dict({"a": 1, "secret": "x"}) == {"a": 1}


def test_depth_guard_terminates():
    # A deeply nested structure must not recurse forever; beyond the cap it is
    # dropped rather than raising.
    node = {"k": "v"}
    for _ in range(20):
        node = {"child": node, "token": "x"}
    cleaned = redact_safe_payload(node)
    assert "token" not in cleaned
    assert isinstance(cleaned, dict)
