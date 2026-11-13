import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.features.forms.binding import FormBindingError, bind_form, form_field_index
from src.features.forms.exceptions import FormNotFoundException
from src.features.generation.dto import GenerationRequest
from src.features.generation.prompt_batch import MAX_BATCH_IMAGES
from src.features.kits.expansion import split_kit_rows
from src.features.llm.tools.media_values import missing_media_paths
from src.features.models.exceptions import ModelAccessDeniedException, ModelNotFoundException
from src.features.models.form_refs import collect_model_ids
from src.features.presets.operations.access import preset_available_to_user
from src.features.presets.retired_modes import replacement_for, replacement_note
from src.features.sessions.dto import Session
from src.features.sessions.request import (
    DIRECTOR_REFUSAL,
    DIRECTOR_SUPPORTED,
    RequestRefused,
    director_active,
    mode_payload,
    resolve,
    settings_hash,
)
from src.features.sessions.request_segments import flatten_rich_segments, prompt_list, seed_segments_from_text
from src.features.sessions.request_variables import normalize_variable_def, option_text
from src.features.sessions.version_insight import changed_fields_between, changes_between, trim_preview
from src.platform.database.rows import dt_iso

logger = logging.getLogger(__name__)

KIND_VALUE = "value"
KIND_MEDIA = "media"
KIND_MODEL = "model"
KIND_DISPLAY = "display"
KIND_CONTAINER = "container"

FIELD_KINDS: Dict[str, str] = {
    **{name: KIND_VALUE for name in (
        "string", "textbox", "number", "integer", "boolean", "checkbox", "slider", "stepper", "seed",
        "resolution", "select", "checkbox_group", "tags", "cloud_options", "sampler", "schedule",
        "carousel", "llm", "gate", "prompt_timeline", "camera_shot", "segmented", "anchor", "color",
    )},
    **{name: KIND_MEDIA for name in ("image", "video", "audio", "media", "file")},
    **{name: KIND_MODEL for name in ("model", "models", "lora_picker")},
    **{name: KIND_DISPLAY for name in ("alert", "markdown", "header")},
    **{name: KIND_CONTAINER for name in ("tabs", "tab", "row", "group", "accordion", "section")},
}

SEED_TYPE = "seed"
COUNT_FIELD = "quantity"
OWN_DOCUMENT_KEYS = ("video_director", "music_director", "timeline", "regional_prompt")


def field_kind(field_type: str) -> str:
    return FIELD_KINDS.get(field_type, KIND_VALUE)


class SessionRequestError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}

    def payload(self) -> Dict[str, Any]:
        return {"success": False, "error": self.code, "message": self.message, **self.detail}


@dataclass
class Overrides:
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    prompts: Optional[List[Dict[str, str]]] = None
    variables: Dict[str, str] = field(default_factory=dict)
    seed: Optional[int] = None
    count: Optional[int] = None
    form: Dict[str, Any] = field(default_factory=dict)
    tag_ids: Optional[List[str]] = None
    collection_ids: Optional[List[str]] = None
    idempotency_key: Optional[str] = None
    if_settings_hash: Optional[str] = None


def overrides_from(body: Any) -> Overrides:
    overrides = body.overrides
    return Overrides(
        prompt=overrides.prompt,
        negative_prompt=overrides.negative_prompt,
        prompts=[{"positive": p.positive, "negative": p.negative or ""} for p in overrides.prompts]
        if overrides.prompts is not None
        else None,
        variables=dict(overrides.variables),
        seed=overrides.seed,
        count=overrides.count,
        form=dict(overrides.form),
        tag_ids=body.tag_ids,
        collection_ids=body.collection_ids,
        idempotency_key=body.idempotency_key,
        if_settings_hash=body.if_settings_hash,
    )


@dataclass
class Prepared:
    request: GenerationRequest
    resolved_request: Dict[str, Any]
    session: Dict[str, Any]
    notes: List[str]


def session_modes(session: Session) -> List[str]:
    data = session.data if isinstance(session.data, dict) else {}
    return [mode for mode, entry in data.items() if isinstance(entry, dict)]


