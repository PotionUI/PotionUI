import hashlib
import secrets
import string
import zlib

PREFIX = "pui_"
BODY_LENGTH = 40
CHECK_LENGTH = 6
DISPLAY_PREFIX_LENGTH = 12
LAST_FOUR_LENGTH = 4
MAX_PRESENTED_LENGTH = 256

_ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase


def _base62(value: int, width: int) -> str:
    digits = []
    for _ in range(width):
        value, remainder = divmod(value, len(_ALPHABET))
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))


def checksum(body: str) -> str:
    return _base62(zlib.crc32(body.encode("ascii")), CHECK_LENGTH)


def generate_secret() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(BODY_LENGTH))
    return PREFIX + body + checksum(body)


def has_valid_checksum(candidate: str) -> bool:
    if not candidate.startswith(PREFIX) or len(candidate) != len(PREFIX) + BODY_LENGTH + CHECK_LENGTH:
        return False
    body = candidate[len(PREFIX):len(PREFIX) + BODY_LENGTH]
    if not candidate.isascii() or any(char not in _ALPHABET for char in body):
        return False
    return secrets.compare_digest(checksum(body), candidate[len(PREFIX) + BODY_LENGTH:])


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def display_prefix(secret: str) -> str:
    return secret[:DISPLAY_PREFIX_LENGTH]


def last_four(secret: str) -> str:
    return secret[-LAST_FOUR_LENGTH:]
