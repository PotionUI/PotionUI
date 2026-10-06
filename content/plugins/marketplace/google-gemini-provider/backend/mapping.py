import base64
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.plugin_api.cloud import CloudArtifact, CloudCost, CloudError, CloudResult

from .catalog import MILLION, image_price, token_prices

REFUSED_MESSAGE = "The model's content filter refused this request. Try a different prompt or picture."
NO_IMAGE_MESSAGE = "The model answered without a picture. Try rephrasing the prompt."
UNREADABLE_MESSAGE = "Google sent an answer that could not be read."
REFUSAL_FINISH_REASONS = frozenset({
    "SAFETY",
    "IMAGE_SAFETY",
    "PROHIBITED_CONTENT",
    "IMAGE_PROHIBITED_CONTENT",
    "BLOCKLIST",
    "SPII",
    "RECITATION",
    "IMAGE_RECITATION",
})
RESPONSE_MODALITIES = ["TEXT", "IMAGE"]
TEXT_DETAIL_LIMIT = 200


def inline_part(path: Path, media_type: str) -> Dict[str, Any]:
    return {"inlineData": {"mimeType": media_type, "data": base64.b64encode(path.read_bytes()).decode("ascii")}}


def build_image_body(request: Any) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = [{"text": request.prompt}]
    for media in request.inputs.get("reference") or []:
        parts.append(inline_part(media.path, media.media_type))
    image_config: Dict[str, Any] = {}
    aspect_ratio = request.params.get("aspect_ratio")
    if aspect_ratio:
        image_config["aspectRatio"] = str(aspect_ratio)
    size = request.params.get("resolution")
    if size:
        image_config["imageSize"] = str(size)
    generation_config: Dict[str, Any] = {"responseModalities": list(RESPONSE_MODALITIES)}
    if image_config:
        generation_config["imageConfig"] = image_config
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": generation_config}


def _ratings(entries: Any) -> str:
    listed = []
    for rating in entries if isinstance(entries, list) else []:
        if not isinstance(rating, dict):
            continue
        if rating.get("blocked") or str(rating.get("probability") or "") in ("MEDIUM", "HIGH"):
            listed.append(f"{rating.get('category')}={rating.get('probability')}")
    return ", ".join(listed)


def _refusal(stage: str, reason: str, ratings: str) -> CloudError:
    facts = [f"{stage} blocked: {reason}"]
    if ratings:
        facts.append(f"ratings: {ratings}")
    return CloudError("refused", REFUSED_MESSAGE, detail="; ".join(facts))


def _token_count(details: Any, modality: str) -> int:
    total = 0
    for item in details if isinstance(details, list) else []:
        if isinstance(item, dict) and str(item.get("modality") or "").upper() == modality:
            count = item.get("tokenCount", item.get("token_count"))
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                total += count
    return total


def _whole(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def image_cost(model_id: str, usage: Any, size: Optional[str], images: int) -> Optional[CloudCost]:
    prices = token_prices(model_id)
    if prices is not None and isinstance(usage, dict) and _whole(usage.get("candidatesTokenCount")) + _whole(usage.get("thoughtsTokenCount")) > 0:
        prompt_tokens = _whole(usage.get("promptTokenCount"))
        candidates = _whole(usage.get("candidatesTokenCount"))
        image_tokens = min(_token_count(usage.get("candidatesTokensDetails"), "IMAGE"), candidates)
        text_tokens = candidates - image_tokens + _whole(usage.get("thoughtsTokenCount"))
        amount = (
            Decimal(prompt_tokens) * prices.input_per_million
            + Decimal(image_tokens) * prices.output_image_per_million
            + Decimal(text_tokens) * prices.output_text_per_million
        ) / MILLION
        return CloudCost(
            amount_usd=amount,
            source="estimate",
            detail={
                "basis": "tokens",
                "input_tokens": prompt_tokens,
                "output_image_tokens": image_tokens,
                "output_text_tokens": text_tokens,
            },
        )
    price = image_price(model_id, size)
    if price is None:
        return None
    return CloudCost(amount_usd=price * images, source="estimate", detail={"basis": "per_image", "images": images, "size": size or ""})


def _image_parts(candidate: Mapping[str, Any]) -> Tuple[List[Tuple[bytes, str]], List[str]]:
    images: List[Tuple[bytes, str]] = []
    texts: List[str] = []
    content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
    for part in content.get("parts") or []:
        if not isinstance(part, dict) or part.get("thought") is True:
            continue
        inline = part.get("inlineData") or part.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            media_type = str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
            if not media_type.startswith("image/"):
                continue
            try:
                data = base64.b64decode(inline["data"], validate=False)
            except (ValueError, TypeError):
                continue
            if data:
                images.append((data, media_type))
        elif isinstance(part.get("text"), str) and part["text"].strip():
            texts.append(part["text"].strip())
    return images, texts


def parse_image_result(payload: Any, request: Any) -> CloudResult:
    if not isinstance(payload, dict):
        raise CloudError("failed", UNREADABLE_MESSAGE, detail="response is not an object")
    feedback = payload.get("promptFeedback") if isinstance(payload.get("promptFeedback"), dict) else {}
    block = feedback.get("blockReason")
    if block and block != "BLOCK_REASON_UNSPECIFIED":
        raise _refusal("prompt", str(block), _ratings(feedback.get("safetyRatings")))
    candidates = [item for item in payload.get("candidates") or [] if isinstance(item, dict)]
    images: List[Tuple[bytes, str]] = []
    texts: List[str] = []
    reasons: List[str] = []
    ratings = ""
    for candidate in candidates:
        found, said = _image_parts(candidate)
        images.extend(found)
        texts.extend(said)
        reason = str(candidate.get("finishReason") or "")
        if reason:
            reasons.append(reason)
        ratings = ratings or _ratings(candidate.get("safetyRatings"))
    if not images:
        refusal = next((reason for reason in reasons if reason in REFUSAL_FINISH_REASONS), None)
        if refusal:
            raise _refusal("output", refusal, ratings)
        said = " ".join(texts)[:TEXT_DETAIL_LIMIT]
        detail = f"no image in the answer (finish reason: {', '.join(reasons) or 'none'})"
        if said:
            detail += f"; model said: {said}"
        raise CloudError("failed", NO_IMAGE_MESSAGE, detail=detail)
    artifacts = tuple(
        CloudArtifact(modality="image", index=index, data=data, media_type=media_type)
        for index, (data, media_type) in enumerate(images)
    )
    size = request.params.get("resolution")
    return CloudResult(
        artifacts=artifacts,
        cost=image_cost(request.model.provider_model_id, payload.get("usageMetadata"), str(size) if size else None, len(artifacts)),
        provider_job_id=str(payload["responseId"]) if payload.get("responseId") else None,
    )
