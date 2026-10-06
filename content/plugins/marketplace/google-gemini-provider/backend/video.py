import base64
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.plugin_api.cloud import CloudArtifact, CloudCost, CloudError, CloudModelSpec, CloudResult, CloudStatus

from .catalog import second_price
from .mapping import REFUSED_MESSAGE, UNREADABLE_MESSAGE

FIRST_POLL_SECONDS = 10.0
POLL_SECONDS = 15.0
VIDEO_MEDIA_TYPE = "video/mp4"
LONG_ONLY_RESOLUTIONS = frozenset({"1080p", "4k"})
LONG_DURATION = 8
MODERATION_WORDS = ("safety", "policy", "blocked", "filtered", "responsible ai", "prohibited", "violat")
OPERATION_NAME = re.compile(r"^(models/[A-Za-z0-9._-]+/)?operations/[A-Za-z0-9._-]+$")


def is_video_spec(spec: CloudModelSpec) -> bool:
    return "video" in spec.outputs


def _image(media: Any) -> Dict[str, Any]:
    return {"inlineData": {"mimeType": media.media_type, "data": base64.b64encode(media.path.read_bytes()).decode("ascii")}}


def _duration(request: Any) -> int:
    value = request.params.get("duration_s")
    if value is None or value == "":
        value = next((param.default for param in request.model.params if param.name == "duration_s"), LONG_DURATION)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise CloudError("invalid_request", "The video length is not a number of seconds.", request_sent=False) from None


def build_video_body(request: Any) -> Dict[str, Any]:
    instance: Dict[str, Any] = {"prompt": request.prompt}
    first = (request.inputs.get("first_frame") or [])[:1]
    last = (request.inputs.get("last_frame") or [])[:1]
    if last and not first:
        raise CloudError(
            "invalid_request", "An end picture needs a start picture as well. Add a start picture or remove the end picture.",
            detail="lastFrame without image", request_sent=False,
        )
    if first:
        instance["image"] = _image(first[0])
    if last:
        instance["lastFrame"] = _image(last[0])
    duration = _duration(request)
    resolution = request.params.get("resolution")
    if resolution and str(resolution).lower() in LONG_ONLY_RESOLUTIONS and duration != LONG_DURATION:
        raise CloudError(
            "invalid_request", f"{resolution} videos are always {LONG_DURATION} seconds long. Pick {LONG_DURATION} seconds or 720p.",
            detail=f"durationSeconds {duration} with resolution {resolution}", request_sent=False,
        )
    parameters: Dict[str, Any] = {"durationSeconds": str(duration)}
    if request.params.get("aspect_ratio"):
        parameters["aspectRatio"] = str(request.params["aspect_ratio"])
    if resolution:
        parameters["resolution"] = str(resolution)
    return {"instances": [instance], "parameters": parameters}


def operation_name(payload: Any) -> str:
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not OPERATION_NAME.match(name) or ".." in name:
        raise CloudError("failed", "Google did not accept the video job.", detail="no usable operation name in the answer")
    return name


def job_handle(request: Any, name: str) -> Dict[str, Any]:
    resolution = request.params.get("resolution")
    return {
        "operation": name,
        "model": request.model.provider_model_id,
        "resolution": str(resolution).lower() if resolution else "",
        "seconds": _duration(request),
    }


def video_cost(handle: Dict[str, Any]) -> Optional[CloudCost]:
    price = second_price(str(handle.get("model") or ""), handle.get("resolution") or None)
    seconds = handle.get("seconds")
    if price is None or not isinstance(seconds, int):
        return None
    return CloudCost(
        amount_usd=price * Decimal(seconds), source="estimate",
        detail={"basis": "per_second", "seconds": seconds, "resolution": handle.get("resolution") or ""},
    )


def _filtered(response: Dict[str, Any]) -> List[str]:
    reasons = response.get("raiMediaFilteredReasons") or response.get("raiFilteredReason") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    listed = [str(reason)[:200] for reason in reasons if reason] if isinstance(reasons, list) else []
    count = response.get("raiMediaFilteredCount")
    if not listed and isinstance(count, int) and not isinstance(count, bool) and count > 0:
        listed = [f"{count} video(s) filtered"]
    return listed


def parse_operation(http: Any, payload: Any, handle: Dict[str, Any], job_id: str) -> CloudStatus:
    if not isinstance(payload, dict):
        raise CloudError("failed", UNREADABLE_MESSAGE, detail="poll response is not an object")
    if not payload.get("done"):
        return CloudStatus(state="running", poll_after_s=POLL_SECONDS)
    error = payload.get("error")
    if isinstance(error, dict) and (error.get("message") or error.get("code")):
        message = str(error.get("message") or error.get("code"))[:500]
        if any(word in message.lower() for word in MODERATION_WORDS):
            raise CloudError("refused", REFUSED_MESSAGE, detail=f"video job refused: {message}")
        return CloudStatus(state="failed", message=message)
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    generated = response.get("generateVideoResponse") if isinstance(response.get("generateVideoResponse"), dict) else response
    samples = generated.get("generatedSamples") or generated.get("generatedVideos") or []
    uris = []
    for sample in samples if isinstance(samples, list) else []:
        video = sample.get("video") if isinstance(sample, dict) else None
        uri = video.get("uri") if isinstance(video, dict) else None
        if isinstance(uri, str) and uri.startswith(("https://", "http://")):
            uris.append(uri)
    if not uris:
        filtered = _filtered(generated)
        if filtered:
            raise CloudError("refused", REFUSED_MESSAGE, detail="video filtered: " + "; ".join(filtered))
        return CloudStatus(state="failed", message="the finished job listed no video")
    artifacts = tuple(
        CloudArtifact(modality="video", index=index, url=http.resolve(uri), media_type=VIDEO_MEDIA_TYPE)
        for index, uri in enumerate(uris)
    )
    return CloudStatus(
        state="succeeded", progress=1.0,
        result=CloudResult(artifacts=artifacts, cost=video_cost(handle), provider_job_id=job_id),
    )
