"""Lifecycle handlers for the runpod-provider plugin.

`plugin.lifecycle.enable` fires only on the disabled->enabled transition;
`plugin.lifecycle.boot` fires on every start the plugin is enabled for, and
again right after a runtime enable.
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
