import copy
import logging
import secrets
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from src.features.elixirs.records import ACTIVE_INVOCATION_STATUSES
from src.features.video_director.binding import DirectorBindingError

logger = logging.getLogger(__name__)

SEED_LIMIT = 4294967295
FAILED_INVOCATION_STATUSES = ("failed", "interrupted")


class BindingFailed(Exception):
    pass


SINGLE_SHOT_MODES = ("t2v", "i2v", "flf")
DIRECTOR_REFUSALS = {
    "no_director": "This session has no Video Director to put the image into.",
    "unsupported_mode": "This session's video cannot take a frame here. Use a single shot without keyframes or audio.",
    "segment_required": "This session has more than one shot. Elixirs can feed an image into a single-shot session only.",
    "unknown_segment": "The shot this connection points at is no longer in the session.",
    "first_frame_required": "This session has no first frame. A last frame needs one, so add a first frame in the session.",
    "invalid_media": "Only an image can be a first or last frame.",
    "invalid_role": "This input cannot take a frame.",
}


def director_problem(document: Any, role: str, subject: str = "This session") -> Optional[str]:
    if not isinstance(document, Mapping):
        return f"{subject} has no Video Director to put the image into."
    segments = [segment for segment in document.get("segments") or [] if isinstance(segment, Mapping)]
    if len(segments) > 1:
        return f"{subject} has {len(segments)} shots. Elixirs can feed an image into a single-shot session only."
    if document.get("mode") not in SINGLE_SHOT_MODES:
        return f"{subject} uses keyframes, audio or other timed parts. Elixirs can feed an image into a plain single-shot session only."
    if role == "last" and document.get("mode") == "t2v":
        return f"{subject} has no first frame. A last frame needs one, so add a first frame in the session."
    return None


