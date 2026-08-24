"""AWS Signature Version 4 request signing.

A minimal, dependency-free implementation of the algorithm documented at
https://docs.aws.amazon.com/general/latest/gr/sigv4-signed-request-examples.html
so `S3FileStorageDriver` can talk to S3 (and S3-compatible services such as
MinIO or R2) over plain `httpx` without pulling in `boto3`/`aiobotocore`.

Each step of the algorithm is its own function so it can be exercised (and
tested) independently of an actual HTTP request.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Dict, Mapping, Optional, Tuple
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def uri_encode(value: str) -> str:
    """URI-encode one path segment or query key/value per SigV4 rules.

    Every byte is encoded except the unreserved set (`A-Za-z0-9-._~`), with
    uppercase hex digits - exactly what `urllib.parse.quote(value, safe="")`
    already produces, so this is a thin, self-documenting wrapper.
    """
    return quote(value, safe="")


def canonical_uri_from_path(path: str) -> str:
    """URI-encode a path for the canonical request, `/` preserved as a separator.

    Splitting on `/` and encoding each segment (rather than encoding the
    whole path with `/` marked safe) also encodes a literal `%` or space
    inside a segment, which `quote(path, safe="/")` would not catch reliably
    across segments.
    """
    if not path or path == "/":
        return "/"
    return "/".join(uri_encode(segment) for segment in path.split("/"))


def canonical_query_string(params: Mapping[str, str]) -> str:
    encoded = sorted((uri_encode(k), uri_encode(v)) for k, v in params.items())
    return "&".join(f"{k}={v}" for k, v in encoded)


def canonical_headers(headers: Mapping[str, str]) -> Tuple[str, str]:
    """Returns (canonical_headers_block, signed_headers)."""
    normalized = sorted((k.lower(), " ".join(v.split())) for k, v in headers.items())
    block = "".join(f"{k}:{v}\n" for k, v in normalized)
    signed = ";".join(k for k, _ in normalized)
    return block, signed


def canonical_request(
    method: str,
    canonical_uri: str,
    canonical_query: str,
    headers: Mapping[str, str],
    payload_hash: str,
) -> Tuple[str, str]:
    """Returns (canonical_request, signed_headers)."""
    header_block, signed_headers = canonical_headers(headers)
    request = "\n".join(
        [method, canonical_uri, canonical_query, header_block, signed_headers, payload_hash]
    )
    return request, signed_headers


def string_to_sign(timestamp: str, credential_scope: str, canonical_req: str) -> str:
    return "\n".join(
        [ALGORITHM, timestamp, credential_scope, sha256_hex(canonical_req.encode("utf-8"))]
    )


def derive_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    return _hmac_sha256(k_service, "aws4_request")


def sign_string(secret_key: str, date_stamp: str, region: str, service: str, sts: str) -> str:
    key = derive_signing_key(secret_key, date_stamp, region, service)
    return hmac.new(key, sts.encode("utf-8"), hashlib.sha256).hexdigest()


def authorization_header(
    access_key: str, credential_scope: str, signed_headers: str, signature: str
) -> str:
    return (
        f"{ALGORITHM} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def sign_request(
    *,
    method: str,
    canonical_uri: str,
    query_params: Optional[Mapping[str, str]],
    headers: Mapping[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    timestamp: str,
) -> Dict[str, str]:
    """Sign a request and return `headers` with `Authorization` added.

    `headers` must already carry every header that should be part of the
    signature (at minimum `host`, `x-amz-date`, and for S3
    `x-amz-content-sha256`) - this function does not invent headers, it only
    signs the ones it is given.
    """
    date_stamp = timestamp[:8]
    canonical_query = canonical_query_string(query_params or {})
    creq, signed_headers = canonical_request(
        method, canonical_uri, canonical_query, headers, payload_hash
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    sts = string_to_sign(timestamp, credential_scope, creq)
    signature = sign_string(secret_key, date_stamp, region, service, sts)

    signed = dict(headers)
    signed["Authorization"] = authorization_header(
        access_key, credential_scope, signed_headers, signature
    )
    return signed
