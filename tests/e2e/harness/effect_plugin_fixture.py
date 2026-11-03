from pathlib import Path
from typing import Dict, Iterable

from e2e_harness import REPO_ROOT

import cloud_fake

PLUGINS_ROOT = REPO_ROOT / "tests" / "e2e" / "effect_plugins"
PLUGIN_ID = "e2e-effects"
SPEC_PREFIXES = ("plugin-effect",)


def wants_effect_plugin(chunk_names: Iterable[str]) -> bool:
    return any(Path(name).name.startswith(SPEC_PREFIXES) for name in chunk_names)


def plugin_env() -> Dict[str, str]:
    return {cloud_fake.EXTRA_PLUGIN_DIRS_ENV: str(PLUGINS_ROOT)}
