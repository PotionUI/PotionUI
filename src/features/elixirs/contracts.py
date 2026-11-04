import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.features.elixirs.catalog import Catalog
from src.features.elixirs.vocabulary import MediaType, PortName

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "content" / "elixirs"
CONTRACTS_PATH = CONTENT_ROOT / "marketplace" / "contracts.yml"
LOCAL_CONTRACTS_PATH = CONTENT_ROOT / "local" / "contracts.yml"

logger = logging.getLogger(__name__)


class InputTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["form_field", "video_director_role"]
    field: Optional[str] = None
    role: Optional[Literal["first", "last"]] = None

    @model_validator(mode="after")
    def _one_target(self) -> "InputTarget":
        if self.kind == "form_field" and (not self.field or self.role is not None):
            raise ValueError("a form_field target names a field and no role")
        if self.kind == "video_director_role" and (not self.role or self.field is not None):
            raise ValueError("a video_director_role target names a role and no field")
        return self


class InputPort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    media_type: MediaType
    target: InputTarget


class OutputPort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    media_type: MediaType
    selector: Dict[Literal["is_final", "is_derived"], bool] = Field(
        default_factory=lambda: {"is_final": True, "is_derived": False}
    )


class ModeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    prompt: bool
    quantity_field: Optional[str] = None
    inputs: Dict[PortName, InputPort] = Field(default_factory=dict)
    outputs: Dict[PortName, OutputPort] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ports(self) -> "ModeContract":
        if not self.inputs and not self.outputs:
            raise ValueError("a contract declares at least one input or output")
        clashes = set(self.inputs) & set(self.outputs)
        if clashes:
            raise ValueError(f"port names used for both an input and an output: {sorted(clashes)}")
        return self


class PresetContracts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    modes: Dict[str, ModeContract]


class ContractsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    presets: Dict[str, PresetContracts]


Contracts = Dict[Tuple[str, str], ModeContract]


def parse_contracts(document: dict) -> Contracts:
    parsed = ContractsFile.model_validate(document)
    return {
        (preset_id, mode): contract
        for preset_id, preset in parsed.presets.items()
        for mode, contract in preset.modes.items()
    }


def load_contracts(path: Path = CONTRACTS_PATH) -> Contracts:
    with Path(path).open("r", encoding="utf-8") as handle:
        return parse_contracts(yaml.safe_load(handle) or {})


_cache: Dict[str, Any] = {"key": None, "contracts": None, "error": None}


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def merge_contracts(shipped: Contracts, local: Contracts) -> Contracts:
    return {**shipped, **local}


def current_contracts(shipped_path: Path = CONTRACTS_PATH, local_path: Optional[Path] = None) -> Contracts:
    local_path = LOCAL_CONTRACTS_PATH if local_path is None else local_path
    key = (str(shipped_path), _mtime(shipped_path), str(local_path), _mtime(local_path))
    if _cache["key"] == key:
        return _cache["contracts"]
    shipped = load_contracts(shipped_path)
    local: Contracts = {}
    error = None
    if key[3] is not None:
        try:
            local = load_contracts(local_path)
        except (ValidationError, yaml.YAMLError, OSError) as failure:
            error = f"{local_path.name} was not loaded: {failure}"
            logger.warning("Elixirs local contracts ignored: %s", failure)
    _cache.update(key=key, contracts=merge_contracts(shipped, local), error=error)
    return _cache["contracts"]


def local_contracts_error() -> Optional[str]:
    return _cache["error"]


def lint_contract(preset_id: str, mode: str, contract: ModeContract, catalog: Catalog) -> List[str]:
    preset = catalog.get(preset_id)
    if preset is None:
        return [f"preset {preset_id} is not installed"]
    mode_entry = preset["modes"].get(mode)
    if mode_entry is None:
        return [f"{preset['name']} has no {mode} mode"]
    problems: List[str] = []
    forms = mode_entry["forms"]
    if contract.quantity_field and not any(contract.quantity_field in fields for fields in forms.values()):
        problems.append(f"{preset['name']} {mode} has no {contract.quantity_field} field")
    for port, spec in contract.inputs.items():
        target = spec.target
        if target.kind == "form_field":
            found = [fields[target.field] for fields in forms.values() if target.field in fields]
            if not found:
                problems.append(f"{preset['name']} {mode} has no {target.field} field for {port}")
            elif not all(field["type"] == spec.media_type for field in found):
                problems.append(
                    f"{preset['name']} {mode} field {target.field} does not take {spec.media_type} media"
                )
        elif mode not in preset["director_modes"]:
            problems.append(f"{preset['name']} {mode} does not use the Video Director for {port}")
        elif spec.media_type != "image":
            problems.append(f"{port} binds a Video Director frame, which must be an image")
    return problems


def lint_contracts(contracts: Contracts, catalog: Catalog) -> List[str]:
    problems: List[str] = []
    for (preset_id, mode), contract in sorted(contracts.items()):
        problems.extend(lint_contract(preset_id, mode, contract, catalog))
    return problems
