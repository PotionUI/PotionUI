import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CLIENT_HEADER = "x-potion-client"
_CLIENT_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")

WINDOW_LABEL = "another window"
API_LABEL = "API"


@dataclass(frozen=True)
class WriteOrigin:
    kind: str
    label: str
    client_id: Optional[str] = None

    def public(self) -> Dict[str, str]:
        return {"kind": self.kind, "label": self.label}

    def stored(self) -> Dict[str, Any]:
        return {**self.public(), "client_id": self.client_id}


def browser_or_api_origin(client_header: Optional[str]) -> WriteOrigin:
    client_id = (client_header or "").strip()
    if _CLIENT_ID.match(client_id):
        return WriteOrigin("window", WINDOW_LABEL, client_id)
    return WriteOrigin("api", API_LABEL)


def stored_origin(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"origin": None, "client_id": None}
    return {
        "origin": {"kind": raw.get("kind"), "label": raw.get("label")},
        "client_id": raw.get("client_id"),
    }


def announce(notifier: Optional[Any], user_id: str, session: Dict[str, Any], origin: WriteOrigin) -> None:
    if notifier is None:
        return
    try:
        notifier.schedule_send(
            user_id,
            {
                "type": "session_changed",
                "session_id": session["id"],
                "version": session["version"],
                "origin": origin.public(),
                "client_id": origin.client_id,
            },
        )
    except Exception:
        logger.warning("Could not push session %s change to its user", session.get("id"), exc_info=True)
