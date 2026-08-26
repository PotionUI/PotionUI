"""YAML frontmatter parsing shared by the doc-tree aggregation
(`src.features.docs.operations`) and the typed-doc linter (`src.features.docs.lint`)."""
import re
from typing import Any, Dict, Tuple

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split optional `---\\n...\\n---` YAML frontmatter off the top of `text`."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    return data, text[match.end():]
