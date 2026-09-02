"""Generation metadata embedded in an image: A1111/Forge/SD.Next `parameters`
(PNG text chunk or JPEG/WebP EXIF UserComment), InvokeAI, NovelAI, and a
ComfyUI API-format workflow graph.

Best-effort throughout: a source that carries no recognized metadata, or a
malformed one, returns an empty/partial result rather than raising - the
caller reports that as a skipped file, not a batch failure.
"""
import io
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Union

from PIL import Image

from src.features.prompt_database.importing.models import ParsedPrompt

logger = logging.getLogger(__name__)

_STEPS_LINE = re.compile(r"\bSteps:\s*\d", re.IGNORECASE)
_SAMPLER_CLASS_TYPES = {"KSampler", "KSamplerAdvanced"}


def _stem(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base or None


def _split_settings_line(line: str) -> Dict[str, str]:
    """A1111's trailing `Key: value, Key: value` line - commas inside
    brackets/parens don't end a field (e.g. `Lora hashes: "a: b, c: d"`)."""
    parts: List[str] = []
    depth = 0
    current = ""
    for ch in line:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    settings: Dict[str, str] = {}
    for part in parts:
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        settings[key.strip().lower()] = value.strip()
    return settings


def _parse_a1111_parameters_text(text: str) -> Optional[Dict[str, Any]]:
    if not text or not text.strip():
        return None
    lines = text.replace("\r\n", "\n").split("\n")

    neg_idx = next(
        (i for i, line in enumerate(lines) if line.strip().lower().startswith("negative prompt:")), None
    )
    steps_idx = next(
        (i for i in range(len(lines) - 1, -1, -1) if _STEPS_LINE.search(lines[i])), None
    )

    if neg_idx is not None:
        positive = "\n".join(lines[:neg_idx]).strip()
    elif steps_idx is not None:
        positive = "\n".join(lines[:steps_idx]).strip()
    else:
        positive = text.strip()

    negative = ""
    if neg_idx is not None:
        end = steps_idx if (steps_idx is not None and steps_idx > neg_idx) else len(lines)
        negative_lines = list(lines[neg_idx:end])
        if negative_lines:
            negative_lines[0] = re.sub(r"^negative prompt:\s?", "", negative_lines[0], flags=re.IGNORECASE)
        negative = "\n".join(negative_lines).strip()

    if not positive and not negative:
        return None

    settings = _split_settings_line(lines[steps_idx]) if steps_idx is not None else {}
    result: Dict[str, Any] = {"positive": positive, "negative": negative}
    if "steps" in settings:
        digits = re.sub(r"[^\d]", "", settings["steps"])
        if digits:
            result["steps"] = int(digits)
    if settings.get("sampler"):
        result["sampler"] = settings["sampler"]
    if "cfg scale" in settings:
        try:
            result["cfg_scale"] = float(settings["cfg scale"])
        except ValueError:
            pass
    if "seed" in settings:
        try:
            result["seed"] = int(settings["seed"])
        except ValueError:
            pass
    if "size" in settings:
        match = re.match(r"(\d+)\s*x\s*(\d+)", settings["size"])
        if match:
            result["width"] = int(match.group(1))
            result["height"] = int(match.group(2))
    if settings.get("model"):
        result["model_name"] = settings["model"]
    return result


def _paired_results(positive: str, negative: str, name: Optional[str], shared: Dict[str, Any]) -> List[ParsedPrompt]:
    shared = {k: v for k, v in shared.items() if v is not None}
    group_id = uuid.uuid4().hex if positive and negative else None
    results: List[ParsedPrompt] = []
    if positive:
        results.append(ParsedPrompt(text=positive, usage_hint="positive", name=name, group_id=group_id, **shared))
    if negative:
        results.append(ParsedPrompt(text=negative, usage_hint="negative", name=name, group_id=group_id, **shared))
    return results


def _parse_a1111_string(raw: str, name: Optional[str]) -> Optional[List[ParsedPrompt]]:
    parsed = _parse_a1111_parameters_text(raw)
    if not parsed:
        return None
    positive = parsed.pop("positive", "") or ""
    negative = parsed.pop("negative", "") or ""
    results = _paired_results(positive, negative, name, parsed)
    return results or None


def _parse_invokeai_metadata(raw: str, name: Optional[str]) -> Optional[List[ParsedPrompt]]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    positive = str(payload.get("positive_prompt") or "").strip()
    negative = str(payload.get("negative_prompt") or "").strip()
    if not positive and not negative:
        return None
    shared: Dict[str, Any] = {
        "cfg_scale": payload.get("cfg_scale"),
        "steps": payload.get("steps"),
        "sampler": payload.get("scheduler"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "seed": payload.get("seed"),
    }
    model = payload.get("model")
    if isinstance(model, dict) and model.get("name"):
        shared["model_name"] = model.get("name")
    return _paired_results(positive, negative, name, shared) or None


def _parse_novelai_comment(raw: str, name: Optional[str]) -> Optional[List[ParsedPrompt]]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "prompt" not in payload:
        return None
    positive = str(payload.get("prompt") or "").strip()
    negative = str(payload.get("uc") or "").strip()
    if not positive and not negative:
        return None
    shared: Dict[str, Any] = {
        "cfg_scale": payload.get("scale"),
        "steps": payload.get("steps"),
        "sampler": payload.get("sampler"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "seed": payload.get("seed"),
    }
    return _paired_results(positive, negative, name, shared) or None


def _comfyui_node_text(nodes: Dict[str, Any], node_id: Any) -> Optional[str]:
    node = nodes.get(str(node_id))
    if not isinstance(node, dict):
        return None
    text = (node.get("inputs") or {}).get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _comfyui_follow_link(nodes: Dict[str, Any], link: Any) -> Optional[str]:
    if not isinstance(link, list) or not link:
        return None
    return _comfyui_node_text(nodes, link[0])


def _parse_comfyui_graph(raw: str, name: Optional[str]) -> Optional[List[ParsedPrompt]]:
    try:
        nodes = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(nodes, dict):
        return None

    try:
        width = height = model_name = None
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type")
            inputs = node.get("inputs") or {}
            if class_type == "EmptyLatentImage" and width is None:
                width, height = inputs.get("width"), inputs.get("height")
            elif class_type == "CheckpointLoaderSimple" and model_name is None:
                model_name = inputs.get("ckpt_name")

        sampler_node = next(
            (node for node in nodes.values() if isinstance(node, dict) and node.get("class_type") in _SAMPLER_CLASS_TYPES),
            None,
        )

        if sampler_node is None:
            texts = [
                node["inputs"]["text"].strip()
                for node in nodes.values()
                if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode"
                and isinstance((node.get("inputs") or {}).get("text"), str)
                and node["inputs"]["text"].strip()
            ]
            if not texts:
                return None
            return [
                ParsedPrompt(text=text, usage_hint="positive", name=name, width=width, height=height, model_name=model_name)
                for text in texts
            ]

        inputs = sampler_node.get("inputs") or {}
        positive = _comfyui_follow_link(nodes, inputs.get("positive")) or ""
        negative = _comfyui_follow_link(nodes, inputs.get("negative")) or ""
        shared: Dict[str, Any] = {
            "width": width, "height": height, "model_name": model_name,
            "steps": inputs.get("steps"), "cfg_scale": inputs.get("cfg"),
            "seed": inputs.get("seed"), "sampler": inputs.get("sampler_name"),
        }
        return _paired_results(positive, negative, name, shared) or None
    except Exception as exc:  # pragma: no cover - defensive against odd graphs
        logger.debug("ComfyUI graph metadata parse gave up: %s", exc)
        return None


def _decode_user_comment(raw: bytes) -> str:
    if raw.startswith(b"UNICODE\x00"):
        payload = raw[8:]
        try:
            return payload.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return payload.decode("utf-16-be", errors="ignore").rstrip("\x00")
    if raw.startswith(b"ASCII\x00\x00\x00"):
        return raw[8:].decode("ascii", errors="ignore").rstrip("\x00")
    if raw.startswith(b"\x00" * 8):
        return raw[8:].decode("utf-8", errors="ignore").rstrip("\x00")
    return raw.decode("utf-8", errors="ignore").rstrip("\x00")


def _read_exif_user_comment(image: Image.Image) -> Optional[str]:
    try:
        exif = image.getexif()
    except Exception:
        return None
    if not exif:
        return None
    raw = exif.get(0x9286)
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        return _decode_user_comment(raw)
    return None


def parse_image(data: Union[bytes, str], *, filename: Optional[str] = None) -> List[ParsedPrompt]:
    if isinstance(data, str):
        return []
    try:
        image = Image.open(io.BytesIO(data))
    except Exception:
        return []

    name = _stem(filename)
    info = getattr(image, "info", {}) or {}

    invokeai_raw = info.get("invokeai_metadata")
    if isinstance(invokeai_raw, str):
        parsed = _parse_invokeai_metadata(invokeai_raw, name)
        if parsed:
            return parsed

    comment_raw = info.get("Comment")
    if isinstance(comment_raw, str):
        parsed = _parse_novelai_comment(comment_raw, name)
        if parsed:
            return parsed

    graph_raw = info.get("prompt")
    if isinstance(graph_raw, str):
        parsed = _parse_comfyui_graph(graph_raw, name)
        if parsed:
            return parsed

    parameters_raw = info.get("parameters")
    if not isinstance(parameters_raw, str):
        parameters_raw = _read_exif_user_comment(image)
    if parameters_raw:
        parsed = _parse_a1111_string(parameters_raw, name)
        if parsed:
            return parsed

    return []
