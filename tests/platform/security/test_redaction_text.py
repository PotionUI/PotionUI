"""`redact_text`: credentials masked in free text, everything else kept."""

import traceback

import pytest

from src.platform.security.redaction import SECRET_MASK, is_secret_key, redact_text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Authorization: Bearer sk-live-abc123", f"Authorization: {SECRET_MASK}"),
        ("authorization: Bearer sk-live-abc123", f"authorization: {SECRET_MASK}"),
        ("sent with Bearer sk-live-abc123", f"sent with Bearer {SECRET_MASK}"),
        ("GET /v1/models?api_key=sk-1&steps=20", f"GET /v1/models?api_key={SECRET_MASK}&steps=20"),
        ('{"password": "hunter2", "steps": 20}', f'{{"password": "{SECRET_MASK}", "steps": 20}}'),
        ("{'api_key': 'sk-1', 'width': 1024}", f"{{'api_key': '{SECRET_MASK}', 'width': 1024}}"),
        ("client_secret=abc123 expired", f"client_secret={SECRET_MASK} expired"),
        ("TOKEN=abc123", f"TOKEN={SECRET_MASK}"),
        ("Cookie: session=abc; other=def", f"Cookie: {SECRET_MASK}"),
    ],
)
def test_credential_shapes_are_masked(text, expected):
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Traceback (most recent call last):",
        '  File "/app/src/features/generation/engine.py", line 41, in run',
        "ValueError: steps=20 is out of range at 12:34:56",
        "max_tokens=4096 exceeded, monkey=1",
        "nothing credential shaped here",
        "",
    ],
)
def test_ordinary_text_is_untouched(text):
    assert redact_text(text) == text


def test_an_ordinary_key_does_not_shadow_a_credential_later_on_the_line():
    """A non-credential key must not swallow what follows it.

    A pattern that matched every `key: value` would consume `error: ...` whole
    and never look at the `api_key=` sitting inside it.
    """
    assert redact_text("error: api_key=sk-1") == f"error: api_key={SECRET_MASK}"


def test_a_real_traceback_comes_out_masked():
    try:
        raise RuntimeError("upstream rejected Authorization: Bearer sk-live-9f2c")
    except RuntimeError:
        text = traceback.format_exc()

    masked = redact_text(text)

    assert "sk-live-9f2c" not in masked
    assert SECRET_MASK in masked
    assert "RuntimeError" in masked
    assert "test_a_real_traceback_comes_out_masked" in masked


def test_the_key_survives_so_the_line_still_reads():
    assert redact_text("api_key=sk-1").startswith("api_key=")


def test_a_non_string_is_returned_unchanged():
    assert redact_text(None) is None


def test_hyphenated_header_names_count_as_secret_keys():
    assert is_secret_key("x-api-key")
    assert is_secret_key("X-Auth-Token")
    assert redact_text("x-api-key: abc123") == "x-api-key: ***"
