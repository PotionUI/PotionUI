from pathlib import Path
from typing import Any

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


def acquire_depth_estimator(assets: Any, weights_dir: str = "") -> VideoDepthEstimator:
    import torch

    directory = weights_directory(assets, weights_dir)
    try:
        path = verify_weights(directory)
        model = load_model(path)
    except DepthWeightsError as error:
        raise GenerationExecutionError(f"{error}.") from error
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return VideoDepthEstimator(model=model.to(device), device=device)
