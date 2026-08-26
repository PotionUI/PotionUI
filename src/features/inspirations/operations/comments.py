"""Comment operations on a published inspiration."""
from src.features.inspirations.collaborators import InspirationCollaborators
from src.features.inspirations.records import InspirationComment

MAX_COMMENT_LENGTH = 2000


def add_comment(collaborators: InspirationCollaborators, inspiration_id: str, user_id: str, body: str) -> InspirationComment:
    """Raises ValueError if the inspiration is not found or the body is empty/too long."""
    insp = collaborators.repository.get_by_id(inspiration_id)
    if not insp:
        raise ValueError("Inspiration not found")

    body = (body or "").strip()
    if not body:
        raise ValueError("Comment body is required")
    if len(body) > MAX_COMMENT_LENGTH:
        raise ValueError(f"Comment must be {MAX_COMMENT_LENGTH} characters or fewer")

    comment = collaborators.repository.create_comment(inspiration_id, user_id, body)

    if insp.user_id != user_id:
        collaborators.notification_manager(
            level="info",
            title="New comment on your inspiration",
            message=f'{comment.author_username or "Someone"} commented on "{insp.title}"',
            category="inspirations",
            user_id=insp.user_id,
            source="core",
            type="inspiration.comment",
            metadata={"inspiration_id": inspiration_id, "comment_id": comment.id},
        )

    return comment


def delete_comment(collaborators: InspirationCollaborators, comment_id: str, user_id: str, is_admin: bool = False) -> None:
    comment = collaborators.repository.get_comment(comment_id)
    if not comment:
        raise ValueError("Comment not found")
    if comment.user_id != user_id and not is_admin:
        raise ValueError("Comment not found")
    collaborators.repository.delete_comment(comment_id)
