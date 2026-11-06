from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.features.elixirs.catalog import Catalog
from src.features.elixirs.vocabulary import MediaType, PortName


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


Contracts = Dict[Tuple[str, str], ModeContract]


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
