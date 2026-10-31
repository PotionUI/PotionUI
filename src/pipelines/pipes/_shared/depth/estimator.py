from pathlib import Path
from typing import Any, Optional

from src.pipelines.outputs import GenerationExecutionError
from src.platform.assets import asset_subdir
from src.platform.runtime.depth.video_depth import (
    DEPTH_REPO,
    WEIGHT_FILENAME,
    DepthWeightsError,
    VideoDepthEstimator,
    load_model,
    verify_weights,
)


MODEL_KEY = "native/depth/video_depth_small"
ESTIMATED_VRAM_GB = 1.5


def weights_directory(assets: Any, weights_dir: str = "") -> Path:
    configured = str(weights_dir or "").strip()
    if configured:
        return Path(configured)
    if assets is None:
        raise GenerationExecutionError(
            "The video depth model is needed but no asset service is available to fetch it."
        )
    return Path(
        assets.ensure_asset_repo(
            DEPTH_REPO,
            subdir=asset_subdir("annotators", DEPTH_REPO),
            files=[WEIGHT_FILENAME],
        )
    )


def weights_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime}|{stat.st_size}"


def acquire_depth_estimator(assets: Any, models: Optional[Any] = None, weights_dir: str = "") -> VideoDepthEstimator:
    directory = weights_directory(assets, weights_dir)
    try:
        path = verify_weights(directory)
    except DepthWeightsError as error:
        raise GenerationExecutionError(f"{error}.") from error

    def load() -> VideoDepthEstimator:
        return VideoDepthEstimator(model=load_model(path))

    if models is None:
        estimator = load()
    else:
        estimator = models.acquire(
            key=MODEL_KEY, fingerprint=weights_fingerprint(path), loader=load, estimated_vram_gb=ESTIMATED_VRAM_GB,
        )
    return estimator.activate()
