"""Detection-orchestration tests for the LTX video detailer.

``detect_tracks`` is exercised with STUB detectors (no mediapipe, no YOLO, no
model files, no cv2): stride sampling, per-kind linking, scene-cut splitting,
short-track filtering and the cap all run for real.
"""

from __future__ import annotations

import numpy as np

from src.pipelines.pipes.detailer.video_ltx.detection import (
    FrameDetector,
    build_frame_detectors,
    detect_tracks,
)


class _StubDetector(FrameDetector):
    """Returns a caller-supplied box per frame index. Reads the index out of the
    frame's ``[0, 0, 0]`` pixel, which the tests stamp with the frame number."""

    def __init__(self, kind, boxes_by_index):
        self.kind = kind
        self.boxes_by_index = boxes_by_index
        self.seen = []

    def detect(self, image):
        idx = int(np.asarray(image)[0, 0, 0])
        self.seen.append(idx)
        return self.boxes_by_index.get(idx, [])


def _indexed_frames(n, h=32, w=32, base=0):
    """n frames whose [0,0,0] pixel encodes the frame index (rest = ``base``)."""
    frames = np.full((n, h, w, 3), base, np.uint8)
    for i in range(n):
        frames[i, 0, 0, 0] = i
    return frames


def test_detect_tracks_samples_at_stride():
    frames = _indexed_frames(24)
    det = _StubDetector("face", {i: [(5, 5, 15, 15)] for i in range(24)})
    detect_tracks(frames, [det], stride=6, fps=24.0, min_track_seconds=0.0)
    assert det.seen == [0, 6, 12, 18]  # only every 6th frame detected on


def test_detect_tracks_builds_one_track_from_stable_subject():
    frames = _indexed_frames(24)
    det = _StubDetector("face", {i: [(5, 5, 15, 15)] for i in range(24)})
    tracks = detect_tracks(frames, [det], stride=6, fps=24.0, min_track_seconds=0.5)
    assert len(tracks) == 1
    assert tracks[0].kind == "face"
    assert tracks[0].start_frame == 0 and tracks[0].end_frame == 18


def test_detect_tracks_no_detections_returns_empty():
    frames = _indexed_frames(24)
    det = _StubDetector("face", {})  # never fires
    assert detect_tracks(frames, [det], stride=6, fps=24.0) == []


def test_detect_tracks_splits_at_scene_cut():
    frames = _indexed_frames(24, base=0)
    frames[12:] = 255  # hard cut at frame 12
    for i in range(12, 24):
        frames[i, 0, 0, 0] = i  # keep the index marker readable
    det = _StubDetector("face", {i: [(5, 5, 15, 15)] for i in range(24)})
    tracks = detect_tracks(frames, [det], stride=6, fps=24.0,
                           cut_threshold=0.35, min_track_seconds=0.0)
    # one continuous subject, but the cut severs it into two tubes
    assert len(tracks) == 2
    assert tracks[0].end_frame == 6
    assert tracks[1].start_frame == 12


def test_detect_tracks_filters_short_and_caps():
    frames = _indexed_frames(60)
    # three subjects of decreasing size, all present the whole clip
    boxes = {i: [(0, 0, 80, 80), (100, 100, 140, 140), (200, 200, 210, 210)] for i in range(60)}
    det = _StubDetector("face", boxes)
    tracks = detect_tracks(frames, [det], stride=6, fps=24.0,
                           min_track_seconds=0.5, max_tracks=2)
    assert len(tracks) == 2  # capped
    # largest-area subject first
    assert tracks[0].boxes[0] == (0, 0, 80, 80)


def test_detect_tracks_two_kinds_tracked_separately():
    frames = _indexed_frames(24)
    face = _StubDetector("face", {i: [(5, 5, 45, 45)] for i in range(24)})
    hand = _StubDetector("hand", {i: [(200, 200, 230, 230)] for i in range(24)})
    tracks = detect_tracks(frames, [face, hand], stride=6, fps=24.0, min_track_seconds=0.5)
    kinds = sorted(t.kind for t in tracks)
    assert kinds == ["face", "hand"]


# -- backend construction (no heavy imports triggered) --------------------


def _touch(path):
    path.write_bytes(b"stub-model-bytes")
    return str(path)


def test_build_frame_detectors_mediapipe_default(tmp_path):
    face = _touch(tmp_path / "face_landmarker.task")
    hand = _touch(tmp_path / "hand_landmarker.task")
    result = build_frame_detectors(detect_faces=True, detect_hands=True,
                                    face_model=face, hand_model=hand)
    kinds = sorted(d.kind for d in result.detectors)
    assert kinds == ["face", "hand"]
    assert result.missing == []
    assert type(result.detectors[0]).__name__.startswith("MediaPipe")


def test_build_frame_detectors_respects_toggles(tmp_path):
    face = _touch(tmp_path / "face_landmarker.task")
    hand = _touch(tmp_path / "hand_landmarker.task")
    assert [d.kind for d in build_frame_detectors(
        detect_faces=True, detect_hands=False, face_model=face, hand_model=hand).detectors] == ["face"]
    assert [d.kind for d in build_frame_detectors(
        detect_faces=False, detect_hands=True, face_model=face, hand_model=hand).detectors] == ["hand"]
    off = build_frame_detectors(detect_faces=False, detect_hands=False, face_model=face, hand_model=hand)
    assert off.detectors == [] and off.missing == []


def test_build_frame_detectors_yolo_backend_is_opt_in(tmp_path):
    face_yolo = _touch(tmp_path / "face_yolov12m.pt")
    # constructing the adapter must NOT import ultralytics (lazy until .detect)
    result = build_frame_detectors(detect_faces=True, detect_hands=False, backend="yolo",
                                    face_yolo_model=face_yolo)
    assert len(result.detectors) == 1 and result.detectors[0].kind == "face"
    assert type(result.detectors[0]).__name__ == "_SharedYoloDetector"


# -- missing model files: on-demand download, graceful degradation ---------
# (Mediapipe .task files are fetched on demand, so a
# fresh install has neither -- this must never look like a crash.)


def test_build_frame_detectors_missing_face_model_is_skipped_not_raised(tmp_path):
    hand = _touch(tmp_path / "hand_landmarker.task")
    missing_face = str(tmp_path / "does-not-exist.task")
    result = build_frame_detectors(detect_faces=True, detect_hands=True,
                                    face_model=missing_face, hand_model=hand)
    assert [d.kind for d in result.detectors] == ["hand"]
    assert result.missing == ["face"]


def test_build_frame_detectors_missing_hand_model_is_skipped_not_raised(tmp_path):
    face = _touch(tmp_path / "face_landmarker.task")
    missing_hand = str(tmp_path / "does-not-exist.task")
    result = build_frame_detectors(detect_faces=True, detect_hands=True,
                                    face_model=face, hand_model=missing_hand)
    assert [d.kind for d in result.detectors] == ["face"]
    assert result.missing == ["hand"]


def test_build_frame_detectors_both_models_missing_yields_no_detectors(tmp_path):
    missing = str(tmp_path / "does-not-exist.task")
    result = build_frame_detectors(detect_faces=True, detect_hands=True,
                                    face_model=missing, hand_model=missing)
    assert result.detectors == []
    assert sorted(result.missing) == ["face", "hand"]


def test_build_frame_detectors_missing_yolo_model_is_skipped_not_raised(tmp_path):
    missing = str(tmp_path / "does-not-exist.pt")
    result = build_frame_detectors(detect_faces=True, detect_hands=False, backend="yolo",
                                    face_yolo_model=missing)
    assert result.detectors == []
    assert result.missing == ["face"]
