import copy
import secrets
import string
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from pydantic import ValidationError

from src.features.elixirs.catalog import Catalog
from src.features.elixirs.contracts import Contracts, lint_contract
from src.features.elixirs.definitions import NodeRejected, copy_followed, follow_session, import_session, own_settings
from src.features.elixirs.dto import ElixirDefinition
from src.features.elixirs.follow import COPY, FOLLOW, NEW, SESSION_MODES, session_mode_of
from src.features.elixirs.graph import _Graph
from src.features.elixirs.sessions import LAYOUT_KEYS, SessionModeMissing, apply_overrides, mode_payload
from src.features.elixirs.vocabulary import WRITER_INPUTS, WRITER_OUTPUT
from src.features.forms.binding import MEDIA_FIELD_TYPES
from src.features.tab_drafts.validation import variable_errors

OPERATIONS = (
    "add_step", "add_start", "insert_between", "add_writer", "remove", "connect",
    "set_settings", "set_writer", "set_session",
)
WRITER_PORTS = {media_type: port for port, (_, media_type) in WRITER_INPUTS.items()}
FEEDABLE = ("image", "video")
ID_ALPHABET = string.ascii_lowercase + string.digits
PROMPT_RESETS = {"prompt": ("promptSegments", "promptTabs", "activePromptTab"), "negativePrompt": ("negativePromptSegments",)}


class EditRefused(ValueError):
    pass


@dataclass
class SessionWrite:
    node_id: str
    session_id: str
    session_name: str
    mode: str
    base_version: Optional[int]
    payload: Dict[str, Any]


@dataclass
class EditPlan:
    definition: Dict[str, Any]
    changed: bool
    rows: List[Dict[str, Any]] = field(default_factory=list)
    session_writes: Dict[str, SessionWrite] = field(default_factory=dict)


def new_id(prefix: str, taken) -> str:
    taken = set(taken)
    while True:
        candidate = prefix + "".join(secrets.choice(ID_ALPHABET) for _ in range(6))
        if candidate not in taken:
            return candidate


def blank_node(kind: str, node_id: str) -> Dict[str, Any]:
    if kind == "llm":
        return {"id": node_id, "kind": "llm", "title": None, "instruction": "", "target_node_id": None, "pinned": {}}
    return {
        "id": node_id, "kind": "preset", "title": None, "preset_id": None, "mode": None, "form_name": None,
        "overrides": {}, "session_source": None, "session_snapshot": None, "session_mode": NEW,
        "session_ref": None, "session_hash": None,
    }


def usable_modes(catalog: Catalog, contracts: Contracts) -> List[Dict[str, Any]]:
    usable = []
    for (preset_id, mode), contract in contracts.items():
        preset = catalog.get(preset_id)
        if preset is None or lint_contract(preset_id, mode, contract, catalog):
            continue
        usable.append({
            "preset_id": preset_id,
            "preset": preset["name"],
            "mode": mode,
            "mode_label": contract.label,
            "takes": sorted({port.media_type for port in contract.inputs.values()}),
            "makes": sorted({port.media_type for port in contract.outputs.values()}),
            "prompt": contract.prompt,
        })
    usable.sort(key=lambda item: (item["preset"].lower(), item["mode_label"].lower()))
    return usable


def segments_text(segments: Any) -> str:
    if not isinstance(segments, list):
        return ""
    parts = []
    for segment in segments:
        if isinstance(segment, Mapping):
            text = segment.get("content", segment.get("text"))
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return " ".join(parts)


