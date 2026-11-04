from dataclasses import asdict, dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from src.features.elixirs.brew import prompts_per_input
from src.features.elixirs.catalog import Catalog
from src.features.elixirs.contracts import Contracts, ModeContract, lint_contract
from src.features.elixirs.follow import NEW, session_mode_of
from src.features.elixirs.identity import asset_key
from src.features.elixirs.limits import Limits
from src.features.elixirs.sessions import apply_overrides
from src.features.elixirs.vocabulary import (
    PROMPT_INPUTS,
    TEXT,
    WRITER_INPUTS,
    WRITER_OUTPUT,
)
from src.features.elixirs.vocabulary import node_label as plain_label

MEDIA_SOURCE_PORTS = {"image": "images", "video": "videos"}


def prompt_inputs(contract: Optional[ModeContract]) -> Dict[str, str]:
    if contract is None or not contract.prompt:
        return {}
    return {name: label for name, label in PROMPT_INPUTS.items() if name not in contract.inputs and name not in contract.outputs}


def is_text_edge(nodes_by_id: Mapping[str, Mapping[str, Any]], edge: Mapping[str, Any]) -> bool:
    return (nodes_by_id.get(edge["source"]) or {}).get("kind") == "llm"


def writer_target(nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]], node: Mapping[str, Any]) -> Optional[str]:
    by_id = {item["id"]: item for item in nodes}
    explicit = node.get("target_node_id")
    if explicit:
        return explicit if (by_id.get(explicit) or {}).get("kind") == "preset" else None
    for edge in edges:
        if edge["source"] == node["id"] and (by_id.get(edge["target"]) or {}).get("kind") == "preset":
            return edge["target"]
    return None


def driving_edges(nodes_by_id: Mapping[str, Mapping[str, Any]], incoming: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    media = [edge for edge in incoming if not is_text_edge(nodes_by_id, edge)]
    if media:
        return media
    texts = sorted(incoming, key=lambda edge: edge["target_handle"] != "prompt")
    return texts[:1]


def linked_edge_ids(nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]], edge: Mapping[str, Any]) -> List[str]:
    edges = list(edges)
    kinds = {node["id"]: node.get("kind") for node in nodes}
    linked = []
    for other in edges:
        if other["id"] == edge["id"] or other["source"] != edge["source"] or other["source_handle"] != edge["source_handle"]:
            continue
        pair = (kinds.get(edge["target"]), kinds.get(other["target"]))
        if pair == ("llm", "preset"):
            writer_id, preset_id = edge["target"], other["target"]
        elif pair == ("preset", "llm"):
            writer_id, preset_id = other["target"], edge["target"]
        else:
            continue
        if any(item["source"] == writer_id and item["target"] == preset_id for item in edges):
            linked.append(other["id"])
    return linked


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "error"
    node_id: Optional[str] = None
    edge_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _join(labels: Iterable[str]) -> str:
    labels = list(labels)
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _has_settings(node: Mapping[str, Any]) -> bool:
    if node.get("session_snapshot") is not None:
        return True
    return session_mode_of(node) == NEW and bool(node.get("overrides"))


def _quantity(form_data: Mapping[str, Any], field: Optional[str]) -> int:
    if not field:
        return 1
    value = form_data.get(field)
    if isinstance(value, bool):
        return 1
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return number if number >= 1 else 1


