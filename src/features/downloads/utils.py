"""Filename handling for download operations.

Filename policy
---------------
Every name that ends up in a download's `destination_path` passes through
`safe_download_name`: an explicit override from the API or a `before_queue`
hook, a name derived from the URL path or a `Content-Disposition`, a
repo-relative path in a grouped Hugging Face job, and a provider's mid-flight
rewrite. The rule is the same for all of them - the name must be a *relative*
path made only of ordinary segments. Rejected: an absolute path, a drive or
UNC root, an empty/`.`/`..` segment, a POSIX or Windows separator (checked
both as given and after a single percent-decode, so `%2f`/`%5c`/`%2e%2e`
cannot smuggle one past), a control character, a segment with a trailing dot
or space, and a Windows reserved device name. `single_segment=True` (the
default) additionally forbids crossing a directory at all; only a grouped
repo's per-file paths and an explicit caller-supplied name are allowed a
subpath, and a subpath can still only descend inside the already-contained
destination directory.

An EXPLICIT name that fails is a hard error (`UnsafeFilenameException`, a
`DownloadQueueException` the routes answer 400 for) - silently renaming what a
caller asked for would hide the attempt. A DERIVED name that fails is simply
not used: `derived_download_name` returns None and its caller falls back to a
generated name.

`verify_file_target` is the write-time half of the policy. The file and its
`.part` sibling must be direct children of the approved destination directory
and must not be symlinks resolving outside it. The approved directory is the
destination this process already contained against the configured depot, not a
re-resolved depot root, so an admin's symlinked type directory
(`models/diffusion_models -> /mnt/ssd2/models/diffusion_models`) stays valid.
The guarantee is lexical containment plus a symlink check taken at open and
rename time. It is NOT atomic: a symlink planted between the check and the
write is not defeated by it.
"""

import logging
import os
import posixpath
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

from src.features.downloads.exceptions import UnsafeFilenameException

logger = logging.getLogger(__name__)

_SEPARATORS = re.compile(r"[/\\]")

_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _reject(name: str, reason: str) -> None:
    raise UnsafeFilenameException(f"Unsafe download filename '{name}': {reason}")


def _check_shape(name: str, candidate: str, single_segment: bool) -> None:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in candidate):
        _reject(name, "contains a control character")
    if candidate.startswith(("/", "\\")):
        _reject(name, "is an absolute path")
    if len(candidate) >= 2 and candidate[1] == ":":
        _reject(name, "is drive-qualified")

    segments = _SEPARATORS.split(candidate)
    if single_segment and len(segments) > 1:
        _reject(name, "crosses a directory")

    for segment in segments:
        if segment == "":
            _reject(name, "has an empty path segment")
        if segment in (".", ".."):
            _reject(name, f"has a '{segment}' path segment")
        if segment != segment.rstrip(" ."):
            _reject(name, "has a segment ending in a dot or space")
        if segment.split(".")[0].upper() in _WINDOWS_DEVICE_NAMES:
            _reject(name, "uses a reserved device name")


def safe_download_name(name: str, *, single_segment: bool = True) -> str:
    """`name` as given once it is proven to stay inside its destination
    directory, else `UnsafeFilenameException`. See the module docstring.

    The name is validated, never rewritten: percent-decoding is a detection
    step only, so a legitimate `my%20model.safetensors` on disk keeps the
    spelling the caller asked for.
    """
    if not name:
        _reject(str(name), "is empty")

    _check_shape(name, name, single_segment)
    decoded = unquote(name)
    if decoded != name:
        _check_shape(name, decoded, single_segment)
    return name


def derived_download_name(name: Optional[str], *, single_segment: bool = True) -> Optional[str]:
    """`safe_download_name` for a name nobody asked for by hand (a URL path, a
    `Content-Disposition`, a provider's rewrite): an unsafe one is dropped so
    the caller can fall back, not raised."""
    if not name:
        return None
    try:
        return safe_download_name(name, single_segment=single_segment)
    except UnsafeFilenameException as e:
        logger.warning(f"Ignoring derived download filename: {e}")
        return None


def verify_file_target(path, approved_dir) -> None:
    """`path` is writable as a download target inside `approved_dir`, else
    `UnsafeFilenameException`.

    `approved_dir` is the destination already contained against the configured
    depot - it may itself be a symlink into shared storage, which is why both
    sides are compared after `os.path.realpath` rather than the candidate
    being resolved against a lexical depot root. See the module docstring for
    what this does and does not guarantee.
    """
    path_str = str(path)
    safe_download_name(os.path.basename(path_str))

    approved_real = os.path.realpath(str(approved_dir))
    parent_real = os.path.realpath(os.path.dirname(os.path.abspath(path_str)))
    if parent_real != approved_real:
        raise UnsafeFilenameException(
            f"Download target '{path_str}' is not inside '{approved_dir}'"
        )

    target_real = os.path.realpath(path_str)
    if os.path.dirname(target_real) != approved_real:
        raise UnsafeFilenameException(
            f"Download target '{path_str}' is a symlink escaping '{approved_dir}'"
        )


def extract_filename_from_content_disposition(header: str) -> Optional[str]:
    """Extract filename from Content-Disposition header.

    Handles various formats:
    - filename*=UTF-8''name.ext (RFC 5987 encoding)
    - filename="name.ext" (quoted)
    - filename=name.ext (unquoted)

    Args:
        header: Content-Disposition header value

    Returns:
        Extracted filename or None if not found
    """
    if not header:
        return None

    # Try to find filename*= (RFC 5987 encoding) first
    match = re.search(r"filename\*=(?:UTF-8''|utf-8'')([^;]+)", header, re.IGNORECASE)
    if match:
        return unquote(match.group(1).strip())

    # Try to find filename= with quotes
    match = re.search(r'filename="([^"]+)"', header)
    if match:
        return match.group(1).strip()

    # Try to find filename= without quotes
    match = re.search(r'filename=([^;\s]+)', header)
    if match:
        return match.group(1).strip()

    return None


def extract_filename_from_url(url: str) -> Optional[str]:
    """A single safe path segment named by `url`, or None.

    Handles:
    - response-content-disposition query parameter (used by CDNs like Cloudflare)
    - Path basename

    `unquote` must run BEFORE `posixpath.basename` (a URL path is POSIX on
    every host): an encoded segment like
    `..%2F..%2Fetc%2Fcron` carries no raw slash, so basename on the still
    encoded path leaves it whole and decoding afterwards hands back
    `../../etc/cron`. Decoding first makes basename split on the real
    separators. `DownloadQueue._filename_from_url` derives its name the same
    way, and the two must not diverge.

    Args:
        url: The URL to extract filename from

    Returns:
        A filename that passes the module's policy, or None if the URL names
        none or names an unsafe one
    """
    parsed = urlparse(url)

    # Check for response-content-disposition query parameter (used by CDNs like Cloudflare)
    query_params = parse_qs(parsed.query)
    if 'response-content-disposition' in query_params:
        disposition = unquote(query_params['response-content-disposition'][0])
        filename = derived_download_name(
            extract_filename_from_content_disposition(disposition)
        )
        if filename:
            return filename

    # Fall back to path basename
    path_filename = posixpath.basename(unquote(parsed.path))
    if path_filename and '.' in path_filename:
        return derived_download_name(path_filename)

    return None
