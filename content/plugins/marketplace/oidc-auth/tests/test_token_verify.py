import base64
import time
from typing import Optional

import pytest

from backend import discovery, http_client
from backend.token_verify import TokenVerificationError, verify_id_token

from .oidc_fixture import FakeIdP, calculate_at_hash, default_claims, sign_id_token, unsigned_component


@pytest.fixture
def idp():
    fixture = FakeIdP()
    http_client.set_transport_override(fixture.transport())
    yield fixture
    http_client.set_transport_override(None)


def _jwks_uri(idp_fixture: FakeIdP) -> str:
    return f"{idp_fixture.issuer}/jwks"


async def _verify(
    idp_fixture: FakeIdP,
    token: str,
    nonce: str = "test-nonce",
    client_id: str = "test-client",
    access_token: Optional[str] = None,
):
    return await verify_id_token(
        token, _jwks_uri(idp_fixture), idp_fixture.issuer, client_id, nonce, access_token=access_token
    )


@pytest.mark.asyncio
async def test_valid_token_is_accepted(idp):
    token = idp.issue_id_token(sub="user-1", nonce="test-nonce")
    claims = await _verify(idp, token)
    assert claims["sub"] == "user-1"


@pytest.mark.asyncio
async def test_bad_signature_is_rejected(idp):
    token = idp.issue_id_token(sub="user-1", nonce="test-nonce")
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{header}.{payload}.{tampered_signature}"
    with pytest.raises(TokenVerificationError):
        await _verify(idp, tampered)


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(idp):
    claims = default_claims(idp.issuer, "someone-elses-client", "user-1", "test-nonce")
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_multi_audience_without_matching_azp_is_rejected(idp):
    claims = default_claims(idp.issuer, "user-1", "user-1", "test-nonce")
    claims["aud"] = ["test-client", "some-other-client"]
    claims["azp"] = "some-other-client"
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_multi_audience_with_matching_azp_is_accepted(idp):
    claims = default_claims(idp.issuer, "user-1", "user-1", "test-nonce")
    claims["aud"] = ["test-client", "some-other-client"]
    claims["azp"] = "test-client"
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    result = await _verify(idp, token)
    assert result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected(idp):
    claims = default_claims("https://not-the-idp.example", "test-client", "user-1", "test-nonce")
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_issuer_with_trailing_slash_on_either_side_is_accepted(idp):
    claims = default_claims(f"{idp.issuer}/", "test-client", "user-1", "test-nonce")
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    claims_result = await _verify(idp, token)
    assert claims_result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_issuer_with_appended_suffix_is_rejected(idp):
    claims = default_claims(f"{idp.issuer}.evil", "test-client", "user-1", "test-nonce")
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_nonce_mismatch_is_rejected(idp):
    token = idp.issue_id_token(sub="user-1", nonce="the-real-nonce")
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token, nonce="a-different-nonce")


@pytest.mark.asyncio
async def test_expired_token_is_rejected(idp):
    claims = default_claims(idp.issuer, "test-client", "user-1", "test-nonce")
    claims["exp"] = int(time.time()) - 60
    token = sign_id_token(claims, idp.private_pem, idp.jwk["kid"])
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_alg_none_is_rejected(idp):
    claims = default_claims(idp.issuer, "test-client", "user-1", "test-nonce")
    header = unsigned_component({"alg": "none", "kid": idp.jwk["kid"], "typ": "JWT"})
    payload = unsigned_component(claims)
    token = f"{header}.{payload}."
    with pytest.raises(TokenVerificationError, match="disallowed id_token algorithm"):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_hs256_forged_with_public_key_is_rejected(idp):
    import hashlib
    import hmac as hmac_module

    claims = default_claims(idp.issuer, "test-client", "user-1", "test-nonce")
    header = unsigned_component({"alg": "HS256", "kid": idp.jwk["kid"], "typ": "JWT"})
    payload = unsigned_component(claims)
    signing_input = f"{header}.{payload}".encode()
    signature = hmac_module.new(idp.public_pem.encode(), signing_input, hashlib.sha256).digest()
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    token = f"{header}.{payload}.{signature_b64}"
    with pytest.raises(TokenVerificationError, match="disallowed id_token algorithm"):
        await _verify(idp, token)


@pytest.mark.asyncio
async def test_id_token_with_correct_at_hash_is_accepted(idp):
    access_token = "the-access-token"
    token = idp.issue_id_token(
        sub="user-1", nonce="test-nonce", at_hash=calculate_at_hash(access_token)
    )
    result = await _verify(idp, token, access_token=access_token)
    assert result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_id_token_with_wrong_at_hash_is_rejected(idp):
    token = idp.issue_id_token(sub="user-1", nonce="test-nonce", at_hash=calculate_at_hash("some-other-token"))
    with pytest.raises(TokenVerificationError):
        await _verify(idp, token, access_token="the-real-access-token")


@pytest.mark.asyncio
async def test_id_token_without_at_hash_is_accepted_without_an_access_token(idp):
    token = idp.issue_id_token(sub="user-1", nonce="test-nonce")
    result = await _verify(idp, token, access_token=None)
    assert result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_unknown_kid_within_the_rate_limit_window_does_not_refetch(idp):
    idp.keys = []
    jwks_uri = _jwks_uri(idp)

    first = await discovery.get_jwk(jwks_uri, "kid-a", "RS256")
    assert first is None
    assert idp.jwks_requests == 1

    second = await discovery.get_jwk(jwks_uri, "kid-b", "RS256")
    assert second is None
    assert idp.jwks_requests == 1


@pytest.mark.asyncio
async def test_unknown_kid_after_the_rate_limit_window_refetches_and_finds_a_rotated_key(idp, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])
    idp.keys = []
    jwks_uri = _jwks_uri(idp)

    first = await discovery.get_jwk(jwks_uri, idp.jwk["kid"], "RS256")
    assert first is None
    assert idp.jwks_requests == 1

    now[0] += discovery.UNKNOWN_KID_REFETCH_MIN_INTERVAL_SECONDS + 1
    idp.keys = [idp.jwk]
    second = await discovery.get_jwk(jwks_uri, idp.jwk["kid"], "RS256")
    assert second is not None
    assert second["kid"] == idp.jwk["kid"]
    assert idp.jwks_requests == 2


@pytest.mark.asyncio
async def test_jwk_with_a_non_matching_alg_is_not_selected(idp):
    idp.keys = [{**idp.jwk, "alg": "RS384"}]
    key = await discovery.get_jwk(_jwks_uri(idp), idp.jwk["kid"], "RS256")
    assert key is None


@pytest.mark.asyncio
async def test_jwk_with_a_non_sig_use_is_not_selected(idp):
    idp.keys = [{**idp.jwk, "use": "enc"}]
    key = await discovery.get_jwk(_jwks_uri(idp), idp.jwk["kid"], "RS256")
    assert key is None
