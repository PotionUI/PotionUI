"""
Response mappers for the sessions feature.

Plain functions that turn Session/SessionVersion records into their API
response dicts. No class, no state.
"""
from typing import Any, Dict

from src.features.sessions.dto import Session, SessionVersion


def session_to_response_dict(session: Session) -> Dict[str, Any]:
    """
    Convert a Session to a response dictionary (excludes user_id for security).

    Args:
        session: Session DTO

    Returns:
        Dictionary with session data (without user_id)
    """
    return {
        'id': session.id,
        'preset_id': session.preset_id,
        'name': session.name,
        'data': session.data,
        'created_at': session.created_at.isoformat() if session.created_at else None,
        'updated_at': session.updated_at.isoformat() if session.updated_at else None
    }


def session_version_summary_to_dict(version: SessionVersion) -> Dict[str, Any]:
    """
    Convert a SessionVersion to a summary dictionary (no payload).

    Args:
        version: SessionVersion DTO

    Returns:
        {version_number, created_at, summary} dict
    """
    return {
        "version_number": version.version_number,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "summary": version.summary,
    }


def session_version_to_dict(version: SessionVersion) -> Dict[str, Any]:
    """
    Convert a SessionVersion to its full response dictionary (with payload).

    Args:
        version: SessionVersion DTO

    Returns:
        {version_number, created_at, summary, data} dict
    """
    return {
        "version_number": version.version_number,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "summary": version.summary,
        "data": version.data,
    }
