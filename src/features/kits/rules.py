import re
from typing import Callable, Iterable, Optional

from src.platform.http.errors import ConflictError, InvalidInputError

KIND_IMAGE = "image"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_TEXT = "text"
MEDIA_KINDS = (KIND_IMAGE, KIND_VIDEO, KIND_AUDIO)
ITEM_KINDS = MEDIA_KINDS + (KIND_TEXT,)

MAX_ITEMS_PER_KIT = 50
MAX_TEXT_CHARS = 4000
MAX_NAME_CHARS = 120
MAX_LABEL_CHARS = 40
MAX_PATH_NAME_CHARS = 32

PATH_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_INVALID_RUN = re.compile(r"[^a-z0-9_]+")

KIT_ID_PREFIX = "kt_"
ITEM_ID_PREFIX = "it_"


def normalize_path_name(raw: str) -> str:
    return re.sub(r"\s+", "_", (raw or "").strip()).lower()


def validate_path_name(raw: str, what: str) -> str:
    name = normalize_path_name(raw)
    if not PATH_NAME_RE.match(name):
        raise InvalidInputError(
            f"invalid_{what}_path",
            f"A {what} path name uses lowercase letters, digits and _, starts with a letter and is 1 to "
            f"{MAX_PATH_NAME_CHARS} characters",
        )
    return name


def derive_path_name(display: str, fallback: str) -> str:
    name = re.sub(r"_+", "_", _INVALID_RUN.sub("_", normalize_path_name(display))).strip("_")
    if not name or not name[0].isalpha():
        name = f"{fallback}_{name}".strip("_") if name else fallback
    name = name[:MAX_PATH_NAME_CHARS].rstrip("_")
    return name or fallback


def free_path_name(base: str, taken: Callable[[str], bool]) -> str:
    if not taken(base):
        return base
    number = 2
    while True:
        suffix = str(number)
        candidate = f"{base[:MAX_PATH_NAME_CHARS - len(suffix)]}{suffix}"
        if not taken(candidate):
            return candidate
        number += 1


def clash(what: str, name: str, taken: Callable[[str], bool]) -> ConflictError:
    suggestion = free_path_name(name, taken)
    return ConflictError(
        f"{what}_path_taken",
        f"'{name}' is already used, try '{suggestion}'",
        suggestion=suggestion,
    )


def default_item_name(kind: str, names: Iterable[str]) -> str:
    used = set(names)
    number = 1
    while f"{kind}{number}" in used:
        number += 1
    return f"{kind}{number}"


def clean_label(raw: Optional[str]) -> Optional[str]:
    label = " ".join((raw or "").split())
    if not label:
        return None
    return label[:MAX_LABEL_CHARS]


def clean_display_name(raw: Optional[str]) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise InvalidInputError("kit_name_required", "A kit needs a name")
    return name[:MAX_NAME_CHARS]
