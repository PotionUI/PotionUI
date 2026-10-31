from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from src.platform.runtime.gpu_profile import GPU_GENERATIONS, PRECISIONS

RECOMMENDATION_EXTENSIONS = ('.safetensors', '.ckpt', '.pt', '.pth', '.bin')

RECOMMENDATION_KEYS = frozenset(
    {
        "name", "provider", "ref", "link", "size", "sha256", "description",
        "size_bytes", "precision", "recommended_for", "default", "gated", "license_url",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)\s*$", re.IGNORECASE)
_SIZE_POWERS = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4, "KIB": 1, "MIB": 2, "GIB": 3, "TIB": 4}
_SIZE_TOLERANCE = 0.01


@dataclass(frozen=True)
class RecommendationRule:
    min_vram_gb: Optional[float] = None
    generations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Recommendation:
    name: str
    provider: Optional[str] = None
    ref: Optional[str] = None
    link: Optional[str] = None
    size: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    precision: Optional[str] = None
    recommended_for: Tuple[RecommendationRule, ...] = ()
    default: bool = False
    gated: bool = False
    license_url: Optional[str] = None
    description: Optional[str] = None

    @property
    def downloadable(self) -> bool:
        return bool(self.link or (self.provider and self.ref))

    @property
    def filename(self) -> str:
        if PurePosixPath(self.name).suffix.lower() in RECOMMENDATION_EXTENSIONS:
            return self.name
        if self.link:
            tail = unquote(PurePosixPath(urlparse(self.link).path).name)
            if tail:
                return tail
        return self.name

    @property
    def known_size_bytes(self) -> Optional[int]:
        if self.size_bytes is not None:
            return self.size_bytes
        return size_text_bytes(self.size)

    def provider_hint(self) -> Dict[str, Any]:
        if self.provider and self.ref:
            model_id, version_id = parse_ref(self.ref)
            hint: Dict[str, Any] = {"source": self.provider}
            if model_id:
                hint["model_id"] = model_id
            if version_id:
                hint["version_id"] = version_id
            return hint
        if self.link:
            return {"download_url": self.link}
        return {}


def parse_ref(ref: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not ref:
        return None, None
    try:
        parsed = json.loads(ref)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        if parsed.get("provider_model_id"):
            return parsed.get("provider_model_id"), parsed.get("provider_version_id")
        if parsed.get("model_id"):
            return parsed.get("model_id"), parsed.get("version_id")
        if parsed.get("repo"):
            revision = parsed.get("revision") or "main"
            file_path = parsed.get("file")
            return parsed.get("repo"), (f"{revision}@{file_path}" if file_path else None)
    return ref, None


def recommendation_filenames(name: str) -> List[str]:
    return [name, *(f"{name}{ext}" for ext in RECOMMENDATION_EXTENSIONS)]


def size_text_candidates(text: Optional[str]) -> List[int]:
    if not isinstance(text, str):
        return []
    match = _SIZE_RE.match(text)
    if match is None:
        return []
    value = float(match.group(1))
    power = _SIZE_POWERS[match.group(2).upper()]
    return sorted({int(value * 1000 ** power), int(value * 1024 ** power)})


def size_text_bytes(text: Optional[str]) -> Optional[int]:
    candidates = size_text_candidates(text)
    return candidates[-1] if candidates else None


def size_text_agrees(text: Optional[str], size_bytes: int) -> bool:
    candidates = size_text_candidates(text)
    if not candidates:
        return True
    if size_bytes == 0:
        return 0 in candidates
    return any(abs(candidate - size_bytes) / size_bytes <= _SIZE_TOLERANCE for candidate in candidates)


def format_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return ""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} GB"


def _optional_bool(raw: Dict[str, Any], key: str) -> bool:
    value = raw.get(key)
    return value if isinstance(value, bool) else False


