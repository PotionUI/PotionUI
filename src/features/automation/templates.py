"""Core catalog wiring for the immutable automation-template extension point.

`AutomationTemplate`/`AutomationTemplateRegistry` live in
`src.platform.plugins.automation_templates` (the platform extension point
`PluginRegistry` writes plugin contributions into); this module builds the
templates PotionUI itself ships (`content/automation/marketplace/`) and scans the
user's own (`content/automation/local/`, .gitignored).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.platform.plugins.automation_templates import (
    AutomationTemplateRegistrationError,
    AutomationTemplateRegistry,
)

logger = logging.getLogger(__name__)

MARKETPLACE_ROOT = Path("content/automation/marketplace")
LOCAL_ROOT = Path("content/automation/local")


def register_builtin_templates(
    registry: AutomationTemplateRegistry,
    marketplace_root: Path = MARKETPLACE_ROOT,
    local_root: Path = LOCAL_ROOT,
) -> None:
    """
    Register templates shipped with the core application, then scan the
    user's own local templates.

    Called unconditionally during `build_container()` startup, so one bad
    catalog file (missing, unreadable, malformed) must not abort the whole
    app: each entry is registered independently and a failure is logged and
    skipped rather than raised.
    """
    root = marketplace_root
    entries = [
        dict(
            source="core",
            source_name="PotionUI",
            template_id="index-new-model-files",
            title="Index new model files",
            description=(
                "Watch a model directory, index newly added weights, and notify administrators. "
                "Choose the directory and model type before enabling."
            ),
            category="models",
            icon="database",
            tags=["models", "filesystem", "indexing"],
            path=root / "index-new-model-files.json",
            root=root,
        ),
        dict(
            source="core",
            source_name="PotionUI",
            template_id="evict-ollama-before-generation",
            title="Free VRAM before a generation (automatic)",
            description=(
                "Before each generation, unload Ollama's models when the generation needs "
                "more VRAM than is free (or when the need can't be estimated) - and hold the "
                "generation until the eviction finishes. Swap the Expression node for a "
                "Compare node to use a fixed free-VRAM threshold instead. Requires the Ollama plugin."
            ),
            category="gpu",
            icon="trash-2",
            tags=["gpu", "vram", "ollama", "generation"],
            path=root / "evict-ollama-before-generation.json",
            root=root,
        ),
        dict(
            source="core",
            source_name="PotionUI",
            template_id="index-gallery-when-idle",
            title="Index gallery when idle",
            description=(
                "When the GPU is mostly free and no generation is running, catches up the gallery "
                "index (tags and smart search) in the background."
            ),
            category="media",
            icon="images",
            tags=["media", "gallery", "gpu"],
            path=root / "index-gallery-when-idle.json",
            root=root,
        ),
    ]

    for entry in entries:
        try:
            registry.register_from_file(**entry)
        except AutomationTemplateRegistrationError as exc:
            logger.error(
                "Skipping builtin automation template '%s': %s",
                entry.get("template_id"), exc,
            )
        except Exception:
            logger.error(
                "Skipping builtin automation template '%s' due to an unexpected error",
                entry.get("template_id"), exc_info=True,
            )

    register_local_templates(registry, local_root)


def register_local_templates(registry: AutomationTemplateRegistry, local_root: Path = LOCAL_ROOT) -> None:
    """
    Scan `content/automation/local/*.json` for user-authored automation templates.

    Unlike the curated core catalog above, a local template has no hand-written
    Python entry to source `title`/`description` from - those are derived from
    the envelope's own `automation.name`/`automation.description`, falling
    back to the filename when the envelope carries no name. A missing
    `local_root` is normal (no local templates yet) and not an error.
    """
    if not local_root.exists():
        return

    for path in sorted(local_root.glob("*.json")):
        template_id = path.stem
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Skipping local automation template '%s': %s", template_id, exc)
            continue

        automation = document.get("automation") if isinstance(document, dict) else None
        automation = automation if isinstance(automation, dict) else {}

        try:
            registry.register_from_file(
                source="local",
                source_name="Local",
                template_id=template_id,
                title=automation.get("name") or template_id,
                description=automation.get("description") or "",
                category="general",
                icon="bolt",
                tags=[],
                path=path,
                root=local_root,
            )
        except AutomationTemplateRegistrationError as exc:
            logger.error("Skipping local automation template '%s': %s", template_id, exc)
        except Exception:
            logger.error(
                "Skipping local automation template '%s' due to an unexpected error",
                template_id, exc_info=True,
            )
