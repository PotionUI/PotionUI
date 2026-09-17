import base64
import json
import secrets
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.constants import ALGORITHMS
from jose.jwt import calculate_at_hash as _jose_calculate_at_hash


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_rsa_keypair(kid: str = "test-kid"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }
    return private_pem, public_pem, jwk


def sign_id_token(claims: Dict[str, Any], private_pem: str, kid: str, algorithm: str = "RS256") -> str:
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers={"kid": kid})


def calculate_at_hash(access_token: str, algorithm: str = "RS256") -> str:
    return _jose_calculate_at_hash(access_token, ALGORITHMS.HASHES[algorithm])


def unsigned_component(value: Dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode("ascii")


def default_claims(issuer: str, client_id: str, sub: str, nonce: str, **extra: Any) -> Dict[str, Any]:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": client_id,
        "sub": sub,
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(extra)
    return claims


class FakeIdP:
    def __init__(self, issuer: str = "https://idp.example"):
        self.issuer = issuer.rstrip("/")
        self.private_pem, self.public_pem, self.jwk = generate_rsa_keypair()
        self.keys: List[Dict[str, Any]] = [self.jwk]
        self.jwks_call_count = 0
        self.jwks_response_after_first_call: Optional[List[Dict[str, Any]]] = None
        self.cache_control: Optional[str] = None
        self.discovery_issuer_override: Optional[str] = None
        self.token_endpoint_auth_methods_supported: List[str] = ["client_secret_post"]
        self.token_response_override: Optional[Dict[str, Any]] = None
        self.next_id_token: Optional[str] = None
        self.pending_sub: Optional[str] = None
        self.pending_nonce: Optional[str] = None
        self.pending_extra: Dict[str, Any] = {}
        self.include_at_hash = True
        self.at_hash_override: Optional[str] = None
        self.access_token_override: Optional[str] = None
        self.userinfo_response: Optional[Dict[str, Any]] = None
        self.userinfo_status_code = 200
        self.token_requests: List[Dict[str, str]] = []
        self.discovery_requests = 0
        self.jwks_requests = 0
        self.userinfo_requests = 0
        self._last_issued_sub: Optional[str] = None

    def queue_id_token(self, sub: str, nonce: str, **extra: Any) -> None:
        self.pending_sub = sub
        self.pending_nonce = nonce
        self.pending_extra = extra
        self.next_id_token = None

    def discovery_document(self) -> Dict[str, Any]:
        return {
            "issuer": self.discovery_issuer_override or self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "userinfo_endpoint": f"{self.issuer}/userinfo",
            "token_endpoint_auth_methods_supported": self.token_endpoint_auth_methods_supported,
        }

    def issue_id_token(self, sub: str, nonce: str, algorithm: str = "RS256", **extra: Any) -> str:
        self._last_issued_sub = sub
        claims = default_claims(self.issuer, "test-client", sub, nonce, **extra)
        return sign_id_token(claims, self.private_pem, self.jwk["kid"], algorithm=algorithm)

    def _response_headers(self) -> Dict[str, str]:
        headers = {}
        if self.cache_control is not None:
            headers["cache-control"] = self.cache_control
        return headers

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            self.discovery_requests += 1
            return httpx.Response(200, json=self.discovery_document(), headers=self._response_headers())
        if path.endswith("/jwks"):
            self.jwks_requests += 1
            self.jwks_call_count += 1
            if self.jwks_call_count >= 2 and self.jwks_response_after_first_call is not None:
                keys = self.jwks_response_after_first_call
            else:
                keys = self.keys
            return httpx.Response(200, json={"keys": keys}, headers=self._response_headers())
        if path.endswith("/token"):
            body = dict(parse_qsl(request.content.decode()))
            self.token_requests.append(body)
            if self.token_response_override is not None:
                return httpx.Response(200, json=self.token_response_override)

            access_token = self.access_token_override or f"at-{secrets.token_hex(8)}"
            if self.next_id_token is not None:
                id_token = self.next_id_token
            elif self.pending_sub is not None:
                claims = default_claims(
                    self.issuer, "test-client", self.pending_sub, self.pending_nonce, **self.pending_extra
                )
                if self.include_at_hash and "at_hash" not in claims:
                    claims["at_hash"] = self.at_hash_override or calculate_at_hash(access_token)
                self._last_issued_sub = self.pending_sub
                id_token = sign_id_token(claims, self.private_pem, self.jwk["kid"])
            else:
                id_token = None

            payload = {
                "access_token": access_token,
                "token_type": "Bearer",
                "id_token": id_token,
            }
            return httpx.Response(200, json=payload)
        if path.endswith("/userinfo"):
            self.userinfo_requests += 1
            body = self.userinfo_response
            if body is None:
                body = {"sub": self._last_issued_sub} if self._last_issued_sub else {}
            return httpx.Response(self.userinfo_status_code, json=body)
        return httpx.Response(404, json={"error": "not_found"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