def prompt_of(settings: Mapping[str, Any], key: str = "prompt") -> str:
    segments_key = "promptSegments" if key == "prompt" else "negativePromptSegments"
    text = settings.get(key)
    return segments_text(settings.get(segments_key)) or (text.strip() if isinstance(text, str) else "")


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Planner:
    def __init__(
        self,
        definition: Mapping[str, Any],
        *,
        catalog_for: Callable[[List[str]], Catalog],
        contracts: Contracts,
        read_session: Callable[[str], Optional[Dict[str, Any]]],
        find_session: Callable[[str, str], Optional[Dict[str, Any]]],
    ) -> None:
        self.original = definition
        self.d: Dict[str, Any] = copy.deepcopy(dict(definition))
        self.d.setdefault("nodes", [])
        self.d.setdefault("edges", [])
        self.catalog_for = catalog_for
        self.contracts = contracts
        self.read_session = read_session
        self.find_session = find_session
        self.rows: List[Dict[str, Any]] = []
        self.writes: Dict[str, SessionWrite] = {}
        self.changed = False
        self._catalog: Catalog = {}
        self._usable: Optional[List[Dict[str, Any]]] = None

    def catalog(self) -> Catalog:
        wanted = sorted({node.get("preset_id") for node in self.d["nodes"] if node.get("preset_id")} - set(self._catalog))
        if wanted:
            self._catalog.update(self.catalog_for(wanted))
        return self._catalog

    def usable(self) -> List[Dict[str, Any]]:
        if self._usable is None:
            ids = sorted({preset_id for preset_id, _ in self.contracts})
            self._catalog.update(self.catalog_for(ids))
            self._usable = usable_modes(self._catalog, self.contracts)
        return self._usable

    def graph(self) -> _Graph:
        return _Graph(self.d, self.catalog(), self.contracts)

    def label(self, node: Mapping[str, Any]) -> str:
        return self.graph().label(node)

    def node(self, node_id: Any, what: str = "step") -> Dict[str, Any]:
        found = next((node for node in self.d["nodes"] if node["id"] == node_id), None) if isinstance(node_id, str) else None
        if found is None:
            known = ", ".join(f"{node['id']} ({self.label(node)})" for node in self.d["nodes"]) or "none"
            raise EditRefused(f"There is no {what} '{node_id}'. Steps in this elixir: {known}. Use the ids from get_elixir.")
        return found

    def kind_of(self, node_id: str) -> Optional[str]:
        return next((node.get("kind") for node in self.d["nodes"] if node["id"] == node_id), None)

    def is_text(self, edge: Mapping[str, Any]) -> bool:
        return self.kind_of(edge["source"]) == "llm"

    def file_edge(self, node_id: str) -> Optional[Dict[str, Any]]:
        return next((edge for edge in self.d["edges"] if edge["target"] == node_id and not self.is_text(edge)), None)

    def prompt_edge(self, node_id: str) -> Optional[Dict[str, Any]]:
        texts = [edge for edge in self.d["edges"] if edge["target"] == node_id and self.is_text(edge)]
        return next((edge for edge in texts if edge["target_handle"] == "prompt"), texts[0] if texts else None)

    def parent(self, node_id: str) -> Optional[str]:
        edge = self.prompt_edge(node_id) or self.file_edge(node_id)
        return edge["source"] if edge else None

    def writers_of(self, preset_id: str) -> List[str]:
        return [edge["source"] for edge in self.d["edges"] if edge["target"] == preset_id and self.kind_of(edge["source"]) == "llm"]

    def writer_presets(self, writer_id: str) -> List[str]:
        return list(dict.fromkeys(edge["target"] for edge in self.d["edges"] if edge["source"] == writer_id and self.kind_of(edge["target"]) == "preset"))

    def linked_group(self, node_id: str) -> List[str]:
        kind = self.kind_of(node_id)
        if kind == "llm":
            return [node_id, *self.writer_presets(node_id)]
        if kind == "preset":
            return [node_id, *self.writers_of(node_id)]
        return [node_id]

    def outputs(self, node: Mapping[str, Any]) -> Dict[str, str]:
        return self.graph().outputs(node) or {}

    def inputs(self, node: Mapping[str, Any]) -> Dict[str, str]:
        return self.graph().inputs(node) or {}

    def output_type(self, node_id: str, handle: str) -> Optional[str]:
        return self.outputs(self.node(node_id)).get(handle)

    def reaches(self, edges, start: str, goal: str) -> bool:
        following: Dict[str, List[str]] = {}
        for edge in edges:
            following.setdefault(edge["source"], []).append(edge["target"])
        seen, stack = set(), [start]
        while stack:
            current = stack.pop()
            if current == goal:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(following.get(current, []))
        return False

    def check_connection(self, source: str, source_handle: str, target: str, target_handle: str) -> Optional[str]:
        source_node, target_node = self.node(source), self.node(target)
        if source == target:
            return f"{self.label(source_node)} cannot feed into itself."
        if target_node.get("kind") == "media":
            return f"{self.label(target_node)} holds picked files and cannot take a connection."
        output = self.outputs(source_node).get(source_handle)
        inputs = self.inputs(target_node)
        if output is None:
            return f"{self.label(source_node)} has no {source_handle} output. Its outputs: {', '.join(self.outputs(source_node)) or 'none'}."
        if target_handle not in inputs:
            return f"{self.label(target_node)} has no {target_handle} input. Its inputs: {', '.join(inputs) or 'none'}."
        if inputs[target_handle] != output:
            return f"{self.label(target_node)}'s {target_handle} takes {inputs[target_handle]}s, not {output}s."
        others = [edge for edge in self.d["edges"] if edge["target"] != target]
        if self.reaches(others, target, source):
            return f"Connecting {self.label(source_node)} to {self.label(target_node)} would make a loop; a step can only take from steps to its left."
        return None

    def connect_edge(self, source: str, source_handle: str, target: str, target_handle: str) -> None:
        problem = self.check_connection(source, source_handle, target, target_handle)
        if problem:
            raise EditRefused(problem)
        text = self.kind_of(source) == "llm"
        self.d["edges"] = [
            edge for edge in self.d["edges"]
            if edge["target"] != target or (edge["target_handle"] != target_handle and (text or self.is_text(edge)))
        ]
        self.d["edges"].append({
            "id": new_id("e", [edge["id"] for edge in self.d["edges"]]),
            "source": source, "source_handle": source_handle,
            "target": target, "target_handle": target_handle,
            "target_segment_id": None, "iteration": "each",
        })
        self.changed = True

    def input_for(self, node: Mapping[str, Any], media_type: Optional[str], preferred: Optional[str] = None) -> Optional[str]:
        if node.get("kind") == "llm":
            return WRITER_PORTS.get(media_type)
        inputs = self.inputs(node)
        if preferred and inputs.get(preferred) == media_type:
            return preferred
        return next((port for port, kind in inputs.items() if kind == media_type), None)

    def link_files(self, source: str, source_handle: str, target: str, preferred: Optional[str] = None) -> bool:
        media_type = self.output_type(source, source_handle)
        port = self.input_for(self.node(target), media_type, preferred) if media_type else None
        if port is None or self.check_connection(source, source_handle, target, port):
            return False
        self.connect_edge(source, source_handle, target, port)
        return True

    def insert_node(self, node: Dict[str, Any], before: Optional[str] = None) -> None:
        nodes = self.d["nodes"]
        at = next((index for index, item in enumerate(nodes) if item["id"] == before), None) if before else None
        if at is None:
            nodes.append(node)
        else:
            nodes.insert(at, node)
        self.changed = True

    def taken(self) -> List[str]:
        return [node["id"] for node in self.d["nodes"]]

    def preset_choice(self, op: Mapping[str, Any]) -> Dict[str, Any]:
        preset_id, mode = op.get("preset_id"), op.get("mode")
        if not isinstance(preset_id, str) or not isinstance(mode, str) or not preset_id or not mode:
            raise EditRefused(f"{op.get('op')} needs preset_id and mode. Call get_elixir with include_presets=true to list the preset modes an elixir can use.")
        found = next((item for item in self.usable() if item["preset_id"] == preset_id and item["mode"] == mode), None)
        if found is None:
            same = [item["mode"] for item in self.usable() if item["preset_id"] == preset_id]
            hint = f" That preset can be used in these modes: {', '.join(same)}." if same else ""
            raise EditRefused(
                f"Preset {preset_id} in mode {mode} cannot be used in an elixir.{hint} "
                "Call get_elixir with include_presets=true for the list."
            )
        return found

    def new_preset(self, op: Mapping[str, Any], before: Optional[str] = None) -> Dict[str, Any]:
        choice = self.preset_choice(op)
        node = {**blank_node("preset", new_id("n", self.taken())), "preset_id": choice["preset_id"], "mode": choice["mode"]}
        title = op.get("title")
        if isinstance(title, str) and title.strip():
            node["title"] = title.strip()[:120]
        self.insert_node(node, before)
        return node

    def row(self, step: str, field_name: str, old: Any, new: Any) -> None:
        self.rows.append({"step": step, "field_name": field_name, "old_value": old, "new_value": new})

    def add_step(self, op: Mapping[str, Any]) -> None:
        after = op.get("after")
        if op.get("kind") == "writer":
            raise EditRefused("A prompt writer is added with add_writer (for_step) or add_start (kind 'writer'), not add_step.")
        parent = self.node(after) if after else None
        node = self.new_preset(op)
        label = self.label(node)
        if parent is None:
            self.row(node["id"], "new step", None, f"{label}, starting a new lane")
            return
        if parent.get("kind") == "llm":
            files = self.file_edge(parent["id"])
            if files:
                self.link_files(files["source"], files["source_handle"], node["id"])
            if "prompt" not in self.inputs(node):
                raise EditRefused(f"{label} takes no prompt, so it cannot follow the prompt writer {self.label(parent)}. Pick a mode with a prompt.")
            self.connect_edge(parent["id"], WRITER_OUTPUT, node["id"], "prompt")
            self.row(node["id"], "new step", None, f"{label}, prompted by {self.label(parent)}")
            return
        outputs = self.outputs(parent)
        if not outputs:
            raise EditRefused(f"{self.label(parent)} has no outputs yet; choose its preset before adding a step after it.")
        handle = next(iter(outputs))
        media_type = outputs[handle]
        if not self.link_files(parent["id"], handle, node["id"]):
            raise EditRefused(
                f"{label} takes no {media_type}s, so it cannot follow {self.label(parent)}. "
                f"Pick a preset mode whose inputs take {media_type}s (get_elixir with include_presets=true lists 'takes')."
            )
        writer_note = ""
        if op.get("with_writer"):
            if "prompt" not in self.inputs(node):
                raise EditRefused(f"{label} takes no prompt, so it cannot get a prompt writer. Drop with_writer.")
            writer = blank_node("llm", new_id("n", self.taken()))
            self.insert_node(writer, node["id"])
            self.link_files(parent["id"], handle, writer["id"])
            self.connect_edge(writer["id"], WRITER_OUTPUT, node["id"], "prompt")
            writer_note = f" and a prompt writer ({writer['id']})"
        self.row(node["id"], "new step", None, f"{label} after {self.label(parent)}{writer_note}")

    def add_writer(self, op: Mapping[str, Any]) -> None:
        target = self.node(op.get("for_step"))
        if target.get("kind") != "preset":
            raise EditRefused(f"{self.label(target)} is not a preset step; a prompt writer writes for a preset step.")
        if "prompt" not in self.inputs(target):
            raise EditRefused(f"{self.label(target)} takes no prompt, so a writer cannot write for it.")
        current = self.prompt_edge(target["id"])
        if current:
            raise EditRefused(
                f"{self.label(target)} already gets its prompt from {self.label(self.node(current['source']))} ({current['source']}). "
                "Change that writer with set_writer, or remove it first."
            )
        writer = blank_node("llm", new_id("n", self.taken()))
        self.apply_writer_fields(writer, op)
        self.insert_node(writer, target["id"])
        files = self.file_edge(target["id"])
        if files:
            self.link_files(files["source"], files["source_handle"], writer["id"])
        self.connect_edge(writer["id"], WRITER_OUTPUT, target["id"], "prompt")
        self.row(writer["id"], "new prompt writer", None, f"writes the prompt for {self.label(target)}")

    def add_start(self, op: Mapping[str, Any]) -> None:
        root = self.node(op.get("before"))
        if root.get("kind") == "media" or self.parent(root["id"]) is not None:
            raise EditRefused(
                f"{self.label(root)} is not the first step of its lane. add_start goes before a lane's first step; "
                "use insert_between to put a step between two steps."
            )
        if op.get("kind") == "writer":
            if root.get("kind") != "preset" or "prompt" not in self.inputs(root):
                raise EditRefused(f"{self.label(root)} takes no prompt, so a writer cannot start its lane.")
            writer = blank_node("llm", new_id("n", self.taken()))
            self.apply_writer_fields(writer, op)
            self.insert_node(writer, root["id"])
            self.connect_edge(writer["id"], WRITER_OUTPUT, root["id"], "prompt")
            self.row(writer["id"], "new prompt writer", None, f"starts the lane and writes for {self.label(root)}")
            return
        wanted = self.open_input(root)
        if wanted is None:
            raise EditRefused(f"{self.label(root)} has no free image or video input, so nothing can go before it.")
        port, media_type = wanted
        node = self.new_preset(op, root["id"])
        outputs = self.outputs(node)
        handle = next((name for name, kind in outputs.items() if kind == media_type), None)
        if handle is None and root.get("kind") == "llm":
            handle = next((name for name, kind in outputs.items() if kind in FEEDABLE), None)
        if handle is None or not self.link_files(node["id"], handle, root["id"], port):
            raise EditRefused(
                f"{self.label(node)} makes {', '.join(sorted(set(outputs.values()))) or 'nothing'}, but {self.label(root)} needs {media_type}s. "
                f"Pick a preset mode that makes {media_type}s."
            )
        self.row(node["id"], "new first step", None, f"{self.label(node)} before {self.label(root)}")

    def open_input(self, node: Mapping[str, Any]) -> Optional[Tuple[Optional[str], str]]:
        if node.get("kind") == "llm":
            return None, "image"
        for port, media_type in self.inputs(node).items():
            if media_type in FEEDABLE and not any(edge["target"] == node["id"] and edge["target_handle"] == port for edge in self.d["edges"]):
                return port, media_type
        return None

    def insert_between(self, op: Mapping[str, Any]) -> None:
        child = self.node(op.get("before"))
        files = self.file_edge(child["id"])
        if files is None:
            raise EditRefused(f"{self.label(child)} takes no files from another step; use add_start to put a step before it.")
        parent = self.node(files["source"])
        if parent.get("kind") == "llm" or (child.get("kind") == "preset" and self.writers_of(child["id"])):
            raise EditRefused(
                f"A step cannot go between {self.label(parent)} and {self.label(child)} while a prompt writer is linked to them. "
                "Remove the writer first, or add the step elsewhere."
            )
        takes = self.output_type(files["source"], files["source_handle"])
        makes = takes if child.get("kind") == "llm" else self.inputs(child).get(files["target_handle"])
        node = self.new_preset(op, child["id"])
        label = self.label(node)
        if not self.link_files(files["source"], files["source_handle"], node["id"]):
            raise EditRefused(f"{label} takes no {takes}s, so it cannot take {self.label(parent)}'s output. Pick a mode that takes {takes}s.")
        handle = next((name for name, kind in self.outputs(node).items() if kind == makes), None)
        if handle is None:
            raise EditRefused(f"{label} makes no {makes}s, so it cannot feed {self.label(child)}. Pick a mode that makes {makes}s.")
        for member in self.linked_group(child["id"]):
            before = next((edge for edge in self.d["edges"] if edge["target"] == member and edge["source"] == files["source"] and edge["source_handle"] == files["source_handle"]), None)
            if before is not None:
                self.link_files(node["id"], handle, member, before["target_handle"])
        self.row(node["id"], "new step", None, f"{label} between {self.label(parent)} and {self.label(child)}")

    def remove(self, op: Mapping[str, Any]) -> None:
        node = self.node(op.get("step"))
        label = self.label(node)
        files = self.file_edge(node["id"])
        if files:
            media_type = self.output_type(files["source"], files["source_handle"])
            for edge in list(self.d["edges"]):
                if edge["source"] != node["id"] or self.is_text(edge):
                    continue
                target = self.node(edge["target"])
                port = WRITER_PORTS.get(media_type) if target.get("kind") == "llm" else (
                    edge["target_handle"] if self.inputs(target).get(edge["target_handle"]) == media_type else None
                )
                if port is None:
                    continue
                self.d["edges"] = [item for item in self.d["edges"] if item["id"] != edge["id"]]
                self.d["edges"].append({
                    "id": new_id("e", [item["id"] for item in self.d["edges"]]),
                    "source": files["source"], "source_handle": files["source_handle"],
                    "target": edge["target"], "target_handle": port, "target_segment_id": None, "iteration": "each",
                })
        self.d["nodes"] = [item for item in self.d["nodes"] if item["id"] != node["id"]]
        self.d["edges"] = [edge for edge in self.d["edges"] if node["id"] not in (edge["source"], edge["target"])]
        for item in self.d["nodes"]:
            if item.get("kind") == "llm" and item.get("target_node_id") == node["id"]:
                item["target_node_id"] = None
        self.writes.pop(node["id"], None)
        self.changed = True
        self.row(node["id"], "remove step", label, None)

    def connect(self, op: Mapping[str, Any]) -> None:
        source, target = self.node(op.get("from")), self.node(op.get("to"))
        if source.get("kind") == "llm":
            port = op.get("port") or "prompt"
            self.connect_edge(source["id"], WRITER_OUTPUT, target["id"], port)
            self.row(target["id"], f"{port} from", None, self.label(source))
            return
        outputs = self.outputs(source)
        handle = op.get("from_port") or next(iter(outputs), None)
        if handle is None:
            raise EditRefused(f"{self.label(source)} has no outputs yet; choose its preset first.")
        media_type = outputs.get(handle)
        port = op.get("port") or self.input_for(target, media_type)
        if port is None:
            raise EditRefused(
                f"{self.label(target)} has no input that takes {media_type or handle}s. Its inputs: "
                + (", ".join(f"{name} ({kind})" for name, kind in self.inputs(target).items()) or "none") + "."
            )
        self.connect_edge(source["id"], handle, target["id"], port)
        for partner in self.linked_group(target["id"])[1:]:
            previous = self.file_edge(partner)
            self.link_files(source["id"], handle, partner, previous["target_handle"] if previous else None)
        self.row(target["id"], f"{port} from", None, self.label(source))

    def form_fields(self, node: Mapping[str, Any], effective: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        preset = self.catalog().get(node.get("preset_id") or "")
        if preset is None or node.get("mode") not in preset["modes"]:
            return {}
        mode = preset["modes"][node["mode"]]
        form_name = node.get("form_name") or effective.get("selectedVariant") or mode["default_form"]
        return dict(mode["forms"].get(form_name) or {})

    def followed_settings(self, node: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        ref = node.get("session_ref") or {}
        current = self.read_session(ref.get("id"))
        if current is None:
            raise EditRefused(f"The session {self.label(node)} follows is gone. Switch it to copy or new with set_session first.")
        try:
            settings = mode_payload(current.get("data") or {}, node["mode"])
        except SessionModeMissing:
            raise EditRefused(f"The session {self.label(node)} follows has no settings for {node['mode']}. Switch it to copy or new first.") from None
        return current, settings

    def set_settings(self, op: Mapping[str, Any]) -> None:
        node = self.node(op.get("step"))
        label = self.label(node)
        if node.get("kind") != "preset":
            hint = " Use set_writer for a writer's instruction." if node.get("kind") == "llm" else ""
            raise EditRefused(f"{label} is not a preset step, so it has no settings.{hint}")
        if not node.get("preset_id") or not node.get("mode"):
            raise EditRefused(f"{label} has no preset yet; settings can only be set once a preset is chosen.")
        values = op.get("fields") or {}
        if not isinstance(values, Mapping):
            raise EditRefused("'fields' must be an object of form field names to values, e.g. {\"steps\": 30}.")
        following = session_mode_of(node) == FOLLOW
        current, base = self.followed_settings(node) if following else (None, apply_overrides(node.get("session_snapshot"), node.get("overrides")))
        pending = self.writes.get(node["id"])
        if pending is not None:
            base = {**base, **{key: value for key, value in pending.payload.items() if key not in LAYOUT_KEYS}}
        fields = self.form_fields(node, base)
        changes: Dict[str, Any] = {}
        form_changes: Dict[str, Any] = {}
        old_form = dict(base.get("formData") or {})
        for name, value in values.items():
            spec = fields.get(name)
            if spec is None:
                editable = sorted(key for key, item in fields.items() if item.get("type") not in MEDIA_FIELD_TYPES)
                raise EditRefused(f"{label} has no field '{name}'. Its fields: {', '.join(editable[:40]) or 'none'}. get_step_settings shows them with values.")
            if spec.get("type") in MEDIA_FIELD_TYPES:
                raise EditRefused(f"{label}'s '{name}' takes files; files come from a connection. Use connect to feed it from another step.")
            form_changes[name] = copy.deepcopy(value)
            self.row(node["id"], name, old_form.get(name), value)
        for key, name in (("prompt", "prompt"), ("negativePrompt", "negative_prompt")):
            if name not in op:
                continue
            text = op.get(name)
            if not isinstance(text, str):
                raise EditRefused(f"'{name}' must be text.")
            changes[key] = text
            self.row(node["id"], name, prompt_of(base, key) or None, text)
        if not form_changes and not changes:
            raise EditRefused("set_settings needs 'fields', 'prompt' or 'negative_prompt'. get_step_settings shows what can be set.")
        if following:
            self.follow_write(node, current, pending, form_changes, changes)
            return
        overrides = copy.deepcopy(node.get("overrides") or {})
        if form_changes:
            overrides["formData"] = {**(overrides.get("formData") or {}), **form_changes}
        for key, text in changes.items():
            overrides[key] = text
            for reset in PROMPT_RESETS[key]:
                overrides[reset] = [] if reset.endswith("Segments") else None
        node["overrides"] = overrides
        self.changed = True

    def follow_write(self, node, current, pending, form_changes, changes) -> None:
        payload = copy.deepcopy(pending.payload) if pending else copy.deepcopy((current.get("data") or {}).get(node["mode"]) or {})
        if form_changes:
            payload["formData"] = {**(payload.get("formData") or {}), **form_changes}
        for key, text in changes.items():
            payload[key] = text
            for reset in PROMPT_RESETS[key]:
                if reset.endswith("Segments"):
                    payload[reset] = []
                else:
                    payload.pop(reset, None)
        ref = node.get("session_ref") or {}
        self.writes[node["id"]] = SessionWrite(
            node_id=node["id"],
            session_id=current.get("session_id"),
            session_name=current.get("name"),
            mode=node["mode"],
            base_version=ref.get("version") if ref.get("version") is not None else current.get("version"),
            payload=payload,
        )

    def apply_writer_fields(self, writer: Dict[str, Any], op: Mapping[str, Any]) -> List[Tuple[str, Any, Any]]:
        changed = []
        if "instruction" in op:
            text = op.get("instruction")
            if not isinstance(text, str):
                raise EditRefused("'instruction' must be text.")
            changed.append(("instruction", writer.get("instruction") or None, text))
            writer["instruction"] = text
        if "instruction_chips" in op:
            chips = op.get("instruction_chips")
            if not isinstance(chips, Mapping):
                raise EditRefused("'instruction_chips' must be an object keyed by chip id, the same shape the elixir stores.")
            changed.append(("instruction_chips", len(writer.get("instruction_chips") or {}), len(chips)))
            writer["instruction_chips"] = copy.deepcopy(dict(chips))
        if "variables" in op:
            variables = op.get("variables")
            if not isinstance(variables, Mapping):
                raise EditRefused("'variables' must be an object keyed by variable name, the same shape as Generate's prompt variables.")
            problems = variable_errors(dict(variables))
            if problems:
                raise EditRefused("The writer's variables are not valid: " + "; ".join(problems[:5]) + ".")
            changed.append(("variables", sorted(writer.get("variables") or {}), sorted(variables)))
            writer["variables"] = copy.deepcopy(dict(variables))
        if "writes_for" in op:
            target_id = op.get("writes_for")
            if target_id is not None:
                target = self.node(target_id)
                if target.get("kind") != "preset":
                    raise EditRefused(f"{self.label(target)} is not a preset step; a writer writes for a preset step, or null for the step its text goes to.")
            old = writer.get("target_node_id")
            changed.append(("writes_for", old, target_id))
            writer["target_node_id"] = target_id
        return changed

    def set_writer(self, op: Mapping[str, Any]) -> None:
        writer = self.node(op.get("step"))
        if writer.get("kind") != "llm":
            raise EditRefused(f"{self.label(writer)} is not a prompt writer. set_writer changes a writer's instruction and writes_for.")
        changed = self.apply_writer_fields(writer, op)
        if not changed:
            raise EditRefused("set_writer needs 'instruction', 'instruction_chips', 'variables' or 'writes_for'.")
        for name, old, new in changed:
            self.row(writer["id"], name, old, new)
        self.changed = True

    def set_session(self, op: Mapping[str, Any]) -> None:
        node = self.node(op.get("step"))
        label = self.label(node)
        mode = op.get("mode")
        if mode not in SESSION_MODES:
            raise EditRefused(f"set_session needs mode follow, copy or new, not {mode!r}.")
        if node["id"] in self.writes:
            raise EditRefused(f"{label}'s settings change in this same batch. Change its session mode in a separate edit_elixir call.")
        before = session_mode_of(node) if node.get("kind") == "preset" else None
        target = op.get("session")
        session = self.session_for(node, target) if target else None
        try:
            if mode == FOLLOW:
                if session is None:
                    raise EditRefused(f"Following needs a saved session: pass 'session' with its name or id for {label}.")
                updated = follow_session(self.d, node["id"], session, node.get("mode"))
            elif mode == COPY and session is not None:
                updated = import_session(self.d, node["id"], session, node.get("mode"), "discard")
            elif mode == COPY:
                if before != FOLLOW:
                    raise EditRefused(f"{label} does not follow a session. Pass 'session' to copy one in.")
                updated = copy_followed(self.d, node["id"], self.read_session((node.get("session_ref") or {}).get("id")))
            else:
                updated = own_settings(self.d, node["id"])
        except NodeRejected as rejected:
            raise EditRefused(f"{rejected.message}") from None
        self.d = updated
        self.changed = True
        name = f" ({session.get('name')})" if session else ""
        self.row(node["id"], "session", before, f"{mode}{name}")

    def session_for(self, node: Mapping[str, Any], target: Any) -> Dict[str, Any]:
        if not isinstance(target, str) or not target.strip():
            raise EditRefused("'session' must be a saved session's name or id.")
        found = self.read_session(target) or self.find_session(node.get("preset_id") or "", target.strip())
        if found is None:
            raise EditRefused(f"No saved session '{target}' for {self.label(node)}'s preset. Ask the user which session to use.")
        if found.get("preset_id") != node.get("preset_id"):
            raise EditRefused(f"The session '{found.get('name')}' belongs to another preset than {self.label(node)}.")
        return {**found, "version": found.get("version")}

    def apply(self, operations: List[Any]) -> EditPlan:
        for index, op in enumerate(operations, start=1):
            if not isinstance(op, Mapping):
                raise EditRefused(f"Operation {index} must be an object like {{\"op\": \"set_settings\", ...}}.")
            name = op.get("op")
            if name not in OPERATIONS:
                raise EditRefused(f"Operation {index}: unknown op {name!r}. Use one of: {', '.join(OPERATIONS)}.")
            try:
                getattr(self, name)(op)
            except EditRefused as refused:
                raise EditRefused(f"Operation {index} ({name}): {refused} No change was made.") from None
        definition = self.d
        if self.changed:
            try:
                parsed = ElixirDefinition.model_validate({key: definition.get(key) for key in ("schema_version", "nodes", "edges", "viewport") if key in definition})
            except ValidationError as error:
                first = error.errors()[0]
                where = ".".join(str(part) for part in first.get("loc", ()))
                raise EditRefused(f"The edited elixir is not valid ({where}: {first.get('msg')}). No change was made.") from None
            definition = {
                "schema_version": parsed.schema_version,
                "nodes": [node.model_dump() for node in parsed.nodes],
                "edges": [edge.model_dump() for edge in parsed.edges],
                "viewport": parsed.viewport.model_dump() if parsed.viewport else None,
            }
        return EditPlan(definition=definition, changed=self.changed, rows=self.rows, session_writes=dict(self.writes))
