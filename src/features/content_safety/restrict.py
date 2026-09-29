from typing import Any, Dict, Iterable, List


def provider_flags_nsfw(providers: Iterable[Any]) -> bool:
    for provider in providers or []:
        flagged = provider.get("nsfw") if isinstance(provider, dict) else getattr(provider, "nsfw", False)
        if flagged:
            return True
    return False


def strip_model_media(model: Dict[str, Any]) -> Dict[str, Any]:
    stripped = dict(model)
    stripped["preview_media"] = None
    stripped["files"] = []
    return stripped


def restrict_model_list(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        strip_model_media(model)
        for model in models
        if not provider_flags_nsfw(model.get("providers"))
    ]
