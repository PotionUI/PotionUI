from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

import yaml

HARNESS_DIR = Path(__file__).resolve().parent
JOURNEYS_DIR = HARNESS_DIR.parent / "journeys"
for path in (HARNESS_DIR, JOURNEYS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fake_huggingface import FakeHuggingFace
from fresh_install_guided_setup import RECIPE_ID, SOURCE_RECIPE, build_recipe, resolve_path

SPEC_NAMES = {"fresh-install-guided-setup"}


def wants_fresh_install(chunk_names) -> bool:
    return any(name in SPEC_NAMES for name in chunk_names)


class FreshInstall:
    def __init__(self) -> None:
        payload = b"fresh-install-vae-" + os.urandom(64)
        recipe = build_recipe(payload)
        self.server = FakeHuggingFace({resolve_path(recipe["artifacts"][0]): payload})
        self._scratch = tempfile.TemporaryDirectory(prefix="potionui-fresh-install-")
        scratch = Path(self._scratch.name)
        self.recipes_dir = scratch / "recipes"
        self.recipes_dir.mkdir()
        self.shipped_starters = []
        for shipped in sorted(SOURCE_RECIPE.parent.glob("*.yml")):
            shutil.copyfile(shipped, self.recipes_dir / shipped.name)
            data = yaml.safe_load(shipped.read_text(encoding="utf-8"))
            if data.get("first_run"):
                self.shipped_starters.append(data["name"])
        (self.recipes_dir / f"{RECIPE_ID}.yml").write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")
        self.models_dir = scratch / "no-models"
        self.recipe_name = recipe["name"]

    def env(self) -> Dict[str, str]:
        return {"HF_ENDPOINT": self.server.url}

    def spec_env(self) -> Dict[str, str]:
        return {
            "E2E_FRESH_RECIPE_NAME": self.recipe_name,
            "E2E_FRESH_SHIPPED_STARTERS": json.dumps(self.shipped_starters),
        }

    def stop(self) -> None:
        self.server.stop()
        self._scratch.cleanup()
