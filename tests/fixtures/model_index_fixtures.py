from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.features.models.indexer import ModelScanner
from src.features.models.repository import model_repo
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import ModelRoot, ModelRootResolver, TypeDir, _Snapshot, root_path_key
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY
from tests.fixtures.family_shape_fixtures import family_shapes
from tests.fixtures.model_header_fixtures import tensor_specs, write_safetensors

POSITION_OFFSET = 1000


class FakeProbe:
    def __init__(self, states: Optional[Dict[str, Tuple[str, Optional[str]]]] = None):
        self._states = states if states is not None else {}

    def state(self, root: ModelRoot) -> Tuple[str, Optional[str]]:
        return self._states.get(root.id, ("online", None))


def make_resolver(roots: List[ModelRoot], bindings: List[TypeDir]) -> ModelRootResolver:
    resolver = ModelRootResolver(repository=None, probe=FakeProbe(), base_dir=Path.cwd())
    by_type: Dict[str, List[TypeDir]] = {}
    for binding in bindings:
        by_type.setdefault(binding.model_type, []).append(binding)
    resolver._snapshot = _Snapshot(
        roots=tuple(roots),
        roots_by_id={r.id: r for r in roots},
        type_dirs_by_type={t: tuple(sorted(e, key=lambda x: x.position)) for t, e in by_type.items()},
    )
    return resolver


def add_root(root_id: str, path) -> ModelRoot:
    assert root_id != "home"
    key = root_path_key(str(path))
    ModelRootRepository().insert_root(root_id, root_id, str(path), key, "library", False, False, now_iso())
    return ModelRoot(
        id=root_id, label=root_id, path=Path(path), kind="library", read_only=False,
        case_insensitive=False, state="online", state_reason=None, raw_path=str(path),
    )


def add_binding(
    root_id: str, model_type: str, path, position: int, *, scan_headers: bool, subdir: Optional[str] = None
) -> TypeDir:
    subdir = subdir if subdir is not None else MODEL_TYPE_TO_DIRECTORY[model_type]
    real_position = position + POSITION_OFFSET
    binding_id = ModelRootRepository().insert_binding(root_id, model_type, subdir, real_position, False, scan_headers)
    return TypeDir(
        root_id=root_id, model_type=model_type, path=Path(path), position=real_position,
        is_write=False, subdir=subdir, scan_headers=scan_headers, binding_id=binding_id,
    )


FLUX = tensor_specs(family_shapes("flux1"))
SDXL_ALL_IN_ONE = tensor_specs(
    {
        "model.diffusion_model.input_blocks.0.0.weight": (320, 4, 3, 3),
        "model.diffusion_model.input_blocks.4.1.transformer_blocks.0.attn2.to_k.weight": (640, 2048),
        "model.diffusion_model.label_emb.0.0.weight": (1280, 2816),
        "first_stage_model.decoder.a": (4,),
        "first_stage_model.decoder.b": (4,),
        "conditioner.embedders.0.a": (4,),
        "conditioner.embedders.0.b": (4,),
    }
)
LORA = tensor_specs({"lora_unet_a.lora_down.weight": (4, 4), "lora_unet_a.lora_up.weight": (4, 4)})


class Library:
    def __init__(self, tmp_path: Path, *, scan_checkpoints: bool = True, scan_diffusion: bool = False):
        self.base = tmp_path / "lib"
        self.checkpoints = self.base / "Stable-diffusion"
        self.diffusion = self.base / "diffusion_models"
        self.checkpoints.mkdir(parents=True)
        self.diffusion.mkdir(parents=True)
        self.root = add_root("r1", self.base)
        self.scan = {"checkpoint": scan_checkpoints, "diffusion_model": scan_diffusion}
        self.bindings = {
            "checkpoint": add_binding(
                "r1", "checkpoint", self.checkpoints, 0, scan_headers=scan_checkpoints, subdir="Stable-diffusion"
            ),
            "diffusion_model": add_binding(
                "r1", "diffusion_model", self.diffusion, 0, scan_headers=scan_diffusion, subdir="diffusion_models"
            ),
        }
        self.scanner = self.new_scanner()

    def new_scanner(self) -> ModelScanner:
        return ModelScanner(make_resolver([self.root], list(self.bindings.values())))

    def set_scan(self, model_type: str, enabled: bool) -> None:
        ModelRootRepository().set_scan_headers("r1", model_type, enabled)
        binding = self.bindings[model_type]
        self.bindings[model_type] = type(binding)(
            root_id=binding.root_id, model_type=binding.model_type, path=binding.path,
            position=binding.position, is_write=binding.is_write, subdir=binding.subdir, scan_headers=enabled,
            binding_id=binding.binding_id,
        )
        self.scanner = self.new_scanner()

    def put(self, folder: Path, name: str, tensors, tag: str = "") -> Path:
        return write_safetensors(folder / name, tensors, {"tag": tag or name})

    def index(self, **kwargs):
        return self.scanner.index_models(max_workers=1, **kwargs)

    def model(self, filename: str, model_type: str | None = None):
        rows = model_repo.get_by_filename(filename)
        if model_type is not None:
            rows = [r for r in rows if r.model_type == model_type]
        assert len(rows) == 1, [(r.model_type, r.filename) for r in rows]
        return rows[0]


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