class _Graph:
    def __init__(self, definition: Mapping[str, Any], catalog: Catalog, contracts: Contracts):
        self.catalog = catalog
        self.contracts = contracts
        self.nodes: List[Mapping[str, Any]] = list(definition.get("nodes") or [])
        self.edges: List[Mapping[str, Any]] = list(definition.get("edges") or [])
        self.by_id: Dict[str, Mapping[str, Any]] = {}
        self.order: Dict[str, int] = {}
        for index, node in enumerate(self.nodes):
            if node["id"] not in self.by_id:
                self.by_id[node["id"]] = node
                self.order[node["id"]] = index

    def label(self, node: Mapping[str, Any]) -> str:
        if (node.get("title") or "").strip() or node.get("kind") != "preset":
            return plain_label(node)
        contract = self.contract(node)
        if contract is None:
            return plain_label(node)
        return f"{self.catalog[node['preset_id']]['name']} · {contract.label}"

    def contract(self, node: Mapping[str, Any]) -> Optional[ModeContract]:
        if node.get("kind") != "preset":
            return None
        preset = self.catalog.get(node.get("preset_id") or "")
        if preset is None or node.get("mode") not in preset["modes"]:
            return None
        return self.contracts.get((node["preset_id"], node["mode"]))

    def outputs(self, node: Mapping[str, Any]) -> Optional[Dict[str, str]]:
        if node.get("kind") == "media":
            media_type = node.get("media_type")
            return {MEDIA_SOURCE_PORTS[media_type]: media_type} if media_type else None
        if node.get("kind") == "llm":
            return {WRITER_OUTPUT: TEXT}
        contract = self.contract(node)
        if contract is None:
            return None
        return {name: port.media_type for name, port in contract.outputs.items()}

    def inputs(self, node: Mapping[str, Any]) -> Optional[Dict[str, str]]:
        if node.get("kind") == "media":
            return {}
        if node.get("kind") == "llm":
            return {name: media_type for name, (_, media_type) in WRITER_INPUTS.items()}
        contract = self.contract(node)
        if contract is None:
            return None
        ports = {name: port.media_type for name, port in contract.inputs.items()}
        ports.update({name: TEXT for name in prompt_inputs(contract)})
        return ports

    def input_label(self, node: Mapping[str, Any], port: str) -> str:
        if node.get("kind") == "llm":
            return WRITER_INPUTS.get(port, (port, None))[0]
        contract = self.contract(node)
        if contract is not None and port in contract.inputs:
            return contract.inputs[port].label
        return PROMPT_INPUTS.get(port, port)

    def effective(self, node: Mapping[str, Any]) -> Dict[str, Any]:
        return apply_overrides(node.get("session_snapshot"), node.get("overrides"))

    def form_name(self, node: Mapping[str, Any], effective: Mapping[str, Any]) -> Optional[str]:
        preset = self.catalog[node["preset_id"]]
        mode = preset["modes"][node["mode"]]
        return node.get("form_name") or effective.get("selectedVariant") or mode["default_form"]


def field_is_multi(catalog: Catalog, node: Mapping[str, Any], field: str) -> bool:
    preset = (catalog or {}).get(node.get("preset_id") or "")
    if not preset or node.get("mode") not in preset["modes"]:
        return False
    effective = apply_overrides(node.get("session_snapshot"), node.get("overrides"))
    mode = preset["modes"][node["mode"]]
    form_name = node.get("form_name") or effective.get("selectedVariant") or mode["default_form"]
    spec = (mode["forms"].get(form_name) or {}).get(field) or {}
    return bool(spec.get("multi"))


def _check_sizes(graph: _Graph, limits: Limits, issues: List[Issue]) -> None:
    if len(graph.nodes) > limits.max_nodes:
        issues.append(Issue("too_many_nodes", f"An elixir can hold at most {limits.max_nodes} nodes; this one has {len(graph.nodes)}."))
    if len(graph.edges) > limits.max_edges:
        issues.append(Issue("too_many_edges", f"An elixir can hold at most {limits.max_edges} connections; this one has {len(graph.edges)}."))


def _check_ids(graph: _Graph, issues: List[Issue]) -> List[Mapping[str, Any]]:
    seen_nodes = set()
    for node in graph.nodes:
        if node["id"] in seen_nodes:
            issues.append(Issue("duplicate_node_id", f'Two nodes share the id "{node["id"]}".', node_id=node["id"]))
        seen_nodes.add(node["id"])
    unique_edges = []
    seen_edges = set()
    for edge in graph.edges:
        if edge["id"] in seen_edges:
            issues.append(Issue("duplicate_edge_id", f'Two connections share the id "{edge["id"]}".', edge_id=edge["id"]))
            continue
        seen_edges.add(edge["id"])
        unique_edges.append(edge)
    return unique_edges


