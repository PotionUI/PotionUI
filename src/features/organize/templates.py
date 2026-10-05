from typing import Any, Dict, List, Optional


def _collection(name: str) -> Dict[str, Any]:
    return {"action": "add_to_collection", "config": {"collection_name": name, "create_if_missing": True}}


STARTER_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "one-model-one-collection",
        "title": "File one model's output in its own collection",
        "description": "Pick a model; everything made with it lands in a collection named after it.",
        "subject": "generation",
        "needs": ["conditions.0.value", "actions.0.config.collection_name"],
        "rule": {
            "name": "One model, one collection",
            "match": "all",
            "conditions": [{"fact": "model", "operator": "is", "value": None}],
            "actions": [_collection("")],
        },
    },
    {
        "id": "landscape-images",
        "title": "Sort landscape images",
        "description": "Wide images go to Landscape.",
        "subject": "generation",
        "needs": [],
        "rule": {
            "name": "Landscape images",
            "match": "all",
            "conditions": [
                {"fact": "media_kind", "operator": "is", "value": "image"},
                {"fact": "aspect", "operator": "is", "value": "landscape"},
            ],
            "actions": [_collection("Landscape")],
        },
    },
    {
        "id": "portrait-images",
        "title": "Sort portrait images",
        "description": "Tall images go to Portrait.",
        "subject": "generation",
        "needs": [],
        "rule": {
            "name": "Portrait images",
            "match": "all",
            "conditions": [
                {"fact": "media_kind", "operator": "is", "value": "image"},
                {"fact": "aspect", "operator": "is", "value": "portrait"},
            ],
            "actions": [_collection("Portrait")],
        },
    },
    {
        "id": "videos",
        "title": "Keep videos together",
        "description": "Every video goes to Videos.",
        "subject": "generation",
        "needs": [],
        "rule": {
            "name": "Videos",
            "match": "all",
            "conditions": [{"fact": "media_kind", "operator": "is", "value": "video"}],
            "actions": [_collection("Videos")],
        },
    },
    {
        "id": "audio",
        "title": "Keep audio together",
        "description": "Every audio clip goes to Audio.",
        "subject": "generation",
        "needs": [],
        "rule": {
            "name": "Audio",
            "match": "all",
            "conditions": [{"fact": "media_kind", "operator": "is", "value": "audio"}],
            "actions": [_collection("Audio")],
        },
    },
    {
        "id": "library-videos",
        "title": "Keep uploaded videos together",
        "description": "Every video you upload goes to Videos.",
        "subject": "upload",
        "needs": [],
        "rule": {
            "name": "Uploaded videos",
            "match": "all",
            "conditions": [{"fact": "media_kind", "operator": "is", "value": "video"}],
            "actions": [_collection("Videos")],
        },
    },
    {
        "id": "library-images",
        "title": "Keep uploaded images together",
        "description": "Every image you upload goes to Images.",
        "subject": "upload",
        "needs": [],
        "rule": {
            "name": "Uploaded images",
            "match": "all",
            "conditions": [{"fact": "media_kind", "operator": "is", "value": "image"}],
            "actions": [_collection("Images")],
        },
    },
    {
        "id": "loras",
        "title": "Collect new LoRAs",
        "description": "Every LoRA added to your models goes to LoRAs.",
        "subject": "model",
        "needs": [],
        "rule": {
            "name": "LoRAs",
            "match": "all",
            "conditions": [{"fact": "model_type", "operator": "is", "value": "lora"}],
            "actions": [_collection("LoRAs")],
        },
    },
]


def list_templates(subject: Optional[str] = None) -> List[Dict[str, Any]]:
    return [t for t in STARTER_TEMPLATES if subject is None or t["subject"] == subject]
