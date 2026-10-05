from src.plugin_api.organize import OrganizeChange

SENT = []
UNDONE = []

LABELS = ("cat", "dog", "sunset", "beach")


def labels(item):
    found = set()
    for value in (item.get("form_data") or {}).values():
        if isinstance(value, str):
            words = {word.strip(".,!").lower() for word in value.split()}
            found.update(label for label in LABELS if label in words)
    return sorted(found)


def label_options(user_id, subject, query):
    return [label for label in LABELS if query.lower() in label]


def notify_webhook(item, config, user_id):
    SENT.append((item.item_id, config["channel"], user_id))
    return [OrganizeChange(target_type="example_webhook", target_id=config["channel"], target_name=config["channel"])]


def undo_webhook(item, change, user_id):
    UNDONE.append((item.item_id, change.target_id, user_id))
    return True
