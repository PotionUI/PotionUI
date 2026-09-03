"""
Hook handler for registering the ComfyUI backend.

This module is called when the backend.register hook is executed,
allowing the ComfyUI backend to be registered with the backend system.
"""

import logging
from src.plugin_api import HookContext

logger = logging.getLogger(__name__)


def register_backend(context: HookContext) -> HookContext:
    """
    Register the ComfyUI backend class with the backend system.

    This hook handler is called during backend discovery. It adds the
    ComfyUIBackend class and ComfyUIBackendConfig class to the context.

    Args:
        context: Hook context with 'backend_types' and 'config_types' dicts in data

    Returns:
        Modified context with ComfyUIBackend registered
    """
    try:
        # Import the backend and config classes
        from ..backend.comfyui_backend import ComfyUIBackend
        from ..backend.comfyui_config import ComfyUIBackendConfig

        # Get or create the type dicts
        backend_types = context.data.get('backend_types', {})
        config_types = context.data.get('config_types', {})

        # Register our backend type
        backend_types['comfyui'] = ComfyUIBackend
        config_types['comfyui'] = ComfyUIBackendConfig

        # Update context
        context.data['backend_types'] = backend_types
        context.data['config_types'] = config_types

        logger.info("ComfyUI backend registered successfully")

    except ImportError as e:
        logger.error(f"Failed to import ComfyUI backend: {e}")
    except Exception as e:
        logger.error(f"Failed to register ComfyUI backend: {e}")

    return context