def _optional_str(raw: Dict[str, Any], key: str) -> Optional[str]:
    value = raw.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _parse_rules(raw: Any) -> Tuple[RecommendationRule, ...]:
    if not isinstance(raw, list):
        return ()
    rules: List[RecommendationRule] = []
    for rule in raw:
        if not isinstance(rule, dict):
            continue
        min_vram = rule.get("min_vram_gb")
        generations = rule.get("generations")
        rules.append(
            RecommendationRule(
                min_vram_gb=float(min_vram) if isinstance(min_vram, (int, float)) and not isinstance(min_vram, bool) else None,
                generations=tuple(g for g in generations or () if isinstance(g, str)) if isinstance(generations, list) else (),
            )
        )
    return tuple(rules)


def parse_recommendation(raw: Any) -> Optional[Recommendation]:
    if not isinstance(raw, dict):
        return None
    name = _optional_str(raw, "name")
    if name is None:
        return None
    size_bytes = raw.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        size_bytes = None
    precision = raw.get("precision")
    return Recommendation(
        name=name,
        provider=_optional_str(raw, "provider"),
        ref=_optional_str(raw, "ref"),
        link=_optional_str(raw, "link"),
        size=_optional_str(raw, "size"),
        size_bytes=size_bytes,
        sha256=(raw.get("sha256").lower() if isinstance(raw.get("sha256"), str) and _SHA256_RE.match(raw["sha256"]) else None),
        precision=precision if precision in PRECISIONS else None,
        recommended_for=_parse_rules(raw.get("recommended_for")),
        default=_optional_bool(raw, "default"),
        gated=_optional_bool(raw, "gated"),
        license_url=_optional_str(raw, "license_url"),
        description=_optional_str(raw, "description"),
    )


def parse_recommendations(raw: Any) -> List[Recommendation]:
    if not isinstance(raw, list):
        return []
    parsed = (parse_recommendation(entry) for entry in raw)
    return [rec for rec in parsed if rec is not None]


def serialize_recommendation(raw: Dict[str, Any]) -> Dict[str, Any]:
    entry = dict(raw)
    size_bytes = entry.get("size_bytes")
    if isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and not entry.get("size"):
        entry["size"] = format_size(size_bytes)
    return entry


def validate_variant_rule(rule: Any, path: str, issues: List[str]) -> None:
    if not isinstance(rule, dict):
        issues.append(f"{path}: must be a mapping")
        return
    unknown = sorted(set(rule) - {"min_vram_gb", "generations"})
    if unknown:
        issues.append(f"{path}: unknown keys {unknown} (allowed: min_vram_gb, generations)")
    if not rule:
        issues.append(f"{path}: must declare 'min_vram_gb' and/or 'generations'")
    min_vram = rule.get("min_vram_gb")
    if min_vram is not None and (
        isinstance(min_vram, bool) or not isinstance(min_vram, (int, float)) or min_vram < 0
    ):
        issues.append(f"{path}: 'min_vram_gb' must be a non-negative number")
    generations = rule.get("generations")
    if generations is not None:
        if not isinstance(generations, list) or not generations:
            issues.append(f"{path}: 'generations' must be a non-empty list")
        else:
            for generation in generations:
                if generation not in GPU_GENERATIONS:
                    issues.append(f"{path}: unknown GPU generation {generation!r} (known: {list(GPU_GENERATIONS)})")


def _is_templated(value: Any) -> bool:
    return isinstance(value, str) and "{{" in value


