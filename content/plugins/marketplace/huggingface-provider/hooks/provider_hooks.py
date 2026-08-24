"""
Hook handler for registering the HuggingFace provider.

This module is called when the provider.register hook is executed,
allowing the HuggingFace provider to be registered with the marketplace system.
"""

import logging
from src.plugin_api import HookContext

logger = logging.getLogger(__name__)


def register_provider(context: HookContext) -> HookContext:
    """
    Register the HuggingFace provider class with the marketplace system.

    This hook handler is called during provider discovery. It adds the
    HuggingFaceProvider class to the providers dictionary in the context.

    Args:
        context: Hook context with 'providers' dict in data

    Returns:
        Modified context with HuggingFaceProvider registered
    """
    try:
        # Import the provider class
        from ..provider.huggingface_provider import HuggingFaceProvider

        # Get or create the providers dict
        providers = context.data.get('providers', {})

        # Register our provider
        providers['huggingface'] = HuggingFaceProvider

        # Update context
        context.data['providers'] = providers

        logger.info("HuggingFace provider registered successfully")

    except ImportError as e:
        logger.error(f"Failed to import HuggingFaceProvider: {e}")
    except Exception as e:
        logger.error(f"Failed to register HuggingFace provider: {e}")

    return context
