from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PRIMARY_METRICS: Dict[str, List[str]] = {
    "cold-generate": ["fcp_ms", "lcp_ms", "t_interactive_ms", "tbt_ms", "js_kb"],
    "slider-drag": ["script_ms_per_frame", "frame_p95_ms", "long_task_total_ms", "mutations_per_frame"],
    "gen-preview-20": ["preview_ms_p95", "frame_p95_ms", "long_task_total_ms", "script_ms"],
    "tab-switch": ["switch_p50_ms", "switch_p95_ms", "long_task_total_ms"],
    "long-prompt-typing": ["event_p95_ms", "persist_ms", "long_task_total_ms", "script_ms"],
    "video-scrub": ["seek_p95_ms", "frame_p95_ms", "long_task_total_ms"],
    "image-pan-zoom-brush": ["heap_used_mb", "pan_frame_p95_ms", "brush_stroke_p95_ms", "adjust_frame_p95_ms"],
    "history-marquee": ["frame_p95_ms", "script_ms_per_frame", "long_task_total_ms", "mutations_per_frame"],
}

NOISE_FLOORS: Dict[str, float] = {"_ms": 1.0, "_kb": 1.0, "_mb": 1.0}
DEFAULT_NOISE_FLOOR = 0.5
RELATIVE_NOISE = 0.05
COMPARE_META_KEYS = ("throttle", "viewport")
LOAD_THRESHOLD = 0.25


def noise_floor(metric: str) -> float:
    for suffix, floor in NOISE_FLOORS.items():
        if metric.endswith(suffix):
            return floor
    return DEFAULT_NOISE_FLOOR


def classify(before: dict, after: dict, metric: str) -> str:
    delta = after["median"] - before["median"]
    spread = float(before.get("max", before["median"])) - float(before.get("min", before["median"]))
    threshold = max(RELATIVE_NOISE * abs(before["median"]), 0.5 * spread, noise_floor(metric))
    if abs(delta) <= threshold:
        return "noise"
    return "better" if delta < 0 else "worse"


def percent_change(before: float, after: float) -> Optional[float]:
    if before == 0:
        return None
    return (after - before) / abs(before) * 100.0


def spread_percent(metric: dict) -> Optional[float]:
    median = metric["median"]
    if median == 0:
        return None
    return (metric["max"] - metric["min"]) / abs(median) * 100.0


def format_number(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:.1f}"
    return f"{value:.2f}"


