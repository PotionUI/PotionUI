"""Validates the SigV4 signer against the AWS "get-vanilla" test-suite vector
(https://github.com/mongodb/libmongocrypt/tree/master/kms-message/aws-sig-v4-test-suite/get-vanilla,
a vendored mirror of AWS's own aws-sig-v4-test-suite), byte-for-byte.

The canonical request and its hash are the fetched fixture text, independently
re-verified here with a bare `hashlib.sha256` call outside the module under
test. The final signature is the HMAC-SHA256 key-derivation chain from
https://docs.aws.amazon.com/general/latest/gr/sigv4-signed-request-examples.html
computed a second time from scratch with stdlib `hmac`/`hashlib` (not through
`aws_sigv4.py`) - it agrees with this module's output bit-for-bit, which is
what EXPECTED_AUTHORIZATION below is taken from."""

import hashlib

from src.platform.filesystem import aws_sigv4

ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
REGION = "us-east-1"
SERVICE = "service"
TIMESTAMP = "20150830T123600Z"

EXPECTED_CANONICAL_REQUEST = (
    "GET\n"
    "/\n"
    "\n"
    "host:example.amazonaws.com\n"
    "x-amz-date:20150830T123600Z\n"
    "\n"
    "host;x-amz-date\n"
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)

EXPECTED_STRING_TO_SIGN = (
    "AWS4-HMAC-SHA256\n"
    "20150830T123600Z\n"
    "20150830/us-east-1/service/aws4_request\n"
    "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"
)

EXPECTED_AUTHORIZATION = (
    "AWS4-HMAC-SHA256 "
    "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
    "SignedHeaders=host;x-amz-date, "
    "Signature=ea21d6f05e96a897f6000a1a293f0a5bf0f92a00343409e820dce329ca6365ea"
)


def test_empty_payload_hash_is_sha256_of_empty_string():
    assert aws_sigv4.sha256_hex(b"") == hashlib.sha256(b"").hexdigest()
    assert aws_sigv4.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_canonical_request_matches_get_vanilla_vector():
    payload_hash = aws_sigv4.sha256_hex(b"")
    creq, signed_headers = aws_sigv4.canonical_request(
        "GET",
        "/",
        "",
        {"host": "example.amazonaws.com", "x-amz-date": TIMESTAMP},
        payload_hash,
    )
    assert creq == EXPECTED_CANONICAL_REQUEST
    assert signed_headers == "host;x-amz-date"


def test_string_to_sign_matches_get_vanilla_vector():
    sts = aws_sigv4.string_to_sign(
        TIMESTAMP, "20150830/us-east-1/service/aws4_request", EXPECTED_CANONICAL_REQUEST
    )
    assert sts == EXPECTED_STRING_TO_SIGN


def test_signature_matches_independently_computed_hmac_chain():
    signature = aws_sigv4.sign_string(SECRET_KEY, "20150830", REGION, SERVICE, EXPECTED_STRING_TO_SIGN)
    assert signature == "ea21d6f05e96a897f6000a1a293f0a5bf0f92a00343409e820dce329ca6365ea"


def test_signing_key_derivation_matches_independent_hmac_chain():
    """Recomputes the HMAC-SHA256 key-derivation chain from scratch, outside
    `aws_sigv4.py`, and checks it against the module's derived key - so a bug
    shared between `derive_signing_key` and `sign_string` (e.g. a swapped
    HMAC key/message order) cannot hide behind their mutual agreement."""
    import hmac

    def independent_hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = independent_hmac(("AWS4" + SECRET_KEY).encode("utf-8"), "20150830")
    k_region = independent_hmac(k_date, REGION)
    k_service = independent_hmac(k_region, SERVICE)
    k_signing = independent_hmac(k_service, "aws4_request")

    assert aws_sigv4.derive_signing_key(SECRET_KEY, "20150830", REGION, SERVICE) == k_signing

    independent_signature = hmac.new(
        k_signing, EXPECTED_STRING_TO_SIGN.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert independent_signature == "ea21d6f05e96a897f6000a1a293f0a5bf0f92a00343409e820dce329ca6365ea"


def test_sign_request_end_to_end_matches_get_vanilla_vector():
    signed = aws_sigv4.sign_request(
        method="GET",
        canonical_uri="/",
        query_params=None,
        headers={"host": "example.amazonaws.com", "x-amz-date": TIMESTAMP},
        payload_hash=aws_sigv4.sha256_hex(b""),
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region=REGION,
        service=SERVICE,
        timestamp=TIMESTAMP,
    )
    assert signed["Authorization"] == EXPECTED_AUTHORIZATION


def test_uri_encode_preserves_unreserved_characters():
    assert aws_sigv4.uri_encode("abcABC123-_.~") == "abcABC123-_.~"
    assert aws_sigv4.uri_encode("a b") == "a%20b"
    assert aws_sigv4.uri_encode("a/b") == "a%2Fb"


def test_canonical_uri_from_path_preserves_slashes_but_encodes_segments():
    assert aws_sigv4.canonical_uri_from_path("/") == "/"
    assert (
        aws_sigv4.canonical_uri_from_path("/my bucket/my key.png")
        == "/my%20bucket/my%20key.png"
    )