def _check_media_node(
    graph: "_Graph",
    node: Mapping[str, Any],
    limits: Limits,
    unavailable: Mapping[str, Sequence[Tuple[int, str]]],
    issues: List[Issue],
) -> None:
    label = graph.label(node)
    assets = list(node.get("assets") or [])
    if not assets:
        issues.append(Issue("no_media", f"{label} has no files yet. Choose media for it.", node_id=node["id"]))
        return
    if len(assets) > limits.max_media_per_source:
        issues.append(Issue(
            "too_many_media",
            f"{label} holds {len(assets)} files; a media source can hold at most {limits.max_media_per_source}.",
            node_id=node["id"],
        ))
    keys = [asset_key(asset) for asset in assets]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        issues.append(Issue("duplicate_media", f"{label} lists the same file more than once.", node_id=node["id"]))
    media_type = node.get("media_type")
    if not media_type:
        issues.append(Issue("media_type_missing", f"{label} does not say whether it holds images or videos.", node_id=node["id"]))
    else:
        mismatched = [asset for asset in assets if asset.get("media_type") and asset.get("media_type") != media_type]
        if mismatched:
            issues.append(Issue(
                "mixed_media",
                f"{label} holds {media_type}s, but {len(mismatched)} of its files are not {media_type}s. Use a separate media source for them.",
                node_id=node["id"],
            ))
    for index, reason in unavailable.get(node["id"], ()):
        issues.append(Issue(
            "unavailable_media",
            f"{label}: file {index + 1} is no longer available ({reason}). Choose it again or remove it.",
            node_id=node["id"],
        ))


def _check_preset_node(graph: _Graph, node: Mapping[str, Any], issues: List[Issue]) -> None:
    label = graph.label(node)
    preset_id = node.get("preset_id")
    if not preset_id:
        issues.append(Issue("no_preset", f"{label} has no preset yet. Choose a saved session for it.", node_id=node["id"]))
        return
    preset = graph.catalog.get(preset_id)
    if preset is None:
        issues.append(Issue("unknown_preset", f"{label} uses a preset that is not installed.", node_id=node["id"]))
        return
    mode = node.get("mode")
    if mode not in preset["modes"]:
        issues.append(Issue("unknown_mode", f"{label} uses a {preset['name']} mode that no longer exists ({mode}).", node_id=node["id"]))
        return
    contract = graph.contracts.get((preset_id, mode))
    if contract is None:
        issues.append(Issue(
            "no_contract",
            f"{label}: {preset['name']} ({mode}) cannot be used in an elixir yet; its inputs and outputs are not declared.",
            node_id=node["id"],
        ))
        return
    for problem in lint_contract(preset_id, mode, contract, graph.catalog):
        issues.append(Issue(
            "contract_out_of_date",
            f"{label}: {preset['name']} changed and its elixir inputs no longer match ({problem}).",
            node_id=node["id"],
        ))
    if not _has_settings(node):
        issues.append(Issue("no_session", f"{label} has no saved session yet. Choose one for it.", node_id=node["id"]))
        return
    form_name = graph.form_name(node, graph.effective(node))
    if form_name not in preset["modes"][mode]["forms"]:
        issues.append(Issue("unknown_form", f"{label} uses a {preset['name']} form that no longer exists ({form_name}).", node_id=node["id"]))


