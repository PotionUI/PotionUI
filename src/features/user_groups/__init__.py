"""
User Group module.

Handles user group management including CRUD operations for groups,
member management, and resource assignments (presets, LLMs, models).
"""

from .manager import UserGroupManager

__all__ = ["UserGroupManager"]
