import base64
import hashlib

from backend.pkce import code_challenge_s256, generate_code_verifier


def test_code_verifier_length_is_within_rfc7636_bounds():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128


def test_code_challenge_matches_manual_sha256():
    verifier = generate_code_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    assert code_challenge_s256(verifier) == expected


def test_code_challenge_has_no_padding():
    verifier = generate_code_verifier()
    assert "=" not in code_challenge_s256(verifier)


def test_two_verifiers_are_not_equal():
    assert generate_code_verifier() != generate_code_verifier()
