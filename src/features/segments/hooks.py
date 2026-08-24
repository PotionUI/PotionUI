"""Hook points for saved Segments, multi-segment Templates, and categories."""

from src.platform.plugins.hooks import hooks_registry


def _field(type_name: str, description: str):
    return {"type": type_name, "description": description}


_BLOCKING = ["blocked", "block_reason"]

SEGMENT_HOOKS = hooks_registry.declare(
    "segment",
    "backend",
    "before_create_category",
    "after_create_category",
    "before_update_category",
    "after_update_category",
    "before_delete_category",
    "after_delete_category",
    "before_create_segment",
    "after_create_segment",
    "before_update_segment",
    "after_update_segment",
    "before_delete_segment",
    "after_delete_segment",
    "before_create_template",
    "after_create_template",
    "before_update_template",
    "after_update_template",
    "before_delete_template",
    "after_delete_template",
    specs={
        "before_create_category": {
            "description": "Before a user creates a Segment Category; may block.",
            "payload": {
                "name": _field("str", "Requested category name"),
                "description": _field("str", "Requested description"),
                "color": _field("str", "Requested category color"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_create_category": {
            "description": "After a Segment Category is created.",
            "payload": {
                "category_id": _field("str", "Created category ID"),
                "name": _field("str", "Category name"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_update_category": {
            "description": "Before a user's Segment Category is updated; may block.",
            "payload": {
                "category_id": _field("str", "Category ID"),
                "old_name": _field("str", "Current name"),
                "new_name": _field("str", "Requested name"),
                "description": _field("str", "Requested description"),
                "color": _field("str", "Requested color"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_update_category": {
            "description": "After a Segment Category is updated.",
            "payload": {
                "category_id": _field("str", "Updated category ID"),
                "name": _field("str", "Current name"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_delete_category": {
            "description": "Before an unused Segment Category is deleted; may block.",
            "payload": {
                "category_id": _field("str", "Category ID"),
                "name": _field("str", "Category name"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_delete_category": {
            "description": "After a Segment Category is deleted.",
            "payload": {
                "category_id": _field("str", "Deleted category ID"),
                "name": _field("str", "Deleted category name"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_create_segment": {
            "description": "Before a reusable single saved Segment is created; may block.",
            "payload": {
                "name": _field("str", "Required saved-Segment name"),
                "category_id": _field("str", "Owning category ID"),
                "segment": _field("RichSegment", "Complete rich Segment state"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_create_segment": {
            "description": "After a reusable saved Segment is created.",
            "payload": {
                "segment_id": _field("str", "Created saved-Segment ID"),
                "name": _field("str", "Saved-Segment name"),
                "category_id": _field("str", "Category ID"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_update_segment": {
            "description": "Before a reusable saved Segment is updated; may block.",
            "payload": {
                "segment_id": _field("str", "Saved-Segment ID"),
                "old_name": _field("str", "Current name"),
                "new_name": _field("str", "Requested name"),
                "category_id": _field("str", "Requested category ID"),
                "segment": _field("RichSegment", "Complete replacement state"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_update_segment": {
            "description": "After a reusable saved Segment is updated.",
            "payload": {
                "segment_id": _field("str", "Updated saved-Segment ID"),
                "name": _field("str", "Saved-Segment name"),
                "category_id": _field("str", "Category ID"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_delete_segment": {
            "description": "Before a reusable saved Segment is deleted; may block.",
            "payload": {
                "segment_id": _field("str", "Saved-Segment ID"),
                "name": _field("str", "Saved-Segment name"),
                "category_id": _field("str", "Category ID"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_delete_segment": {
            "description": "After a reusable saved Segment is deleted.",
            "payload": {
                "segment_id": _field("str", "Deleted saved-Segment ID"),
                "name": _field("str", "Deleted saved-Segment name"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_create_template": {
            "description": "Before an ordered multi-segment Template is created; may block.",
            "payload": {
                "name": _field("str", "Required Template name"),
                "description": _field("str", "Template description"),
                "tags": _field("list[str]", "Template tags"),
                "segments": _field("list[RichSegment]", "Complete ordered child list"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_create_template": {
            "description": "After a multi-segment Template is created.",
            "payload": {
                "template_id": _field("str", "Created Template ID"),
                "name": _field("str", "Template name"),
                "segment_count": _field("int", "Number of ordered children"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_update_template": {
            "description": "Before a Template and its full child list are replaced; may block.",
            "payload": {
                "template_id": _field("str", "Template ID"),
                "old_name": _field("str", "Current name"),
                "new_name": _field("str", "Requested name"),
                "description": _field("str", "Requested description"),
                "tags": _field("list[str]", "Requested tags"),
                "segments": _field("list[RichSegment]", "Complete ordered replacement list"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_update_template": {
            "description": "After a Template aggregate is replaced.",
            "payload": {
                "template_id": _field("str", "Updated Template ID"),
                "name": _field("str", "Template name"),
                "segment_count": _field("int", "Number of ordered children"),
                "user_id": _field("str", "Owning user"),
            },
        },
        "before_delete_template": {
            "description": "Before a multi-segment Template is deleted; may block.",
            "payload": {
                "template_id": _field("str", "Template ID"),
                "name": _field("str", "Template name"),
                "user_id": _field("str", "Owning user"),
            },
            "mutable": _BLOCKING,
        },
        "after_delete_template": {
            "description": "After a multi-segment Template is deleted.",
            "payload": {
                "template_id": _field("str", "Deleted Template ID"),
                "name": _field("str", "Deleted Template name"),
                "user_id": _field("str", "Owning user"),
            },
        },
    },
)