def lint_recommendations(raw: Any, path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    if raw is None:
        return errors, warnings
    if not isinstance(raw, list):
        return [f"{path}: 'recommendations' must be a list"], warnings
    defaults = 0
    seen_names: Dict[str, str] = {}
    precisions: List[Optional[str]] = []
    for index, entry in enumerate(raw):
        entry_path = f"{path}[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{entry_path}: must be a mapping")
            continue
        name = entry.get("name")
        if isinstance(name, str) and not _is_templated(name):
            key = name.lower()
            if key in seen_names and seen_names[key] != name:
                errors.append(f"{entry_path}: '{name}' collides with '{seen_names[key]}' on a case-insensitive file system")
            seen_names.setdefault(key, name)
        size_bytes = entry.get("size_bytes")
        if size_bytes is not None:
            if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
                errors.append(f"{entry_path}: 'size_bytes' must be a non-negative integer")
            elif entry.get("size") is not None and not _is_templated(entry.get("size")) and not size_text_agrees(entry.get("size"), size_bytes):
                errors.append(
                    f"{entry_path}: 'size' {entry.get('size')!r} does not match 'size_bytes' {size_bytes} within 1%; "
                    "drop 'size' to derive it"
                )
        precision = entry.get("precision")
        if precision is not None and precision not in PRECISIONS:
            errors.append(f"{entry_path}: 'precision' must be one of {list(PRECISIONS)}, got {precision!r}")
        precisions.append(precision if isinstance(precision, str) else None)
        rules = entry.get("recommended_for")
        if rules is not None:
            if not isinstance(rules, list):
                errors.append(f"{entry_path}: 'recommended_for' must be a list")
            else:
                for rule_index, rule in enumerate(rules):
                    validate_variant_rule(rule, f"{entry_path}.recommended_for[{rule_index}]", errors)
        for key in ("default", "gated"):
            value = entry.get(key)
            if value is not None and not isinstance(value, bool):
                errors.append(f"{entry_path}: '{key}' must be true or false")
        if entry.get("default") is True:
            defaults += 1
        license_url = entry.get("license_url")
        if license_url is not None and (not isinstance(license_url, str) or not license_url.strip()):
            errors.append(f"{entry_path}: 'license_url' must be a non-empty string")
        sha256 = entry.get("sha256")
        if sha256 is not None and not _is_templated(sha256) and (not isinstance(sha256, str) or not _SHA256_RE.match(sha256)):
            errors.append(f"{entry_path}: 'sha256' must be 64 hex characters")
        if entry.get("link") and not entry.get("provider") and not sha256:
            warnings.append(f"{entry_path}: a 'link' recommendation should carry a 'sha256'")
    if defaults > 1:
        errors.append(f"{path}: at most one recommendation may be marked 'default: true' (found {defaults})")
    if len(precisions) >= 2 and any(p is None for p in precisions):
        warnings.append(f"{path}: give every recommendation a 'precision' when a field offers two or more")
    return errors, warnings


def find_installed_model(
    model_repository: Any,
    model_type: str,
    filename: str,
    sha256: Optional[str] = None,
    size_bytes: Optional[int] = None,
    digests: Optional[Any] = None,
    hash_candidates: str = "never",
    type_manager: Optional[Any] = None,
) -> Optional[Any]:
    model = model_repository.get_by_identity(model_type, filename)
    if model is not None:
        return model
    nocase = getattr(type(model_repository), "get_by_identity_nocase", None)
    if nocase is not None:
        model = nocase(model_repository, model_type, filename)
        if model is not None:
            return model
    lookup = getattr(model_repository, "get_by_sha256", None)
    if not sha256 or lookup is None:
        return None
    by_hash = lookup(sha256)
    if by_hash is None and digests is not None and size_bytes:
        by_hash = digests.find_by_digest(sha256, size_bytes, hash_candidates).model
    if by_hash is None or not getattr(by_hash, "is_available", True):
        return None
    if by_hash.model_type != model_type and type_manager is not None:
        type_manager.assert_type(sha256, model_type, "recipe")
        by_hash = lookup(sha256) or by_hash
    return by_hash


def any_installed(
    model_repository: Any,
    candidates: Iterable[Dict[str, Any]],
    digests: Optional[Any] = None,
) -> Optional[Any]:
    for candidate in candidates:
        model = find_installed_model(
            model_repository,
            candidate.get("model_type") or "",
            candidate.get("filename") or "",
            sha256=candidate.get("sha256"),
            size_bytes=candidate.get("size_bytes"),
            digests=digests,
        )
        if model is not None and getattr(model, "is_available", True):
            return model
    return None