def _check_edges(graph: _Graph, edges: List[Mapping[str, Any]], issues: List[Issue]) -> List[Mapping[str, Any]]:
    structural = []
    seen = set()
    for edge in edges:
        source = graph.by_id.get(edge["source"])
        target = graph.by_id.get(edge["target"])
        missing = [name for name, node in ((edge["source"], source), (edge["target"], target)) if node is None]
        if missing:
            issues.append(Issue(
                "unknown_edge_endpoint",
                f'A connection points at a node that does not exist ({_join(missing)}). Remove it or reconnect it.',
                edge_id=edge["id"],
            ))
            continue
        if edge["source"] == edge["target"]:
            issues.append(Issue("self_connection", f"{graph.label(source)} cannot feed into itself.", node_id=source["id"], edge_id=edge["id"]))
            continue
        if target.get("kind") == "media":
            issues.append(Issue(
                "media_source_has_input",
                f"{graph.label(target)} is a media source and cannot receive a connection.",
                node_id=target["id"],
                edge_id=edge["id"],
            ))
            continue
        signature = (edge["source"], edge["source_handle"], edge["target"], edge["target_handle"])
        if signature in seen:
            issues.append(Issue(
                "duplicate_connection",
                f"{graph.label(source)} is connected to {graph.label(target)} twice in the same way.",
                node_id=target["id"],
                edge_id=edge["id"],
            ))
            continue
        seen.add(signature)
        structural.append(edge)
        outputs = graph.outputs(source)
        inputs = graph.inputs(target)
        source_type = outputs.get(edge["source_handle"]) if outputs is not None else None
        target_type = inputs.get(edge["target_handle"]) if inputs is not None else None
        if outputs is not None and source_type is None:
            issues.append(Issue(
                "unknown_output",
                f"{graph.label(source)} has no {edge['source_handle']} output to connect to {graph.label(target)}.",
                node_id=source["id"],
                edge_id=edge["id"],
            ))
        if inputs is not None and target_type is None:
            issues.append(Issue(
                "unknown_input",
                f"{graph.label(target)} has no {edge['target_handle']} input.",
                node_id=target["id"],
                edge_id=edge["id"],
            ))
        if source_type and target_type and source_type != target_type:
            issues.append(Issue(
                "incompatible_media",
                f"{graph.label(source)} sends {source_type}s, but {graph.label(target)} expects {target_type}s for {edge['target_handle']}.",
                node_id=target["id"],
                edge_id=edge["id"],
            ))
    return structural


def _check_fan(graph: _Graph, edges: List[Mapping[str, Any]], limits: Limits, issues: List[Issue]) -> None:
    outgoing: Dict[str, int] = {}
    incoming: Dict[str, List[Mapping[str, Any]]] = {}
    texts: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for edge in edges:
        outgoing[edge["source"]] = outgoing.get(edge["source"], 0) + 1
        if is_text_edge(graph.by_id, edge):
            texts.setdefault((edge["target"], edge["target_handle"]), []).append(edge)
        else:
            incoming.setdefault(edge["target"], []).append(edge)
    for node_id in sorted(outgoing, key=graph.order.get):
        if outgoing[node_id] > limits.max_branches:
            node = graph.by_id[node_id]
            issues.append(Issue(
                "too_many_branches",
                f"{graph.label(node)} feeds {outgoing[node_id]} nodes; one node can feed at most {limits.max_branches}.",
                node_id=node_id,
            ))
    for node_id in sorted(incoming, key=graph.order.get):
        if len(incoming[node_id]) > 1:
            node = graph.by_id[node_id]
            sources = _join(graph.label(graph.by_id[edge["source"]]) for edge in incoming[node_id])
            issues.append(Issue(
                "too_many_inputs",
                f"{graph.label(node)} can take only one connected input; it is connected to {sources}. Disconnect all but one.",
                node_id=node_id,
            ))
    for (node_id, port), connected in sorted(texts.items(), key=lambda item: (graph.order.get(item[0][0]), item[0][1])):
        if len(connected) > 1:
            node = graph.by_id[node_id]
            sources = _join(graph.label(graph.by_id[edge["source"]]) for edge in connected)
            issues.append(Issue(
                "too_many_inputs",
                f"{graph.label(node)} takes its {graph.input_label(node, port).lower()} from one connection only; it is connected to {sources}. Disconnect all but one.",
                node_id=node_id,
            ))


