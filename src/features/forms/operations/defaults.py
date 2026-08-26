"""Default values for a preset form (`GET /api/form/defaults/{preset_id}`)."""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_form_defaults(preset_id: str) -> Dict[str, Any]:
    """
    Get default values for a preset form.

    Args:
        preset_id: The preset identifier

    Returns:
        Dictionary of field names to default values
    """
    # This would extract default values from preset configuration
    # For now, return empty defaults
    logger.debug(f"Getting form defaults for preset: {preset_id}")
    defaults = {}

    return defaults
