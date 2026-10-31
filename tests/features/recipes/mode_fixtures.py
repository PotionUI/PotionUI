from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate

GIB = 1024 ** 3


def rec(name: str, size_bytes: Optional[int] = None, precision: Optional[str] = None, **extra) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "name": name,
        "provider": "huggingface",
        "ref": '{"repo": "org/repo", "file": "%s", "revision": "main"}' % name,
    }
    if size_bytes is not None:
        entry["size_bytes"] = size_bytes
    if precision is not None:
        entry["precision"] = precision
    entry.update(extra)
    return entry


def model_field(name: str, model_type: str, recs: List[Dict[str, Any]], required: bool = True, label: Optional[str] = None, default=None) -> FieldTemplate:
    return FieldTemplate(
        type="model",
        name=name,
        label=label or name.replace("_", " ").title(),
        required=required,
        default=default,
        configuration={"model_type": model_type, "recommendations": recs},
    )


def mode(fields: List[FieldTemplate], requirements=(), label: Optional[str] = None, short: Optional[str] = None) -> ModeTemplate:
    tab = FieldTemplate(type="tabs", children=[FieldTemplate(type="tab", label="Models", children=list(fields))])
    return ModeTemplate(
        forms=[FormTemplate(name="default", fields=[tab], short_description=short, requirements=list(requirements))],
        pipes=[],
        mode_label=label,
    )


def preset(modes: Dict[str, ModeTemplate], preset_id: str = "P1", name: str = "Demo", engine: str = "native") -> PresetTemplate:
    return PresetTemplate(id=preset_id, name=name, version="1", path=f"/presets/{name}", modes=modes, engine=engine, category="video")


def video_preset() -> PresetTemplate:
    dit = [
        rec("dit-int8.safetensors", 20 * GIB, "int8", recommended_for=[{"generations": ["ampere", "ada", "hopper"]}]),
        rec("dit-nvfp4.safetensors", 17 * GIB, "nvfp4", recommended_for=[{"generations": ["blackwell"]}]),
        rec("dit-bf16.safetensors", 40 * GIB, "bf16", recommended_for=[{"min_vram_gb": 48}]),
    ]
    encoder = [rec("te-int8.safetensors", 14 * GIB, "int8")]
    vae = [rec("vae.safetensors", 1 * GIB, "bf16")]
    audio = [rec("audio.safetensors", 300 * 1024 ** 2, "bf16")]
    base = [
        model_field("model", "diffusion_model", dit, label="Main model"),
        model_field("text_encoder", "text_encoder", encoder),
        model_field("video_vae", "vae", vae),
        model_field("audio_vae", "vae", audio, required=False),
    ]
    cine = base[:3] + [model_field("effect_lora", "lora", [rec("cine.safetensors", 200 * 1024 ** 2)], label="Cinemagraph LoRA")]
    water = base[:3] + [model_field("effect_lora", "lora", [rec("water.safetensors", 900 * 1024 ** 2)], label="Water LoRA")]
    restore = base[:3] + [
        model_field("restore_lora", "lora", [rec("restore.safetensors", 100)], required=False),
        model_field("deblur_lora", "lora", [rec("deblur.safetensors", 200)], required=False),
    ]
    picker = FieldTemplate(type="lora_picker", name="loras", configuration={})
    control = base[:3] + [
        model_field("control_model", "model_patch", [rec("ctrl-int8.safetensors", 4 * GIB, "int8"), rec("ctrl-bf16.safetensors", 8 * GIB, "bf16")], required=False),
        model_field("own_upscaler", "upscaler", [], required=True),
        picker,
    ]
    return preset(
        {
            "video": mode(base, label="Video", short="Text or image to video"),
            "cinemagraph": mode(cine, requirements=[{"type": "model", "filename": "cine.safetensors"}], label="Cinemagraph"),
            "add_water": mode(water, label="Add water"),
            "restore": mode(restore, requirements=[{"type": "model", "any_of": ["restore_lora", "deblur_lora"]}], label="Restore"),
            "motion": mode(control, requirements=[{"type": "model", "field": "control_model"}], label="Motion transfer"),
        },
        preset_id="VID",
        name="Vid-1.0",
    )


class FakeLoader:
    def __init__(self, *presets: PresetTemplate):
        self.presets = list(presets)

    def load_preset_by_id(self, preset_id):
        return next((p for p in self.presets if p.id == preset_id), None)

    def all(self):
        return list(self.presets)


class FakeModelRepo:
    def __init__(self, files=(), by_sha=None):
        self.models = {}
        for index, (model_type, filename) in enumerate(files):
            self.add(model_type, filename, f"m{index}")
        self.by_sha = dict(by_sha or {})
        self.deleted = []

    def add(self, model_type, filename, model_id=None, size=None, available=True):
        model = SimpleNamespace(
            id=model_id or f"m{len(self.models)}", model_type=model_type, filename=filename,
            file_size=size, is_available=available, sha256=None,
        )
        self.models[(model_type, filename)] = model
        return model

    def get_by_identity(self, model_type, filename, include_providers=True):
        return self.models.get((model_type, filename))

    def get_by_sha256(self, sha256, include_providers=True):
        return self.by_sha.get(sha256)

    def get_by_filename(self, filename):
        return [m for (t, f), m in self.models.items() if f == filename]

    def get_by_id(self, model_id, include_providers=True):
        return next((m for m in self.models.values() if m.id == model_id), None)

    def delete(self, model_id):
        self.deleted.append(model_id)
        for key, model in list(self.models.items()):
            if model.id == model_id:
                del self.models[key]
        return True


class FakeDbRepo:
    def __init__(self, overrides=None, installed=("VID",)):
        self.overrides = {k: dict(v) for k, v in (overrides or {}).items()}
        self.installed = set(installed)
        self.writes = []

    def get_preset_form_overrides(self, preset_id):
        return {mode: dict(fields) for mode, fields in self.overrides.get(preset_id, {}).items()}

    def set_preset_form_overrides(self, preset_id, overrides):
        self.writes.append((preset_id, overrides))
        self.overrides[preset_id] = overrides

    def is_preset_installed(self, preset_id):
        return preset_id in self.installed

    def get_all_installed_presets(self):
        return [SimpleNamespace(preset_id=p) for p in sorted(self.installed)]
