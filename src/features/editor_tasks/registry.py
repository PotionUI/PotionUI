import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

CORE_SOURCE = "core"
REMOVE_OBJECT_PROMPT = "Remove the marked object. Fill the area so it matches its surroundings."
REPLACE_BACKGROUND_PROMPT = (
    "Keep the subject exactly as it is, do not change it. Replace everything marked with this background: {prompt}"
)
EXPAND_PROMPT = "Continue the picture beyond its edges so the new area matches it."

Need = Literal["required", "optional", "none"]


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    label: str
    editors: Tuple[str, ...] = ("image",)
    region: Need = "optional"
    prompt: Need = "required"
    produces: Literal["patch", "replace", "matte"] = "patch"
    fallback: Optional[Tuple[str, str]] = None
    source: str = CORE_SOURCE
    icon: str = "edit"
    range: Need = "none"
    result: Literal["replace", "append"] = "replace"
    audio: Literal["keep", "result"] = "keep"
    resizes: bool = False
    grow: float = 0.0
    feather: float = 1.0
    matte: bool = False
    internal: bool = False
    grows: bool = False


VIDEO = ("video",)

CORE_TASKS: Tuple[TaskDefinition, ...] = (
    TaskDefinition(id="edit_region", label="Edit a region", region="optional", prompt="required", icon="edit"),
    TaskDefinition(
        id="remove_object",
        label="Remove an object",
        region="required",
        prompt="none",
        fallback=("edit_region", REMOVE_OBJECT_PROMPT),
        icon="eraser",
        grow=0.04,
        feather=1.6,
    ),
    TaskDefinition(
        id="replace_background",
        label="Replace the background",
        region="required",
        prompt="required",
        fallback=("edit_region", REPLACE_BACKGROUND_PROMPT),
        icon="image",
        matte=True,
    ),
    TaskDefinition(
        id="find_subject",
        label="Find the subject",
        region="none",
        prompt="none",
        produces="matte",
        icon="subject",
        internal=True,
    ),
    TaskDefinition(
        id="expand",
        label="Expand",
        region="none",
        prompt="optional",
        fallback=("edit_region", EXPAND_PROMPT),
        icon="expand",
        grows=True,
    ),
    TaskDefinition(
        id="extend", label="Extend", editors=VIDEO, region="none", prompt="optional", range="optional",
        result="append", audio="result", icon="mode-extend",
    ),
    TaskDefinition(
        id="refine_faces", label="Refine faces", editors=VIDEO, region="none", prompt="none", range="required",
        icon="mode-refine-faces",
    ),
    TaskDefinition(
        id="remix", label="Remix", editors=VIDEO, region="none", prompt="required", range="optional",
        icon="mode-remix",
    ),
    TaskDefinition(
        id="fix_faces", label="Fix faces and hands", region="required", prompt="none", icon="mode-refine-faces",
    ),
)

CORE_TASK_IDS = frozenset(task.id for task in CORE_TASKS)


def _unit(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return default
    return float(value)


class TaskRegistrationError(ValueError):
    pass


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskDefinition] = {}
        self._lock = threading.RLock()

    def register(self, task: TaskDefinition) -> None:
        if task.source == CORE_SOURCE:
            if "." in task.id:
                raise TaskRegistrationError(f"core task id '{task.id}' must not contain a dot")
        elif not task.id.startswith(f"{task.source}."):
            raise TaskRegistrationError(f"task id '{task.id}' must start with '{task.source}.'")
        with self._lock:
            existing = self._tasks.get(task.id)
            if existing is not None and existing.source != task.source:
                raise TaskRegistrationError(f"task '{task.id}' is already registered by '{existing.source}'")
            self._tasks[task.id] = task

    def register_manifest_entry(self, source: str, entry: Mapping[str, Any]) -> None:
        fallback = entry.get("fallback")
        self.register(
            TaskDefinition(
                id=entry["id"],
                label=entry["label"],
                editors=tuple(entry.get("editors") or ("image",)),
                region=entry.get("region") or "optional",
                prompt=entry.get("prompt") or "required",
                produces=entry.get("produces") or "patch",
                fallback=(fallback["task"], fallback["prompt"]) if fallback else None,
                source=source,
                icon=entry.get("icon") or "extension",
                range=entry.get("range") or "none",
                result=entry.get("result") or "replace",
                audio=entry.get("audio") or "keep",
                resizes=bool(entry.get("resizes")),
                grow=_unit(entry.get("grow"), 0.0),
                feather=_unit(entry.get("feather"), 1.0),
                matte=bool(entry.get("matte")),
                internal=bool(entry.get("internal")),
                grows=bool(entry.get("grows")),
            )
        )

    def unregister_source(self, source: str) -> int:
        if source == CORE_SOURCE:
            return 0
        with self._lock:
            gone = [task_id for task_id, task in self._tasks.items() if task.source == source]
            for task_id in gone:
                del self._tasks[task_id]
            return len(gone)

    def get(self, task_id: str) -> Optional[TaskDefinition]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, editor: Optional[str] = None) -> List[TaskDefinition]:
        with self._lock:
            tasks = list(self._tasks.values())
        return [task for task in tasks if editor is None or editor in task.editors]


def register_core_tasks(registry: TaskRegistry) -> TaskRegistry:
    for task in CORE_TASKS:
        registry.register(task)
    return registry
