"""S3 (and S3-compatible: MinIO, Cloudflare R2, ...) storage driver.

Talks to the S3 REST API directly over `httpx`, signing every request with
hand-rolled AWS SigV4 (`aws_sigv4.py`) rather than pulling in `boto3` /
`aiobotocore`, which are not in `requirements.txt`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

import httpx

from src.platform.filesystem import aws_sigv4
from src.platform.filesystem.storage_driver import (
    DEFAULT_CHUNK_SIZE,
    FileStorageDriver,
    validate_key,
)

# S3's documented sentinel meaning "don't hash the body, trust Content-Length
# instead" - used for streamed uploads where hashing would mean reading the
# whole file twice.
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"

_EMPTY_PAYLOAD_HASH = aws_sigv4.sha256_hex(b"")


class S3FileStorageDriver(FileStorageDriver):
    """Object storage over the S3 REST API.

    `prefix`, when set, is prepended to every key, so a single bucket can be
    shared between environments or apps without key collisions.
    `endpoint_url` + `path_style` make this MinIO/R2-compatible: point
    `endpoint_url` at the compatible service and set `path_style=True` (most
    non-AWS S3-compatible services require path-style addressing).
    """

    def __init__(
        self,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        prefix: str = "",
        path_style: bool = False,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
    ):
        if not bucket:
            raise ValueError("S3FileStorageDriver requires a bucket name.")
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region or "us-east-1"
        self.prefix = prefix.strip("/")
        self.path_style = path_style or bool(endpoint_url)

        if endpoint_url:
            parsed = urlparse(endpoint_url)
            self.scheme = parsed.scheme or "https"
            self.host = parsed.netloc
        else:
            self.scheme = "https"
            self.host = (
                f"s3.{self.region}.amazonaws.com"
                if self.path_style
                else f"{self.bucket}.s3.{self.region}.amazonaws.com"
            )

        self._client = client or httpx.Client(timeout=timeout)

    # ---- key/URL plumbing ----

    def _object_key(self, key: str) -> str:
        normalized = validate_key(key)
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def _canonical_uri(self, object_key: str) -> str:
        path = f"/{self.bucket}/{object_key}" if self.path_style else f"/{object_key}"
        return aws_sigv4.canonical_uri_from_path(path)

    def _url(self, object_key: str) -> str:
        canonical_uri = self._canonical_uri(object_key)
        return f"{self.scheme}://{self.host}{canonical_uri}"

    def _timestamp(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _signed_headers(
        self, method: str, object_key: str, payload_hash: str, extra: Optional[dict] = None
    ) -> dict:
        timestamp = self._timestamp()
        headers = {
            "host": self.host,
            "x-amz-date": timestamp,
            "x-amz-content-sha256": payload_hash,
        }
        if extra:
            headers.update(extra)
        return aws_sigv4.sign_request(
            method=method,
            canonical_uri=self._canonical_uri(object_key),
            query_params=None,
            headers=headers,
            payload_hash=payload_hash,
            access_key=self.access_key_id,
            secret_key=self.secret_access_key,
            region=self.region,
            service="s3",
            timestamp=timestamp,
        )

    # ---- FileStorageDriver ----

    def put_bytes(self, key: str, data: bytes) -> int:
        object_key = self._object_key(key)
        payload_hash = aws_sigv4.sha256_hex(data)
        headers = self._signed_headers("PUT", object_key, payload_hash)
        response = self._client.put(self._url(object_key), content=data, headers=headers)
        response.raise_for_status()
        return len(data)

    def put_file(self, key: str, source_path: Path) -> int:
        object_key = self._object_key(key)
        size = source_path.stat().st_size
        headers = self._signed_headers(
            "PUT", object_key, UNSIGNED_PAYLOAD, extra={"content-length": str(size)}
        )
        with open(source_path, "rb") as f:
            response = self._client.put(self._url(object_key), content=f, headers=headers)
        response.raise_for_status()
        return size

    def get_bytes(self, key: str) -> bytes:
        object_key = self._object_key(key)
        headers = self._signed_headers("GET", object_key, _EMPTY_PAYLOAD_HASH)
        response = self._client.get(self._url(object_key), headers=headers)
        if response.status_code == 404:
            raise FileNotFoundError(f"No such storage key: {key!r}")
        response.raise_for_status()
        return response.content

    def get_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        object_key = self._object_key(key)
        headers = self._signed_headers("GET", object_key, _EMPTY_PAYLOAD_HASH)
        url = self._url(object_key)
        client = self._client

        # Existence must be resolved before this becomes a generator - a
        # generator's body does not run until first `next()`, so a bare
        # `def` here would swallow a 404 until the caller starts iterating.
        with client.stream("GET", url, headers=headers) as probe:
            if probe.status_code == 404:
                raise FileNotFoundError(f"No such storage key: {key!r}")
            probe.raise_for_status()

        def _iter() -> Iterator[bytes]:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size):
                    yield chunk

        return _iter()

    def delete(self, key: str) -> bool:
        object_key = self._object_key(key)
        if not self.exists(key):
            return False
        headers = self._signed_headers("DELETE", object_key, _EMPTY_PAYLOAD_HASH)
        response = self._client.delete(self._url(object_key), headers=headers)
        response.raise_for_status()
        return True

    def exists(self, key: str) -> bool:
        return self.size(key) is not None

    def size(self, key: str) -> Optional[int]:
        object_key = self._object_key(key)
        headers = self._signed_headers("HEAD", object_key, _EMPTY_PAYLOAD_HASH)
        response = self._client.head(self._url(object_key), headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        return int(content_length) if content_length is not None else None

    # ---- optional: presigned URLs (not wired to any route in this pass) ----

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """A time-limited, unsigned-by-the-server GET URL for `key`.

        Query-string SigV4 presigning, per AWS's "authentication information
        in the query string" variant. Not used by any route yet - servers
        proxy through the authenticated media routes instead - but the
        primitive is here for a future public-redirect optimization.
        """
        object_key = self._object_key(key)
        timestamp = self._timestamp()
        date_stamp = timestamp[:8]
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"

        query_params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key_id}/{credential_scope}",
            "X-Amz-Date": timestamp,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_uri = self._canonical_uri(object_key)
        canonical_query = aws_sigv4.canonical_query_string(query_params)
        creq, signed_headers = aws_sigv4.canonical_request(
            "GET", canonical_uri, canonical_query, {"host": self.host}, UNSIGNED_PAYLOAD
        )
        sts = aws_sigv4.string_to_sign(timestamp, credential_scope, creq)
        signature = aws_sigv4.sign_string(
            self.secret_access_key, date_stamp, self.region, "s3", sts
        )
        return f"{self.scheme}://{self.host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"
