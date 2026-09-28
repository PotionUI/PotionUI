from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from src.platform.filesystem.model_roots import ModelRoot, ModelRootResolver, TypeDir, _Snapshot
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY, MODEL_TYPES


class FakeProbe:
    def __init__(self, states: Optional[Dict[str, Tuple[str, Optional[str]]]] = None):
        self._states = states or {}

    def state(self, root: ModelRoot) -> Tuple[str, Optional[str]]:
        return self._states.get(root.id, ("online", None))


def make_roots(
    tmp_path: Path,
    types: Optional[Iterable[str]] = None,
    *,
    root_id: str = "home",
    label: str = "PotionUI models",
    read_only: bool = False,
    case_insensitive: bool = False,
    state: str = "online",
    home_dir: Optional[Path] = None,
) -> ModelRootResolver:
    home = Path(home_dir) if home_dir is not None else tmp_path / "models"
    home.mkdir(parents=True, exist_ok=True)

    root = ModelRoot(
        id=root_id, label=label, path=home, kind="home" if root_id == "home" else "library",
        read_only=read_only, case_insensitive=case_insensitive, state=state, state_reason=None,
        raw_path=str(home),
    )

    by_type: Dict[str, Tuple[TypeDir, ...]] = {}
    for model_type in (types if types is not None else MODEL_TYPES):
        subdir = MODEL_TYPE_TO_DIRECTORY[model_type]
        type_dir_path = home / subdir
        type_dir_path.mkdir(parents=True, exist_ok=True)
        by_type[model_type] = (
            TypeDir(
                root_id=root_id, model_type=model_type, path=type_dir_path,
                position=0, is_write=True, subdir=subdir,
            ),
        )

    resolver = ModelRootResolver(repository=None, probe=FakeProbe({root_id: (state, None)}), base_dir=tmp_path)
    resolver._snapshot = _Snapshot(
        roots=(root,), roots_by_id={root_id: root}, type_dirs_by_type=by_type,
    )
    return resolver
