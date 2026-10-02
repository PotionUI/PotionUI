import re

RESOURCE_MARKER_RE = re.compile(r"@\[([A-Za-z_][A-Za-z0-9_-]*):([^\]\r\n]+)\]")
