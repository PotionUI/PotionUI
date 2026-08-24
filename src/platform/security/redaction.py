"""Credential-shaped values, hidden on the way out.

Two places need this and neither has a declaration to lean on:

* core settings (`src/features/settings/`) have no `is_secret` column - unlike a
  plugin setting, whose manifest declares it - so `auth_secret_key` and the
  legacy `*_api_key` rows are indistinguishable from an ordinary string;
* a pipe's configuration comes from preset YAML, which may carry an `api_key`
  for a remote backend, and the whole dict is written to the debug log.

So the key name is the only signal available, and matching it is deliberately
conservative: a missed mask leaks a credential, but a false positive hides a
real value from the admin UI or the logs. Matching is therefore anchored at the
end of the key - `api_key`, `auth_secret_key` and `password` match, `max_tokens`
and `token_budget` do not.

The mask is the same `***` a plugin setting hands out, and it round-trips the
same way: a client that sends it back means "leave the stored value alone".
"""

from __future__ import annotations

from typing import Any

# What a read of a secret returns, and what a client sends back to mean
# "unchanged". Nobody's real credential is three asterisks.
SECRET_MASK = "***"

# Matched against the end of a lowercased key. Every entry is a whole trailing
# word, so `_` boundaries are part of the entry where one is needed.
_SECRET_SUFFIXES = (
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "password",
    "passwd",
    "passphrase",
    "private_key",
    "access_token",
    "refresh_token",
    "auth_token",
    "api_token",
    "bearer_token",
    "credential",
    "credentials",
    "client_secret",
)

# Keys that are exactly this are credentials even though no suffix rule fires.
_SECRET_EXACT = frozenset({"token", "key", "secret"})


def is_secret_key(key: str) -> bool:
    """Whether a key name looks like it names a credential."""
    if not isinstance(key, str):
        return False
    lowered = key.strip().lower()
    if lowered in _SECRET_EXACT:
        return True
    return any(lowered.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def mask_secret_value(key: str, value: Any) -> Any:
    """`value` masked if `key` names a credential and there is one to hide.

    An empty or absent value is passed through untouched: the admin UI tells
    "configured" from "not configured" by whether it got the mask or a blank,
    and masking a blank would claim a credential exists where none does.
    Non-string values are passed through too - a credential is a string, and a
    number or boolean whose key happens to match a suffix is not one.
    """
    if not is_secret_key(key):
        return value
    if not isinstance(value, str) or not value:
        return value
    return SECRET_MASK


def redact_mapping(value: Any) -> Any:
    """A copy of `value` with every credential-shaped entry masked, recursively.

    Built for log lines: dicts and lists are walked so a credential nested in a
    pipe's configuration is caught, and the input is never mutated.
    """
    if isinstance(value, dict):
        return {
            key: (
                SECRET_MASK
                if is_secret_key(key) and isinstance(inner, str) and inner
                else redact_mapping(inner)
            )
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        redacted = [redact_mapping(item) for item in value]
        return tuple(redacted) if isinstance(value, tuple) else redacted
    return value