def pick_mode(session: Session, requested: Optional[str], preset_vars: Optional[Mapping[str, Any]] = None) -> str:
    modes = session_modes(session)
    if requested:
        if requested not in modes:
            replaced_by = replacement_for(preset_vars, requested)
            raise SessionRequestError(
                404, "session_mode_not_found",
                f"This session has no saved settings for mode '{requested}'.{replacement_note(preset_vars, requested)}",
                {"modes": modes, **({"replaced_by": replaced_by} if replaced_by else {})},
            )
        return requested
    if len(modes) == 1:
        return modes[0]
    if not modes:
        raise SessionRequestError(422, "session_empty", "This session has no saved settings.")
    raise SessionRequestError(
        400, "mode_required", f"This session holds several modes; pass mode as one of: {', '.join(modes)}.",
        {"modes": modes},
    )


class SessionRequests:
    def __init__(
        self,
        sessions: Any,
        versions: Any,
        preset_loader: Any,
        preset_db: Any,
        users: Any,
        models: Any,
        settings: Any,
        prompt_batch: Callable[[Optional[str]], bool],
    ):
        self.sessions = sessions
        self.versions = versions
        self.preset_loader = preset_loader
        self.preset_db = preset_db
        self.users = users
        self.models = models
        self.settings = settings
        self.prompt_batch = prompt_batch

    def owned(self, user_id: str, session_id: str) -> Session:
        session = self.sessions.get_by_id(session_id)
        if session is None or session.user_id != user_id:
            raise SessionRequestError(404, "session_not_found", "Session not found")
        return session

    def listing(
        self, user_id: str, preset_id: Optional[str] = None, name: Optional[str] = None, query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        out = []
        for session in self.sessions.list_for_user(user_id, preset_id, name, query):
            template = self._template(session.preset_id)
            data = session.data if isinstance(session.data, dict) else {}
            modes = session_modes(session)
            out.append(
                {
                    "id": session.id,
                    "name": session.name,
                    "preset_id": session.preset_id,
                    "preset_name": getattr(template, "name", None) if template else None,
                    "modes": modes,
                    "prompts": {mode: _prompt_preview(data[mode]) for mode in modes},
                    "pinned": session.pinned,
                    "updated_at": dt_iso(session.updated_at or session.created_at),
                    "version": session.version,
                }
            )
        return out

    def _template(self, preset_id: str) -> Any:
        try:
            return self.preset_loader.load_preset_by_id(preset_id)
        except Exception:
            logger.warning("could not load preset %s for a session", preset_id, exc_info=True)
            return None

    def _storage_dir(self, user_id: str) -> str:
        try:
            storage = self.settings.get_file_storage_directory(user_id)
        except Exception:
            logger.error("could not resolve the storage root for a session run", exc_info=True)
            storage = None
        if not storage:
            raise SessionRequestError(
                503, "storage_unavailable", "Your storage location could not be resolved, so input files cannot be checked. Try again later."
            )
        return storage

    def _locks(self, preset_id: str, mode: str) -> Dict[str, Any]:
        try:
            return dict(self.preset_db.get_preset_form_overrides(preset_id).get(mode, {}))
        except Exception:
            return {}

    @staticmethod
    def _locked(override: Optional[Mapping[str, Any]]) -> bool:
        return bool(override) and (override.get("editable") is False or override.get("visible") is False)

    def _form_fields(self, template: Any, mode: str, form_name: Optional[str]) -> Dict[str, Any]:
        index = form_field_index(template, mode, form_name)
        return {
            name: spec for name, spec in index.items() if field_kind(spec.type) not in (KIND_DISPLAY, KIND_CONTAINER)
        }

    def _unavailable_models(self, user_id: str, values: Mapping[str, Any], skip: set) -> List[Dict[str, str]]:
        if self.models is None or self.users is None:
            return []
        user = self.users.get_by_id(user_id)
        if user is None:
            return []
        found: List[Dict[str, str]] = []
        for name, value in values.items():
            if name in skip:
                continue
            for model_id in collect_model_ids(value):
                try:
                    self.models.verify_model_access(model_id, user)
                except (ModelNotFoundException, ModelAccessDeniedException):
                    found.append({"field": name, "model_id": model_id, "name": self._model_name(model_id)})
        return found

    def _model_name(self, model_id: str) -> str:
        try:
            model = self.models.model_repo.get_by_id(model_id, include_providers=False)
            return getattr(model, "name", None) or model_id
        except Exception:
            return model_id

    def _missing_inputs(
        self, user_id: str, fields: Mapping[str, Any], values: Mapping[str, Any], skip: set
    ) -> List[Dict[str, str]]:
        storage = self._storage_dir(user_id)
        missing: List[Dict[str, str]] = []
        for name, spec in fields.items():
            if field_kind(spec.type) != KIND_MEDIA or name in skip:
                continue
            for path in missing_media_paths(values.get(name), storage):
                missing.append({"field": name, "path": path})
        return missing

    def _access(self, user_id: str, is_admin: bool, session: Session, template: Any) -> None:
        if not preset_available_to_user(self.preset_db, user_id, session.preset_id, is_admin):
            name = getattr(template, "name", None) or session.preset_id
            raise SessionRequestError(
                403, "session_preset_unavailable",
                f"You no longer have access to the preset '{name}' this session uses.",
                {"preset_id": session.preset_id, "preset_name": name},
            )
        if template is None:
            raise SessionRequestError(404, "preset_not_found", "The preset this session uses is not available")

    def describe(self, user_id: str, is_admin: bool, session_id: str, mode: Optional[str]) -> Dict[str, Any]:
        session = self.owned(user_id, session_id)
        template = self._template(session.preset_id)
        mode = pick_mode(session, mode, getattr(template, "vars", None))
        entry = mode_payload(session.data, mode)
        problems: List[Dict[str, Any]] = []
        available = preset_available_to_user(self.preset_db, user_id, session.preset_id, is_admin)
        if not available:
            name = getattr(template, "name", None) or session.preset_id
            problems.append(
                {"code": "session_preset_unavailable", "preset_id": session.preset_id,
                 "message": f"You no longer have access to the preset '{name}' this session uses."}
            )
        if template is None:
            problems.append(
                {"code": "preset_not_found", "preset_id": session.preset_id,
                 "message": "The preset this session uses is not installed."}
            )
        summary = self._summary(session, template, mode, entry)
        base = {"session": summary, "prompt": self._prompt_block(entry), "shortcuts": {"seed": None, "count": None},
                "fields": [], "resolved_request": None, "problems": problems, "editor_only": None}
        if template is None:
            return base
        vars_ = getattr(template, "vars", None) or {}
        if director_active(vars_, mode):
            base["editor_only"] = {"supported": DIRECTOR_SUPPORTED, "message": DIRECTOR_REFUSAL}
            return base
        form_name = entry.get("selectedVariant") or None
        try:
            fields = self._form_fields(template, mode, form_name)
        except FormNotFoundException:
            problems.append({"code": "mode_not_available", "message": f"The preset no longer has the mode '{mode}' or the saved form variant.{replacement_note(vars_, mode)}"})
            return base
        locks = self._locks(session.preset_id, mode)
        form_values = entry.get("formData") if isinstance(entry.get("formData"), dict) else {}
        base["fields"] = [self._field_entry(name, spec, form_values, locks) for name, spec in fields.items()]
        base["shortcuts"] = {
            "seed": next((name for name, spec in fields.items() if spec.type == SEED_TYPE), None),
            "count": COUNT_FIELD if COUNT_FIELD in fields else None,
        }
        locked = {name for name in fields if self._locked(locks.get(name))}
        for name in sorted(locked):
            default = locks[name].get("default")
            if name in form_values and form_values[name] != default:
                problems.append({"code": "field_locked", "field": name, "message": f"'{name}' is locked by an administrator; the saved value is ignored."})
        for item in self._unavailable_models(user_id, form_values, locked):
            problems.append({"code": "session_model_unavailable", **item,
                             "message": f"The model '{item['name']}' in '{item['field']}' is no longer available to you."})
        try:
            missing = self._missing_inputs(user_id, fields, form_values, locked)
        except SessionRequestError as error:
            missing = []
            problems.append({"code": error.code, "message": error.message})
        for item in missing:
            problems.append({"code": "input_missing", **item, "message": f"The input file {item['path']!r} for '{item['field']}' no longer exists."})
        try:
            resolved = resolve(
                entry, session.preset_id, mode, vars_, None, None, self.prompt_batch(getattr(template, "engine", None))
            )
            base["resolved_request"] = self._sanitized(resolved.request, locked)
        except RequestRefused as refused:
            problems.append({"code": refused.code, "message": refused.message})
        return base

    @staticmethod
    def _sanitized(request: Mapping[str, Any], locked: set) -> Dict[str, Any]:
        clean = {key: copy.deepcopy(value) for key, value in request.items() if key not in ("tab_id", "idempotency_key")}
        clean["form_data"] = {name: value for name, value in (clean.get("form_data") or {}).items() if name not in locked}
        return clean

    def _summary(self, session: Session, template: Any, mode: str, entry: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "id": session.id,
            "name": session.name,
            "preset_id": session.preset_id,
            "preset_name": getattr(template, "name", None) if template else None,
            "modes": session_modes(session),
            "mode": mode,
            "version": session.version,
            "settings_hash": settings_hash(entry),
            "updated_at": dt_iso(session.updated_at or session.created_at),
        }

    @staticmethod
    def _prompt_block(entry: Mapping[str, Any]) -> Dict[str, Any]:
        seeded = seed_segments_from_text({"prompt": entry.get("prompt"), "negativePrompt": entry.get("negativePrompt"),
                                         "promptSegments": entry.get("promptSegments"),
                                         "negativePromptSegments": entry.get("negativePromptSegments")})
        variables = []
        for name, stored in (entry.get("variables") or {}).items():
            definition = normalize_variable_def(stored)
            item: Dict[str, Any] = {"name": name, "type": definition["type"]}
            if definition["type"] == "choice":
                item["options"] = [option_text(o) for o in definition["options"] if option_text(o).strip()]
                item["mode"] = definition["mode"]
            elif definition["type"] == "number":
                item.update(mode=definition.get("mode"), min=definition.get("min"), max=definition.get("max"),
                            step=definition.get("step"), value=definition.get("value"))
            else:
                item["value"] = definition.get("value")
            variables.append(item)
        return {
            "positive": flatten_rich_segments(seeded.get("promptSegments")),
            "negative": flatten_rich_segments(seeded.get("negativePromptSegments")),
            "batch": len(prompt_list(entry)),
            "variables": variables,
        }

    def _field_entry(self, name: str, spec: Any, form_values: Mapping[str, Any], locks: Mapping[str, Any]) -> Dict[str, Any]:
        config = spec.configuration or {}
        kind = field_kind(spec.type)
        locked = self._locked(locks.get(name))
        item: Dict[str, Any] = {
            "name": name, "label": spec.label or name, "type": spec.type, "kind": kind,
            "locked": locked, "media": kind == KIND_MEDIA, "required": bool(spec.required),
        }
        if locked:
            return item
        item["value"] = form_values.get(name, spec.default)
        item["default"] = spec.default
        options = config.get("options")
        if isinstance(options, list) and options:
            item["options"] = [o.get("value") if isinstance(o, dict) else o for o in options]
        for key in ("min", "max", "step"):
            if config.get(key) is not None:
                item[key] = config[key]
        return item

    def prepare(
        self, user_id: str, is_admin: bool, session_id: str, mode: Optional[str], overrides: Overrides
    ) -> Prepared:
        session = self.owned(user_id, session_id)
        template = self._template(session.preset_id)
        mode = pick_mode(session, mode, getattr(template, "vars", None))
        entry = mode_payload(session.data, mode)
        self._access(user_id, is_admin, session, template)
        current = settings_hash(entry)
        if overrides.if_settings_hash and overrides.if_settings_hash != current:
            raise SessionRequestError(
                409, "session_changed",
                "The session's settings changed since the hash you sent.",
                {"settings_hash": current, "changed_fields": self._changed_fields(session, mode, overrides.if_settings_hash)},
            )
        vars_ = getattr(template, "vars", None) or {}
        if director_active(vars_, mode):
            raise SessionRequestError(422, "session_needs_editor", DIRECTOR_REFUSAL, {"supported": DIRECTOR_SUPPORTED})
        form_name = entry.get("selectedVariant") or None
        try:
            fields = self._form_fields(template, mode, form_name)
        except FormNotFoundException as exc:
            raise SessionRequestError(
                422, "mode_not_available",
                f"The preset no longer has the mode '{mode}' or the saved form variant.{replacement_note(vars_, mode)}",
                {"replaced_by": replacement_for(vars_, mode)} if replacement_for(vars_, mode) else None,
            ) from exc
        locks = self._locks(session.preset_id, mode)
        can_batch = self.prompt_batch(getattr(template, "engine", None))
        effective = self._apply(entry, overrides, vars_, mode, fields, locks, can_batch)
        notes = self._ignored_notes(entry, fields)
        form_values = effective.get("formData") or {}
        locked = {name for name in fields if self._locked(locks.get(name))}

        missing_models = self._unavailable_models(user_id, form_values, locked)
        if missing_models:
            names = ", ".join(f"'{item['name']}' in '{item['field']}'" for item in missing_models)
            raise SessionRequestError(
                403, "session_model_unavailable",
                f"The model {names} is no longer available to you; choose another one in the session or override form.<field>.",
                {"models": missing_models},
            )
        missing_inputs = self._missing_inputs(user_id, fields, form_values, locked)
        if missing_inputs:
            first = missing_inputs[0]
            raise SessionRequestError(
                422, "input_missing", f"The input file {first['path']!r} for '{first['field']}' no longer exists; upload it again and pass it as overrides.form.{first['field']}.",
                {"inputs": missing_inputs},
            )

        try:
            resolved = resolve(effective, session.preset_id, mode, vars_, None, None, can_batch)
        except RequestRefused as refused:
            status = 422
            raise SessionRequestError(status, refused.code, refused.message, refused.detail) from refused

        request = resolved.request
        request.pop("tab_id", None)
        if overrides.idempotency_key:
            request["idempotency_key"] = overrides.idempotency_key
        self._validate_form(template, mode, request, user_id, locks, locked)
        snapshot = self._sanitized(request, locked)
        summary = {
            "id": session.id, "mode": mode, "version": session.version, "settings_hash": current,
        }
        return Prepared(
            request=GenerationRequest(**request), resolved_request=snapshot, session=summary, notes=notes + resolved.notes,
        )

    def _validate_form(
        self, template: Any, mode: str, request: Mapping[str, Any], user_id: str, locks: Mapping[str, Any], locked: set
    ) -> None:
        try:
            bind_form(
                template, mode, request.get("form_name"), split_kit_rows(request.get("form_data") or {})[0], user_id,
                storage_dir=self._storage_dir(user_id), field_overrides=dict(locks),
            )
        except FormBindingError as exc:
            raise SessionRequestError(
                422, "form_validation_failed", str(exc),
                {
                    "field_errors": {k: v for k, v in exc.field_errors.items() if k not in locked},
                    "coercions": [c for c in exc.coercions if c.split(":", 1)[0] not in locked],
                    "stripped": [k for k in exc.stripped if k not in locked],
                },
            ) from exc
        except FormNotFoundException as exc:
            raise SessionRequestError(422, "mode_not_available", str(exc)) from exc

    def _changed_fields(self, session: Session, mode: str, expected: str) -> List[str]:
        if self.versions is None:
            return []
        try:
            for version in self.versions.list_for_session(session.id):
                full = self.versions.get(session.id, version.version_number)
                if full is None or not isinstance((full.data or {}).get(mode), dict):
                    continue
                if settings_hash(mode_payload(full.data, mode)) == expected:
                    return sorted(set(changes_between(session.data, full.data)) | set(changed_fields_between(session.data, full.data)))
        except Exception:
            logger.debug("could not diff the session against its history", exc_info=True)
        return []

    @staticmethod
    def _ignored_notes(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> List[str]:
        form = entry.get("formData")
        if not isinstance(form, dict):
            return []
        notes = []
        for key in form:
            if key in fields or key in OWN_DOCUMENT_KEYS or key.endswith("__origin") or key.endswith("_inpaint_mask"):
                continue
            notes.append(f"Ignored saved field '{key}': the preset's form no longer has it.")
        return notes

    def _apply(
        self,
        entry: Mapping[str, Any],
        overrides: Overrides,
        vars_: Mapping[str, Any],
        mode: str,
        fields: Mapping[str, Any],
        locks: Mapping[str, Any],
        can_batch: bool,
    ) -> Dict[str, Any]:
        effective = copy.deepcopy(dict(entry))
        form = dict(effective.get("formData") or {})

        if overrides.prompt is not None and overrides.prompts is not None:
            raise SessionRequestError(422, "conflicting_overrides", "Give either prompt or prompts, not both.")

        if overrides.prompts is not None:
            self._apply_prompts(effective, overrides.prompts, can_batch)
        else:
            if overrides.prompt is not None:
                self._apply_prompt(effective, overrides.prompt, vars_, mode)
            if overrides.negative_prompt is not None:
                self._apply_negative(effective, overrides.negative_prompt)

        if overrides.variables:
            stored = effective.get("variables") if isinstance(effective.get("variables"), dict) else {}
            unknown = sorted(name for name in overrides.variables if name not in stored)
            if unknown:
                raise SessionRequestError(
                    422, "unknown_variable", f"This session has no prompt variable named {', '.join(unknown)}.",
                    {"names": unknown, "known": sorted(stored)},
                )
            for name, value in overrides.variables.items():
                stored[name] = {"type": "text", "value": value}
            effective["variables"] = stored

        if overrides.seed is not None:
            seed_field = next((name for name, spec in fields.items() if spec.type == SEED_TYPE), None)
            if seed_field is None:
                raise SessionRequestError(422, "seed_not_supported", "This session's form has no seed field.")
            form[seed_field] = overrides.seed
        if overrides.count is not None:
            if COUNT_FIELD not in fields:
                raise SessionRequestError(422, "count_not_supported", "This session's form has no quantity field.")
            form[COUNT_FIELD] = overrides.count

        unknown_fields = sorted(name for name in overrides.form if name not in fields)
        if unknown_fields:
            raise SessionRequestError(
                422, "unknown_field", f"This session's form has no field named {', '.join(unknown_fields)}.",
                {"names": unknown_fields, "known": sorted(fields)},
            )
        locked = sorted(name for name in overrides.form if self._locked(locks.get(name)))
        if locked:
            raise SessionRequestError(
                422, "field_locked", f"{', '.join(locked)} {'is' if len(locked) == 1 else 'are'} locked by an administrator.",
                {"names": locked},
            )
        form.update(overrides.form)
        effective["formData"] = form
        if overrides.tag_ids is not None:
            effective["autoTagIds"] = list(overrides.tag_ids)
        if overrides.collection_ids is not None:
            effective["autoCollectionIds"] = list(overrides.collection_ids)
        return effective

    @staticmethod
    def _apply_prompt(effective: Dict[str, Any], text: str, vars_: Mapping[str, Any], mode: str) -> None:
        effective["prompt"] = text
        effective["promptSegments"] = []
        effective.pop("promptTabs", None)
        effective.pop("activePromptTab", None)
        if mode in (vars_.get("prompt_relay_modes") or []):
            relay = effective.get("promptRelay") if isinstance(effective.get("promptRelay"), dict) else {}
            timeline = relay.get("timeline") if isinstance(relay.get("timeline"), dict) else {"duration": 5, "fps": 24}
            effective["promptRelay"] = {**relay, "global_prompt": text, "timeline": {**timeline, "segments": []}}
        else:
            effective.pop("promptRelay", None)

    @staticmethod
    def _apply_negative(effective: Dict[str, Any], text: str) -> None:
        effective["negativePrompt"] = text
        effective["negativePromptSegments"] = []
        tabs = effective.get("promptTabs")
        if isinstance(tabs, list):
            for tab in tabs:
                if isinstance(tab, dict):
                    tab["negativePrompt"] = text
                    tab["negativePromptSegments"] = []

    @staticmethod
    def _apply_prompts(effective: Dict[str, Any], pairs: List[Dict[str, str]], can_batch: bool) -> None:
        if len(pairs) > MAX_BATCH_IMAGES:
            raise SessionRequestError(422, "too_many_prompts", f"At most {MAX_BATCH_IMAGES} prompts per generation.")
        if len(pairs) > 1 and not can_batch:
            raise SessionRequestError(422, "batch_not_supported", "This preset runs one prompt at a time.")
        first = pairs[0]
        effective["prompt"] = first["positive"]
        effective["negativePrompt"] = first.get("negative") or ""
        effective["promptSegments"] = []
        effective["negativePromptSegments"] = []
        effective.pop("promptRelay", None)
        if len(pairs) < 2:
            effective.pop("promptTabs", None)
            effective.pop("activePromptTab", None)
            return
        effective["promptTabs"] = [
            {"id": f"p{index}", "prompt": pair["positive"], "negativePrompt": pair.get("negative") or "",
             "promptSegments": [], "negativePromptSegments": []}
            for index, pair in enumerate(pairs, start=1)
        ]
        effective["activePromptTab"] = 0


def _prompt_preview(slot: Mapping[str, Any]) -> str:
    seeded = seed_segments_from_text(
        {"prompt": slot.get("prompt"), "promptSegments": slot.get("promptSegments")}
    )
    return trim_preview(flatten_rich_segments(seeded.get("promptSegments")))
