"""Lifecycle handlers for the runpod-provider plugin.

This plugin registers no marketplace provider (`provider.register` is for
model marketplaces - `ProviderCapability.HASH_LOOKUP`/`SEARCH`/... none of
which describe provisioning compute) and no engine (`backend.register` is
for a pipeline *protocol* - `docs/backends.md` is explicit that a hosting
provider like RunPod is neither). What it actually is - a plugin with its
own settings, its own admin-only API routes, and its own DB table - is
exactly what `plugin.lifecycle.boot`/`enable`/`disable` are for; every other
plugin shaped this way (spritesheet, video-editor, form-builder) uses the
same three hooks for the same reason.

`plugin.lifecycle.enable` fires only on the disabled->enabled transition, so
anything that must be true on every process start belongs in
`plugin.lifecycle.boot` instead - that one fires for every enabled plugin at
startup, and again right after a runtime enable.
"""

import logging

logger = logging.getLogger(__name__)


def on_boot(context):
    """Create the resource-tracking table - runs on every start this plugin is enabled for."""
    from ..resources import RunPodResourceManager

    RunPodResourceManager().create_table()
    logger.info("[RUNPOD-PROVIDER] Resource table ensured")
    return context


def on_enable(context):
    """No-op beyond the audit line - the table is `on_boot`'s job."""
    logger.info("[RUNPOD-PROVIDER] Plugin enabled by an admin")
    return context


def on_disable(context):
    """No-op - managed pods/volumes and their resource records are left
    alone. Disabling the plugin must not orphan a running pod an admin is
    still paying for; deprovisioning is always an explicit API call."""
    logger.info("[RUNPOD-PROVIDER] Plugin disabled - managed resources preserved")
    return context
