import re
from pathlib import Path

DEFAULT_SHARED_PATH = Path("content/presets/_shared")

_CHILDREN_PATH_VAR_RE = re.compile(r"\{\{\s*paths\.(preset|_shared)\s*\}\}")


def resolve_children_path(children_ref: str, preset_root: Path, shared_path: Path) -> Path:
    roots = {"preset": str(preset_root), "_shared": str(shared_path)}
    return Path(_CHILDREN_PATH_VAR_RE.sub(lambda m: roots[m.group(1)], children_ref))