def render_table(headers: List[str], rows: List[List[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def ordered_metrics(scenario: str, metrics: dict) -> List[str]:
    primary = [m for m in PRIMARY_METRICS.get(scenario, []) if m in metrics]
    return primary + sorted(m for m in metrics if m not in primary)


def summarize(report: dict) -> str:
    blocks: List[str] = []
    ratio = load_ratio(report)
    if ratio is not None:
        blocks.append(
            f"host load (1-min avg per logical CPU, worst sample): {ratio:.2f}"
            + (f"  WARNING: above {LOAD_THRESHOLD}, numbers from this run are unreliable" if ratio > LOAD_THRESHOLD else "")
        )
    for name, scenario in report["scenarios"].items():
        status = scenario.get("status", "ok")
        if status != "ok":
            reason = scenario.get("reason", "")
            blocks.append(f"[{name}] {status}" + (f": {reason}" if reason else ""))
            continue
        rows = []
        for metric in ordered_metrics(name, scenario["metrics"]):
            data = scenario["metrics"][metric]
            spread = spread_percent(data)
            rows.append([
                metric,
                format_number(data["median"]),
                format_number(data["min"]),
                format_number(data["max"]),
                "-" if spread is None else f"{spread:.1f}%",
            ])
        blocks.append(f"[{name}] runs={scenario.get('runs')}\n" + render_table(["metric", "median", "min", "max", "spread"], rows))
    return "\n\n".join(blocks)


def load_ratio(report: dict, scenario: Optional[str] = None) -> Optional[float]:
    cpus = report.get("meta", {}).get("logical_cpus")
    if not cpus:
        return None
    names = [scenario] if scenario else list(report.get("scenarios", {}))
    samples: List[float] = []
    for name in names:
        load = report.get("scenarios", {}).get(name, {}).get("load") or {}
        samples.extend(v for v in (load.get("start"), load.get("end")) if isinstance(v, (int, float)))
    if not samples:
        return None
    return max(samples) / cpus


def busy(report: dict, scenario: Optional[str] = None) -> bool:
    ratio = load_ratio(report, scenario)
    return ratio is not None and ratio > LOAD_THRESHOLD


def compare(before: dict, after: dict) -> str:
    warnings = []
    for label, report in (("before", before), ("after", after)):
        if busy(report):
            warnings.append(
                f"WARNING: the host was busy while the {label} report was recorded (1-min load {load_ratio(report):.2f} per CPU, "
                f"threshold {LOAD_THRESHOLD}); changes on loaded scenarios are marked 'unreliable (load)'. Re-run on a quiet box."
            )
    for key in COMPARE_META_KEYS:
        left = before.get("meta", {}).get(key)
        right = after.get("meta", {}).get(key)
        if left != right:
            warnings.append(f"WARNING: {key} differs (before={left!r}, after={right!r}); the comparison is not like for like.")
    blocks: List[str] = list(warnings)
    names = list(before["scenarios"]) + [n for n in after["scenarios"] if n not in before["scenarios"]]
    for name in names:
        left = before["scenarios"].get(name)
        right = after["scenarios"].get(name)
        if left is None or right is None:
            blocks.append(f"[{name}] only present in {'after' if left is None else 'before'}")
            continue
        if left.get("status") != "ok" or right.get("status") != "ok":
            blocks.append(f"[{name}] before={left.get('status')} after={right.get('status')}; nothing to compare")
            continue
        rows = []
        loaded = busy(before, name) or busy(after, name)
        for metric in ordered_metrics(name, left["metrics"]):
            if metric not in right["metrics"]:
                continue
            a = left["metrics"][metric]
            b = right["metrics"][metric]
            pct = percent_change(a["median"], b["median"])
            flag = classify(a, b, metric)
            if loaded and flag != "noise":
                flag = "unreliable (load)"
            rows.append([
                metric,
                format_number(a["median"]),
                format_number(b["median"]),
                f"{b['median'] - a['median']:+.2f}",
                "-" if pct is None else f"{pct:+.1f}%",
                flag,
            ])
        blocks.append(f"[{name}]\n" + render_table(["metric", "before", "after", "delta", "%", "flag"], rows))
    return "\n\n".join(blocks)


def list_reports(perf_dir: Path) -> List[Path]:
    if not perf_dir.is_dir():
        return []
    return sorted(p for p in perf_dir.glob("*.json") if p.is_file())


def resolve_report(ref: str, perf_dir: Path) -> Path:
    if ref in ("latest", "prev"):
        reports = list_reports(perf_dir)
        needed = 1 if ref == "latest" else 2
        if len(reports) < needed:
            raise FileNotFoundError(f"no '{ref}' perf report under {perf_dir}")
        return reports[-needed]
    path = Path(ref)
    if not path.is_file():
        candidate = perf_dir / ref
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"perf report not found: {ref}")
    return path


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_files(a_ref: str, b_ref: str, perf_dir: Path) -> str:
    a = resolve_report(a_ref, perf_dir)
    b = resolve_report(b_ref, perf_dir)
    header = f"before: {a}\nafter:  {b}\n"
    return header + "\n" + compare(load_report(a), load_report(b))


def read_parts(parts_dir: Path, expected: List[str]) -> Dict[str, dict]:
    scenarios: Dict[str, dict] = {}
    for name in expected:
        part = parts_dir / f"{name}.json"
        if part.is_file():
            try:
                scenarios[name] = json.loads(part.read_text(encoding="utf-8"))
                continue
            except (OSError, ValueError):
                pass
        scenarios[name] = {"status": "failed", "reason": "scenario produced no result file", "runs": 0, "metrics": {}}
    return scenarios


def package_version(frontend_dir: Path, package: str) -> Optional[str]:
    manifest = frontend_dir / "node_modules" / package / "package.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def git_state(repo_root: Path) -> Tuple[Optional[str], Optional[bool]]:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True, timeout=30)
        status = subprocess.run(["git", "status", "--porcelain", "--no-renames"], cwd=str(repo_root), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None, None
    return (sha.stdout.strip() or None), bool(status.stdout.strip())


def cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def usable_cpus() -> int:
    getter = getattr(os, "sched_getaffinity", None)
    if getter is not None:
        return len(getter(0))
    return os.cpu_count() or 1


def node_version() -> Optional[str]:
    node = shutil.which("node")
    if not node:
        return None
    try:
        return subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def build_meta(*, repo_root: Path, frontend_dir: Path, build_dir: Path, runs: int, throttle: float, run_id: str, scenarios: Dict[str, dict]) -> dict:
    sha, dirty = git_state(repo_root)
    chromium = next((s["meta"]["chromium"] for s in scenarios.values() if s.get("meta", {}).get("chromium")), None)
    viewport = next((s["meta"]["viewport"] for s in scenarios.values() if s.get("meta", {}).get("viewport")), None)
    try:
        build_mtime = datetime.fromtimestamp((build_dir / ".svelte-kit" / "output" / "client" / ".vite" / "manifest.json").stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        build_mtime = None
    return {
        "run_id": run_id,
        "runs": runs,
        "throttle": throttle,
        "viewport": viewport,
        "chromium": chromium,
        "cpu_model": cpu_model(),
        "cpu_count": usable_cpus(),
        "logical_cpus": os.cpu_count() or 1,
        "node": node_version(),
        "vite": package_version(frontend_dir, "vite"),
        "sveltekit": package_version(frontend_dir, "@sveltejs/kit"),
        "playwright": package_version(frontend_dir, "@playwright/test"),
        "git_sha": sha,
        "git_dirty": dirty,
        "build_mtime": build_mtime,
        "platform": platform.platform(),
    }


def merge_report(*, parts_dir: Path, perf_dir: Path, expected: List[str], meta: dict, loads: Optional[Dict[str, dict]] = None) -> Path:
    scenarios = read_parts(parts_dir, expected)
    for name, load in (loads or {}).items():
        if name in scenarios:
            scenarios[name]["load"] = load
    meta = dict(meta)
    report = {"meta": meta, "scenarios": scenarios}
    perf_dir.mkdir(parents=True, exist_ok=True)
    out = perf_dir / f"{meta['run_id']}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return out


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
