import logging
from typing import Callable, List

_listeners: List[Callable[[str], None]] = []


def subscribe(listener: Callable[[str], None]) -> None:
    if listener not in _listeners:
        _listeners.append(listener)


def notify(key_id: str) -> None:
    for listener in list(_listeners):
        try:
            listener(key_id)
        except Exception:
            logging.exception("API key revocation listener failed for key %s", key_id)
