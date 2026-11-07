import io
import shutil
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

MAX_ARCHIVE_ENTRIES = 10_000
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_NAME_LENGTH = 255
MAX_PATH_LENGTH = 1024

_WINDOWS_DEVICES = frozenset(
    ["con", "prn", "aux", "nul", "conin$", "conout$"]
    + [f"{name}{digit}" for name in ("com", "lpt") for digit in "0123456789¹²³"]
)
_ALLOWED_TYPES = frozenset({0, stat.S_IFREG, stat.S_IFDIR})


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class _Member:
    info: zipfile.ZipInfo
    parts: Tuple[str, ...]
    is_dir: bool


def _member_parts(name: str, plugin_id: str) -> Tuple[Tuple[str, ...], bool]:
    if len(name) > MAX_PATH_LENGTH:
        raise ArchiveError(f"The archive holds a path longer than {MAX_PATH_LENGTH} characters.")
    if "\\" in name or ":" in name or name.startswith("/"):
        raise ArchiveError(f"The archive path {name!r} is absolute or not a plain relative path.")
    is_dir = name.endswith("/")
    parts = tuple(name[:-1].split("/") if is_dir else name.split("/"))
    if parts[0] != plugin_id:
        raise ArchiveError(f"The archive path {name!r} is not inside the folder {plugin_id}/.")
    for part in parts:
        if part in ("", ".", ".."):
            raise ArchiveError(f"The archive path {name!r} has an empty, '.' or '..' part.")
        if len(part) > MAX_NAME_LENGTH or any(ord(char) < 32 for char in part):
            raise ArchiveError(f"The archive path {name!r} has a name Windows or Linux cannot hold.")
        if part != part.rstrip(" .") or part.split(".", 1)[0].lower() in _WINDOWS_DEVICES:
            raise ArchiveError(f"The archive path {name!r} has a name Windows cannot hold.")
    return parts, is_dir


def _checked_members(bundle: zipfile.ZipFile, plugin_id: str) -> List[_Member]:
    infos = bundle.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ArchiveError(f"The archive holds more than {MAX_ARCHIVE_ENTRIES} entries.")
    members: List[_Member] = []
    seen = set()
    declared = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ArchiveError(f"The archive entry {info.filename!r} is encrypted.")
        if stat.S_IFMT(info.external_attr >> 16) not in _ALLOWED_TYPES:
            raise ArchiveError(f"The archive entry {info.filename!r} is a link or a special file.")
        parts, is_dir = _member_parts(info.filename, plugin_id)
        key = "/".join(parts).casefold()
        if key in seen:
            raise ArchiveError(f"The archive holds {info.filename!r} more than once.")
        seen.add(key)
        declared += info.file_size
        if declared > MAX_UNPACKED_BYTES:
            raise ArchiveError(f"The archive unpacks to more than {MAX_UNPACKED_BYTES} bytes.")
        members.append(_Member(info, parts, is_dir))

    files = {"/".join(member.parts).casefold() for member in members if not member.is_dir}
    for member in members:
        for depth in range(1, len(member.parts)):
            if "/".join(member.parts[:depth]).casefold() in files:
                raise ArchiveError(f"The archive uses the file {'/'.join(member.parts[:depth])!r} as a folder.")
    if not any(member.parts == (plugin_id, "manifest.yml") and not member.is_dir for member in members):
        raise ArchiveError(f"The archive holds no {plugin_id}/manifest.yml.")
    return members


def unpack_plugin_archive(data: bytes, plugin_id: str, destination: Path) -> Path:
    try:
        bundle = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ArchiveError("The archive is not a zip file.") from error
    root = destination.resolve()
    with bundle:
        members = _checked_members(bundle, plugin_id)
        for member in members:
            target = root.joinpath(*member.parts)
            if not target.resolve().is_relative_to(root):
                raise ArchiveError(f"The archive path {member.info.filename!r} leaves the install folder.")
            if member.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with bundle.open(member.info) as source, open(target, "xb") as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
            except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError) as error:
                raise ArchiveError(f"The archive entry {member.info.filename!r} cannot be read: {error}") from error
    return root / plugin_id