def _check_pairing(graph: _Graph, edges: List[Mapping[str, Any]], issues: List[Issue]) -> None:
    incoming: Dict[str, List[Mapping[str, Any]]] = {}
    for edge in edges:
        incoming.setdefault(edge["target"], []).append(edge)
    for node_id in sorted(incoming, key=graph.order.get):
        node = graph.by_id[node_id]
        media = [edge for edge in incoming[node_id] if not is_text_edge(graph.by_id, edge)]
        if node.get("kind") != "preset" or len(media) != 1:
            continue
        files = media[0]
        for edge in incoming[node_id]:
            if not is_text_edge(graph.by_id, edge):
                continue
            writer = graph.by_id[edge["source"]]
            fed = [item for item in incoming.get(writer["id"], []) if not is_text_edge(graph.by_id, item)]
            if not fed or (fed[0]["source"], fed[0]["source_handle"]) == (files["source"], files["source_handle"]):
                continue
            issues.append(Issue(
                "text_not_paired",
                f"{graph.label(node)} gets its files from {graph.label(graph.by_id[files['source']])}, but {graph.label(writer)} writes one prompt per file of "
                f"{graph.label(graph.by_id[fed[0]['source']])}. Feed both from the same node, so each file gets its own prompt.",
                node_id=node_id,
                edge_id=edge["id"],
            ))


def _check_writers(graph: _Graph, edges: List[Mapping[str, Any]], writer: Optional[Callable[[Optional[str]], Mapping[str, Any]]], issues: List[Issue]) -> None:
    for node in graph.by_id.values():
        if node.get("kind") != "llm":
            continue
        label = graph.label(node)
        if not (node.get("instruction") or "").strip():
            issues.append(Issue("no_instruction", f"{label} has no instruction yet. Say what it should write.", node_id=node["id"]))
        explicit = node.get("target_node_id")
        if explicit and (graph.by_id.get(explicit) or {}).get("kind") != "preset":
            issues.append(Issue(
                "unknown_target",
                f"{label} writes for a preset node that is no longer on the canvas. Choose another one.",
                node_id=node["id"],
            ))
        elif writer_target(graph.nodes, edges, node) is None:
            issues.append(Issue(
                "no_target",
                f"{label} does not know which preset it writes for, so its prompt is not tuned to a model. Connect its text to a preset or choose one in its settings.",
                severity="warning",
                node_id=node["id"],
            ))
        if writer is None:
            continue
        status = writer(node.get("llm_id"))
        if not status.get("available"):
            issues.append(Issue("no_llm", f"{label} cannot write: {status.get('message') or 'no language model is set up.'}", node_id=node["id"]))
        elif not status.get("vision") and any(edge["target"] == node["id"] for edge in edges):
            issues.append(Issue(
                "llm_cannot_see",
                f"{label} gets files, but the language model {status.get('name')} cannot look at images. Choose a model with vision support, or disconnect the files.",
                node_id=node["id"],
            ))


def _strongly_connected(node_ids: List[str], edges: List[Mapping[str, Any]]) -> List[List[str]]:
    successors: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        successors[edge["source"]].append(edge["target"])
    index_of: Dict[str, int] = {}
    low: Dict[str, int] = {}
    stack: List[str] = []
    on_stack = set()
    components: List[List[str]] = []
    counter = 0
    for root in node_ids:
        if root in index_of:
            continue
        work = [(root, 0)]
        while work:
            node_id, child = work.pop()
            if child == 0:
                index_of[node_id] = low[node_id] = counter
                counter += 1
                stack.append(node_id)
                on_stack.add(node_id)
            children = successors[node_id]
            if child < len(children):
                work.append((node_id, child + 1))
                nxt = children[child]
                if nxt not in index_of:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node_id] = min(low[node_id], index_of[nxt])
                continue
            if low[node_id] == index_of[node_id]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node_id:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node_id])
    return components


