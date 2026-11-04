from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.features.elixirs.records import ElixirRecord
from src.features.elixirs.vocabulary import node_label

LATEST_OUTPUTS = 6
CANDIDATE_INVOCATIONS = 48
BREWING_RUNS = ("pending", "running", "cancelling")
ATTENTION_RUNS = ("needs_attention", "failed", "partial", "interrupted")
MAX_REASON = 120


def recent_outputs(repository, user_id: str, elixir_ids: List[str], finals: Mapping[str, set]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for row in repository.recent_completed_outputs(user_id, elixir_ids, CANDIDATE_INVOCATIONS):
        rows.setdefault(row["elixir_id"], []).append(row)
    chosen: Dict[str, Dict[str, Any]] = {}
    for elixir_id, items in rows.items():
        run_id = items[0]["run_id"]
        in_run = [item for item in items if item["run_id"] == run_id]
        wanted = finals.get(elixir_id) or set()
        last = [item for item in in_run if item["node_id"] in wanted]
        nodes = {item["node_id"] for item in last} or {item["node_id"] for item in in_run}
        chosen[elixir_id] = {
            "run_id": run_id,
            "nodes": nodes,
            "items": [
                {**output, "node_id": item["node_id"]}
                for item in (last or in_run)
                for output in item["outputs"]
            ],
        }
    totals = repository.output_totals(sorted({entry["run_id"] for entry in chosen.values()}))
    for entry in chosen.values():
        entry["total"] = sum(count for (run_id, node_id), count in totals.items() if run_id == entry["run_id"] and node_id in entry["nodes"])
    return chosen


def _output_entry(output: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: output.get(key)
        for key in ("kind", "generation_id", "file_id", "item_id", "media_type", "original_filename", "preview_url", "node_id")
    }


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1].rstrip() + "…"


def _preset_nodes(record: ElixirRecord) -> Dict[str, Mapping[str, Any]]:
    return {node["id"]: node for node in record.definition.get("nodes") or [] if node.get("kind") == "preset"}


def _label(record: ElixirRecord, node_id: str) -> str:
    for node in record.definition.get("nodes") or []:
        if node.get("id") == node_id:
            return node_label(node)
    return "A node"


def _waiting_reason(record: ElixirRecord, run: Mapping[str, Any]) -> str:
    edges = {edge.get("id"): edge for edge in record.definition.get("edges") or []}
    for edge_id, checkpoint in run["checkpoints"].items():
        if checkpoint.get("confirmed") is None and checkpoint.get("eligible") and not checkpoint.get("error"):
            edge = edges.get(edge_id)
            if edge:
                return f"Choose outputs from {_label(record, edge['source'])}"
    return "Choose which outputs continue"


def _attention_reason(record: ElixirRecord, run: Mapping[str, Any]) -> str:
    for node_id, state in run["node_states"].items():
        if state.get("status") == "failed":
            return f"{_label(record, node_id)} failed"
    for node_id, state in run["node_states"].items():
        if state.get("status") == "no_output":
            return f"{_label(record, node_id)} made nothing"
    for checkpoint in run["checkpoints"].values():
        if checkpoint.get("error"):
            return str(checkpoint["error"])
        if not checkpoint.get("eligible"):
            return "A connection has nothing to send"
    if run.get("error"):
        return str(run["error"])
    return "The last brew was interrupted" if run["status"] == "interrupted" else "The last brew did not finish"


def _progress_reason(record: ElixirRecord, run: Mapping[str, Any]) -> str:
    presets = _preset_nodes(record)
    total = len(presets)
    done = sum(1 for node_id in presets if run["node_states"].get(node_id, {}).get("status") == "done")
    return f"{done} of {total} nodes" if total else ""


def summarize(record: ElixirRecord, run: Optional[Mapping[str, Any]], outputs: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    items = (outputs or {}).get("items") or []
    summary: Dict[str, Any] = {
        "status": "not_brewed",
        "reason": "",
        "status_at": None,
        "last_brewed_at": None,
        "latest_run_id": None,
        "latest_outputs": [_output_entry(item) for item in items[:LATEST_OUTPUTS]],
        "output_count": (outputs or {}).get("total", 0),
        "outputs_run_id": (outputs or {}).get("run_id"),
        "outputs_current": True,
    }
    if run is None:
        return summary
    status = run["status"]
    summary.update(last_brewed_at=run["created_at"], latest_run_id=run["id"], status_at=run.get("finished_at") or run["updated_at"])
    summary["outputs_current"] = outputs is None or outputs["run_id"] == run["id"]
    if status in BREWING_RUNS:
        summary["status"] = "brewing"
        summary["reason"] = "Stopping" if status == "cancelling" else _progress_reason(record, run)
    elif status == "awaiting_selection":
        summary["status"] = "awaiting"
        summary["reason"] = _waiting_reason(record, run)
    elif status in ATTENTION_RUNS:
        summary["status"] = "attention"
        summary["reason"] = _attention_reason(record, run)
    elif status == "cancelled":
        summary["status"] = "stopped"
        summary["reason"] = _progress_reason(record, run)
    else:
        summary["status"] = "done"
        count = summary["output_count"]
        summary["reason"] = f"{count} {'file' if count == 1 else 'files'}" if count else ""
    summary["reason"] = _short(summary["reason"])
    return summary


def final_nodes(record: ElixirRecord) -> set:
    sources = {edge.get("source") for edge in record.definition.get("edges") or []}
    return {node["id"] for node in record.definition.get("nodes") or [] if node.get("kind") == "preset" and node["id"] not in sources}


def summaries(repository, records: Iterable[ElixirRecord], user_id: str) -> Dict[str, Dict[str, Any]]:
    records = list(records)
    ids = [record.id for record in records]
    runs = repository.latest_runs(user_id, ids)
    outputs = recent_outputs(repository, user_id, ids, {record.id: final_nodes(record) for record in records})
    return {record.id: summarize(record, runs.get(record.id), outputs.get(record.id)) for record in records}
