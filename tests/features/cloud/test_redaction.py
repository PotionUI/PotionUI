from src.features.cloud.redaction import redact_headers, redact_url, scrub_text


def test_sensitive_headers_are_masked_and_others_kept():
    masked = redact_headers({"Authorization": "Bearer abc123456", "X-Api-Key": "k", "Accept": "json", "Cookie": "a=b", "Proxy-Authorization": "x"})
    assert masked["Authorization"] == "***"
    assert masked["X-Api-Key"] == "***"
    assert masked["Cookie"] == "***"
    assert masked["Proxy-Authorization"] == "***"
    assert masked["Accept"] == "json"


def test_url_query_values_and_userinfo_are_removed():
    cleaned = redact_url("https://user:pw@api.example.com/v1/x?api_key=SECRET&page=2#frag")
    assert "SECRET" not in cleaned
    assert "pw" not in cleaned
    assert "page=" in cleaned
    assert "frag" not in cleaned
    assert cleaned.startswith("https://api.example.com/v1/x")


def test_scrub_text_removes_known_secrets_and_bearer_tokens():
    text = scrub_text("failed with Bearer sk-live-123456789 and key topsecretvalue", ["topsecretvalue"])
    assert "sk-live-123456789" not in text
    assert "topsecretvalue" not in text