def _check_cycles(graph: _Graph, edges: List[Mapping[str, Any]], issues: List[Issue]) -> bool:
    node_ids = list(graph.by_id)
    cyclic = False
    for component in _strongly_connected(node_ids, edges):
        if len(component) < 2:
            continue
        cyclic = True
        members = sorted(component, key=graph.order.get)
        issues.append(Issue(
            "cycle",
            f"These nodes feed each other in a loop: {_join(graph.label(graph.by_id[m]) for m in members)}. Remove one of the connections.",
            node_id=members[0],
        ))
    return cyclic


DIRECTOR_SHAPE_FOR_ROLE = {"first": ("i2v", "start from"), "last": ("flf", "end on")}


def _check_director_inputs(graph: _Graph, node: Mapping[str, Any], contract: ModeContract, bound: set, issues: List[Issue]) -> None:
    shapes = graph.catalog[node["preset_id"]].get("director_shapes", {}).get(node["mode"], [])
    for port, spec in contract.inputs.items():
        if spec.target.kind != "video_director_role" or port not in bound:
            continue
        shape, verb = DIRECTOR_SHAPE_FOR_ROLE[spec.target.role]
        if shape not in shapes:
            issues.append(Issue(
                "director_role_unsupported",
                f"{graph.label(node)} cannot {verb} an image in this mode. Remove the {spec.label.lower()} connection.",
                node_id=node["id"],
            ))


def _check_inputs(graph: _Graph, edges: List[Mapping[str, Any]], issues: List[Issue]) -> None:
    bound: Dict[str, set] = {}
    for edge in edges:
        bound.setdefault(edge["target"], set()).add(edge["target_handle"])
    for node in graph.by_id.values():
        contract = graph.contract(node)
        if contract is None:
            continue
        _check_director_inputs(graph, node, contract, bound.get(node["id"], set()), issues)
        if not _has_settings(node):
            continue
        effective = graph.effective(node)
        form_name = graph.form_name(node, effective)
        fields = graph.catalog[node["preset_id"]]["modes"][node["mode"]]["forms"].get(form_name)
        if fields is None:
            continue
        form_data = effective.get("formData") or {}
        for port, spec in contract.inputs.items():
            if spec.target.kind != "form_field":
                continue
            field = fields.get(spec.target.field)
            if field is None:
                continue
            saved = not _empty(form_data.get(spec.target.field))
            if port in bound.get(node["id"], set()):
                if saved:
                    issues.append(Issue(
                        "input_replaces_saved",
                        f"{graph.label(node)}: the connected media replaces the {spec.label.lower()} saved in its session.",
                        severity="warning",
                        node_id=node["id"],
                    ))
            elif field["required"] and not saved:
                issues.append(Issue(
                    "missing_input",
                    f"{graph.label(node)} needs a {spec.label.lower()}. Connect one or save one in its session.",
                    node_id=node["id"],
                ))


def _topological(graph: _Graph, edges: List[Mapping[str, Any]]) -> List[str]:
    indegree = {node_id: 0 for node_id in graph.by_id}
    successors: Dict[str, List[str]] = {node_id: [] for node_id in graph.by_id}
    for edge in edges:
        indegree[edge["target"]] += 1
        successors[edge["source"]].append(edge["target"])
    ready = sorted((node_id for node_id, degree in indegree.items() if degree == 0), key=graph.order.get)
    ordered = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(node_id)
        for nxt in successors[node_id]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=graph.order.get)
    return ordered