def slot_of(invocation: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (invocation["node_id"], invocation.get("edge_id") or "", invocation["input_key"])


def latest_attempts(invocations: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for invocation in invocations:
        if invocation["status"] == "superseded":
            continue
        slot = slot_of(invocation)
        if slot not in latest or invocation["attempt"] > latest[slot]["attempt"]:
            latest[slot] = dict(invocation)
    return sorted(latest.values(), key=lambda item: (item["node_id"], item["ordinal"], item.get("edge_id") or ""))


def node_status(latest: List[Mapping[str, Any]]) -> str:
    if not latest:
        return "waiting"
    statuses = {item["status"] for item in latest}
    if statuses & {"claimed", "submitted", "running"}:
        return "running"
    if "pending" in statuses:
        return "queued"
    if statuses & set(FAILED_INVOCATION_STATUSES):
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    return "done"


def outputs_from_files(generation_id: str, files: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    outputs = []
    for item in files:
        file_id = str(item.get("id"))
        outputs.append({
            "kind": "history",
            "generation_id": generation_id,
            "file_id": file_id,
            "media_type": str(item.get("file_type") or "").lower() or None,
            "file_index": item.get("index"),
            "original_filename": str(item.get("file_path") or "").rsplit("/", 1)[-1] or None,
            "preview_url": f"/api/media/files/{file_id}?size=medium",
        })
    return outputs


def manifest_for(latest: List[Mapping[str, Any]], ports: Mapping[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    manifest: Dict[str, List[Dict[str, Any]]] = {name: [] for name in ports}
    for invocation in sorted(latest, key=lambda item: item["ordinal"]):
        for output in invocation.get("outputs") or []:
            for name, media_type in ports.items():
                if output.get("media_type") == media_type:
                    manifest[name].append({
                        **output,
                        "source_invocation_id": invocation["id"],
                        "source_ordinal": invocation["ordinal"],
                    })
    return manifest


def media_manifest(assets: Iterable[Mapping[str, Any]], port: str) -> Dict[str, List[Dict[str, Any]]]:
    return {port: [dict(asset) for asset in assets]}


def resolve_seed(request: Dict[str, Any], random_below: Callable[[int], int] = secrets.randbelow) -> Optional[int]:
    form_data = request.get("form_data") or {}
    seed = form_data.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, (int, float)):
        return None
    if int(seed) >= 0:
        return int(seed)
    chosen = random_below(SEED_LIMIT)
    request["form_data"] = {**form_data, "seed": chosen}
    return chosen


def multi_item(resolved: Mapping[str, Any]) -> Dict[str, Any]:
    value = resolved["value"]
    item = {
        "path": value,
        "relative_path": value,
        "name": resolved.get("original_filename") or value.rsplit("/", 1)[-1],
        "type": str(resolved.get("media_type") or "").lower() or None,
    }
    return {key: entry for key, entry in item.items() if entry is not None}


def bind_input(
    request: Dict[str, Any],
    binding: Mapping[str, Any],
    resolved: Mapping[str, Any],
    segment_id: Optional[str],
    bind_director: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    bound = copy.deepcopy(request)
    if binding.get("kind") == "form_field":
        field = binding["field"]
        form_data = dict(bound.get("form_data") or {})
        if binding.get("multi"):
            form_data[field] = [multi_item(resolved)]
        else:
            form_data[field] = resolved["value"]
            if resolved.get("origin"):
                form_data[f"{field}__origin"] = resolved["origin"]
        bound["form_data"] = form_data
        return bound
    if binding.get("kind") == "video_director_role":
        try:
            return bind_director(bound, binding["role"], resolved, segment_id=segment_id, promote=True)
        except DirectorBindingError as error:
            logger.warning("Video Director refused the %s frame (%s): %s", binding["role"], error.code, error)
            raise BindingFailed(DIRECTOR_REFUSALS.get(error.code, "This session's video cannot take the frame.")) from None
    raise BindingFailed(f"Unknown input binding {binding.get('kind')!r}")


TEXT_BINDINGS = {"prompt": ("positive", "prompt"), "negative_prompt": ("negative", "negative_prompt")}


def bind_text(request: Dict[str, Any], binding: str, text: str) -> Dict[str, Any]:
    channel, shot_key = TEXT_BINDINGS[binding]
    bound = copy.deepcopy(request)
    pairs = [dict(pair) for pair in bound.get("prompts") or [] if isinstance(pair, Mapping)] or [{"positive": "", "negative": ""}]
    if channel == "positive":
        bound["prompts"] = [{"positive": text, "negative": pairs[0].get("negative") or ""}]
        bound["batch_prompts"] = False
    else:
        bound["prompts"] = [{**pair, "negative": text} for pair in pairs]
    segments = [segment for segment in bound.get("segments") or [] if isinstance(segment, Mapping) and segment.get("channel", "positive") != channel]
    segments.append({"channel": channel, "prompt_index": 0, "segment_index": 0, "segment_type": "content", "text": text})
    bound["segments"] = segments
    bound["prompt_state"] = None
    form_data = dict(bound.get("form_data") or {})
    document = form_data.get("video_director")
    if isinstance(document, Mapping):
        shots = [dict(shot) for shot in document.get("segments") or [] if isinstance(shot, Mapping)]
        if len(shots) > 1:
            raise BindingFailed(f"This session has {len(shots)} shots. A written prompt can replace the prompt of a single-shot session only.")
        if shots:
            shots[0][shot_key] = text
            form_data["video_director"] = {**document, "segments": shots}
            bound["form_data"] = form_data
    return bound


def text_outputs(text_id: str, text: str, for_key: str, pinned: bool = False, reasoning: Optional[str] = None) -> List[Dict[str, Any]]:
    entry = {"kind": "text", "text_id": text_id, "media_type": "text", "text": text, "for_key": for_key}
    if reasoning:
        entry["reasoning"] = reasoning
    if pinned:
        entry["pinned"] = True
    return [entry]


def pick_text(texts: List[Mapping[str, Any]], key: str) -> Optional[Mapping[str, Any]]:
    for entry in texts:
        if entry.get("for_key") == key:
            return entry
    return texts[0] if len(texts) == 1 else None


def prepared_template(template: Mapping[str, Any], unbound_fields: Iterable[str]) -> Dict[str, Any]:
    request = copy.deepcopy(dict(template))
    form_data = dict(request.get("form_data") or {})
    for field in unbound_fields:
        form_data.pop(field, None)
        form_data.pop(f"{field}__origin", None)
    request["form_data"] = form_data
    request["tab_id"] = None
    request["idempotency_key"] = None
    return request


def rollup_status(run: Mapping[str, Any], latest: List[Mapping[str, Any]], checkpoints: Mapping[str, Any], node_states: Mapping[str, Any]) -> str:
    active = any(item["status"] in ACTIVE_INVOCATION_STATUSES for item in latest)
    if run.get("cancel_requested_at"):
        return "cancelling" if active else "cancelled"
    if active:
        return "running"
    if any(cp.get("confirmed") is None and cp.get("eligible") and not cp.get("error") for cp in checkpoints.values()):
        return "awaiting_selection"
    blocked = any(state.get("status") in ("failed", "no_output") for state in node_states.values()) or any(
        cp.get("error") or not cp.get("eligible") for cp in checkpoints.values()
    )
    if blocked:
        return "needs_attention"
    return "completed"


def reach(edges: Iterable[Mapping[str, Any]], forward: bool = True) -> Dict[str, List[str]]:
    links: Dict[str, List[str]] = {}
    for edge in edges:
        start, end = (edge["source"], edge["target"]) if forward else (edge["target"], edge["source"])
        links.setdefault(start, []).append(end)
    result: Dict[str, List[str]] = {}
    for origin in list(links):
        seen: List[str] = []
        stack = list(links[origin])
        while stack:
            current = stack.pop()
            if current in seen or current == origin:
                continue
            seen.append(current)
            stack.extend(links.get(current, []))
        result[origin] = sorted(seen)
    return result
