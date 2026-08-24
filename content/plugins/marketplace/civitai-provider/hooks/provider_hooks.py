"""
Hook handler for registering the CivitAI provider.

This module is called when the provider.register hook is executed,
allowing the CivitAI provider to be registered with the marketplace system.
"""

import logging
from src.plugin_api import HookContext

logger = logging.getLogger(__name__)


def register_provider(context: HookContext) -> HookContext:
    """
    Register the CivitAI provider class with the marketplace system.

    This hook handler is called during provider discovery. It adds the
    CivitaiProvider class to the providers dictionary in the context.

    Args:
        context: Hook context with 'providers' dict in data

    Returns:
        Modified context with CivitaiProvider registered
    """
    try:
        # Import the provider class
        from ..provider.civitai_provider import CivitaiProvider

        # Get or create the providers dict
        providers = context.data.get('providers', {})

        # Register our provider
        providers['civitai'] = CivitaiProvider

        # Update context
        context.data['providers'] = providers

        logger.info("CivitAI provider registered successfully")

    except ImportError as e:
        logger.error(f"Failed to import CivitaiProvider: {e}")
    except Exception as e:
        logger.error(f"Failed to register CivitAI provider: {e}")

    return context