def _estimate(
    graph: _Graph,
    edges: List[Mapping[str, Any]],
    order: List[str],
    run_mode: str,
    limits: Limits,
    issues: List[Issue],
) -> Dict[str, Any]:
    incoming: Dict[str, List[Mapping[str, Any]]] = {}
    for edge in edges:
        incoming.setdefault(edge["target"], []).append(edge)
    nodes: Dict[str, Dict[str, int]] = {}
    for node_id in order:
        node = graph.by_id[node_id]
        if node.get("kind") == "media":
            nodes[node_id] = {"invocations": 0, "outputs": len(node.get("assets") or [])}
            continue
        if node.get("kind") == "llm":
            sources = incoming.get(node_id, [])
            invocations = max((nodes[edge["source"]]["outputs"] for edge in sources), default=0) if sources else 1
            invocations *= prompts_per_input(node)
            nodes[node_id] = {"invocations": invocations, "outputs": invocations}
            continue
        contract = graph.contract(node)
        if contract is None:
            nodes[node_id] = {"invocations": 0, "outputs": 0}
            continue
        sources = driving_edges(graph.by_id, incoming.get(node_id, []))
        invocations = max((nodes[edge["source"]]["outputs"] for edge in sources), default=0) if sources else 1
        sides = [edge for edge in incoming.get(node_id, []) if edge not in sources and edge["target_handle"] == "prompt"]
        invocations *= max((prompts_per_input(graph.by_id[edge["source"]]) for edge in sides), default=1)
        per_invocation = _quantity(graph.effective(node).get("formData") or {}, contract.quantity_field)
        nodes[node_id] = {"invocations": invocations, "outputs": invocations * per_invocation}
    severity = "error" if run_mode == "automatic" else "warning"
    edge_counts: Dict[str, int] = {}
    for edge in edges:
        count = nodes[edge["source"]]["outputs"]
        edge_counts[edge["id"]] = count
        if count > limits.max_outputs_per_connection:
            source = graph.by_id[edge["source"]]
            target = graph.by_id[edge["target"]]
            hint = "" if severity == "error" else " Choose fewer files when you continue."
            issues.append(Issue(
                "too_many_outputs_on_edge",
                f"{graph.label(source)} may send up to {count} files to {graph.label(target)}; one connection can carry at most {limits.max_outputs_per_connection}.{hint}",
                severity=severity,
                node_id=target["id"],
                edge_id=edge["id"],
            ))
    total = sum(entry["invocations"] for node_id, entry in nodes.items() if graph.by_id[node_id].get("kind") == "preset")
    if total > limits.max_invocations:
        hint = "" if severity == "error" else " Choose fewer files when you continue."
        issues.append(Issue(
            "too_many_invocations",
            f"This elixir may start up to {total} generations in one brew; the limit is {limits.max_invocations}.{hint}",
            severity=severity,
        ))
    return {"nodes": nodes, "edges": edge_counts, "total_invocations": total}


def validate_graph(
    definition: Mapping[str, Any],
    *,
    run_mode: str,
    catalog: Catalog,
    contracts: Contracts,
    limits: Limits,
    unavailable: Optional[Mapping[str, Sequence[Tuple[int, str]]]] = None,
    writer: Optional[Callable[[Optional[str]], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    graph = _Graph(definition, catalog, contracts)
    issues: List[Issue] = []
    _check_sizes(graph, limits, issues)
    edges = _check_ids(graph, issues)
    for node in graph.by_id.values():
        if node.get("kind") == "media":
            _check_media_node(graph, node, limits, unavailable or {}, issues)
        elif node.get("kind") == "preset":
            _check_preset_node(graph, node, issues)
    structural = _check_edges(graph, edges, issues)
    _check_fan(graph, structural, limits, issues)
    _check_pairing(graph, structural, issues)
    _check_writers(graph, structural, writer, issues)
    cyclic = _check_cycles(graph, structural, issues)
    _check_inputs(graph, structural, issues)
    order = None
    estimates = None
    if not cyclic:
        order = _topological(graph, structural)
        estimates = _estimate(graph, structural, order, run_mode, limits, issues)
    return {
        "valid": not any(issue.severity == "error" for issue in issues),
        "issues": [issue.to_dict() for issue in issues],
        "order": order,
        "estimates": estimates,
        "writer": {"models": list(writer(None).get("models") or [])} if writer is not None else None,
    }
