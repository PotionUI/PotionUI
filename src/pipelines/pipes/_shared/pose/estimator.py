from pathlib import Path
from typing import Any, Optional

from src.pipelines.outputs import GenerationExecutionError
from src.platform.assets import asset_subdir
from src.platform.runtime.pose.dwpose import (
    DETECTOR_FILENAME,
    DWPOSE_REPO,
    POSE_FILENAME,
    DWPoseEstimator,
    verify_weights,
)

MODEL_KEY = "native/pose/dwpose"


def weights_directory(assets: Any, weights_dir: str = "") -> Path:
    configured = str(weights_dir or "").strip()
    if configured:
        return Path(configured)
    if assets is None:
        raise GenerationExecutionError(
            "The pose models are needed but no asset service is available to fetch them."
        )
    return Path(
        assets.ensure_asset_repo(
            DWPOSE_REPO,
            subdir=asset_subdir("annotators", DWPOSE_REPO),
            files=[DETECTOR_FILENAME, POSE_FILENAME],
        )
    )


def weights_fingerprint(directory: Path) -> str:
    parts = []
    for name in (DETECTOR_FILENAME, POSE_FILENAME):
        stat = (directory / name).stat()
        parts.append(f"{stat.st_mtime}|{stat.st_size}")
    return "/".join(parts)


def acquire_pose_estimator(assets: Any, models: Optional[Any], weights_dir: str = "") -> DWPoseEstimator:
    directory = weights_directory(assets, weights_dir)
    try:
        verify_weights(directory)
    except ValueError as error:
        raise GenerationExecutionError(f"{error}.") from error

    def load() -> DWPoseEstimator:
        return DWPoseEstimator.from_files(directory)

    if models is None:
        return load()
    return models.acquire(key=MODEL_KEY, fingerprint=weights_fingerprint(directory), loader=load)
