"""
Utility functions for download operations.

Provides helper functions for filename extraction and URL processing.
"""

import os
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote


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
    """Extract filename from URL, including from response-content-disposition query param.

    Handles:
    - response-content-disposition query parameter (used by CDNs like Cloudflare)
    - Path basename

    Args:
        url: The URL to extract filename from

    Returns:
        Extracted filename or None if not found
    """
    parsed = urlparse(url)

    # Check for response-content-disposition query parameter (used by CDNs like Cloudflare)
    query_params = parse_qs(parsed.query)
    if 'response-content-disposition' in query_params:
        disposition = unquote(query_params['response-content-disposition'][0])
        filename = extract_filename_from_content_disposition(disposition)
        if filename:
            return filename

    # Fall back to path basename
    path_filename = unquote(os.path.basename(parsed.path))
    if path_filename and '.' in path_filename:
        return path_filename

    return None
