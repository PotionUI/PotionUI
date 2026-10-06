from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.runtime.offload.mesh_diagnostics import StageRecorder, format_stage_line
from src.platform.runtime.offload.mesh_ops import clean_and_decimate_traced

MIN_COMPONENT_AREA = 1e-5


def simplify(vertices: np.ndarray, faces: np.ndarray, target: int) -> Tuple[np.ndarray, np.ndarray]:
    import pyfqmr

    if faces.shape[0] <= target:
        return vertices, faces
    simplifier = pyfqmr.Simplify()
    simplifier.setMesh(vertices.astype(np.float64), faces.astype(np.int32))
    simplifier.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=True, verbose=0)
    out_vertices, out_faces, _ = simplifier.getMesh()
    return np.ascontiguousarray(out_vertices, dtype=np.float32), np.ascontiguousarray(out_faces, dtype=np.int64)


def drop_small_components(mesh) -> None:
    import trimesh

    if mesh.faces.shape[0] == 0:
        return
    components = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(mesh.faces.shape[0]))
    if len(components) <= 1:
        return
    areas = mesh.area_faces
    keep = np.zeros(mesh.faces.shape[0], dtype=bool)
    for component in components:
        if areas[component].sum() >= MIN_COMPONENT_AREA:
            keep[component] = True
    if keep.any() and not keep.all():
        mesh.update_faces(keep)


def record(recorder, name: str, mesh) -> None:
    row = recorder.record(name, np.asarray(mesh.vertices), np.asarray(mesh.faces))
    print(format_stage_line(row), flush=True)


def tidy_0901(mesh, recorder, prefix: str) -> None:
    import trimesh

    mesh.update_faces(mesh.unique_faces())
    record(recorder, f"{prefix}_unique_faces", mesh)
    mesh.update_faces(mesh.nondegenerate_faces())
    record(recorder, f"{prefix}_nondegenerate", mesh)
    mesh.remove_unreferenced_vertices()
    record(recorder, f"{prefix}_unreferenced", mesh)
    drop_small_components(mesh)
    record(recorder, f"{prefix}_small_components", mesh)
    trimesh.repair.fill_holes(mesh)
    record(recorder, f"{prefix}_hole_fill", mesh)


def clean_and_decimate_0901(vertices: np.ndarray, faces: np.ndarray, decimation_target: int, recorder) -> None:
    import trimesh

    verts = np.ascontiguousarray(vertices, dtype=np.float32)
    tris = np.ascontiguousarray(faces, dtype=np.int64)
    recorder.reset()
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    mesh.remove_unreferenced_vertices()
    record(recorder, "clean_unreferenced", mesh)
    trimesh.repair.fill_holes(mesh)
    record(recorder, "clean_hole_fill", mesh)

    verts, tris = simplify(np.asarray(mesh.vertices), np.asarray(mesh.faces), decimation_target * 3)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    record(recorder, "decimate_3x", mesh)
    tidy_0901(mesh, recorder, "tidy_3x")

    verts, tris = simplify(np.asarray(mesh.vertices), np.asarray(mesh.faces), decimation_target)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    record(recorder, "decimate_1x", mesh)
    tidy_0901(mesh, recorder, "tidy_1x")

    trimesh.repair.fix_normals(mesh, multibody=True)
    record(recorder, "fix_normals", mesh)


def run_current(vertices: np.ndarray, faces: np.ndarray, target: int) -> None:
    _, _, rows = clean_and_decimate_traced(vertices, faces, target)
    for row in rows:
        print(format_stage_line(row), flush=True)


def run_pre_0905(vertices: np.ndarray, faces: np.ndarray, target: int) -> None:
    clean_and_decimate_0901(vertices, faces, target, StageRecorder())


VARIANTS = {"current": run_current, "pre-0905": run_pre_0905}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay TRELLIS.2 mesh cleanup on a raw_mesh.npz dump")
    parser.add_argument("raw_mesh", type=Path, help="raw_mesh.npz from a profiled generation")
    parser.add_argument("--decimation-target", type=int, default=100000)
    parser.add_argument("--variant", choices=[*VARIANTS, "both"], default="both")
    args = parser.parse_args()

    data = np.load(args.raw_mesh)
    vertices = np.ascontiguousarray(data["vertices"], dtype=np.float32)
    faces = np.ascontiguousarray(data["faces"], dtype=np.int64)
    print(f"source_id={data['source_id']} resolution={data['resolution']}", flush=True)

    names = list(VARIANTS) if args.variant == "both" else [args.variant]
    for name in names:
        print(f"--- variant={name} decimation_target={args.decimation_target}", flush=True)
        recorder = StageRecorder()
        print(format_stage_line(recorder.record("raw", vertices, faces)), flush=True)
        VARIANTS[name](vertices, faces, args.decimation_target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
