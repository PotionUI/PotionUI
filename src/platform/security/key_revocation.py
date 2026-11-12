import logging
from typing import Callable, List

_revoked: List[Callable[[str], None]] = []
_changed: List[Callable[[str], None]] = []


def subscribe(listener: Callable[[str], None]) -> None:
    if listener not in _revoked:
        _revoked.append(listener)


def subscribe_changes(listener: Callable[[str], None]) -> None:
    if listener not in _changed:
        _changed.append(listener)


def _fire(listeners: List[Callable[[str], None]], key_id: str) -> None:
    for listener in list(listeners):
        try:
            listener(key_id)
        except Exception:
            logging.exception("API key listener failed for key %s", key_id)


def notify(key_id: str) -> None:
    _fire(_revoked, key_id)


def notify_changed(key_id: str) -> None:
    _fire(_changed, key_id)
