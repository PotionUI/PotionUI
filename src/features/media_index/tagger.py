"""Local WD-tagger provider.

Loads a SmilingWolf ``wd-*-tagger-v3`` timm checkpoint lazily on first use
(mirrors ``LocalEmbeddingProvider``): torch/timm are only imported inside
``_ensure_loaded``/``_load_model``, weights live under
``models/taggers/<slug>/`` with an auto-download gate.

The checkpoint weights themselves are never held on this instance. They go
through ``ModelLifecycle.acquire()`` on every ``tag_image`` call under
a per-call lease (mirrors ``NativeLLMClient._leased`` in
``src/features/llm/clients/native.py``), so the tagger participates in the
same cache/eviction/RAM-admission machinery as every diffusion model: lazy
loaded, evictable under RAM pressure between calls, unevictable only for the
duration of one tag. Tag metadata (`_tag_names`, `_tag_categories`,
`_input_size`, `_mean`, `_std`) IS kept on the instance - it's negligible
(a CSV-derived list, not weight tensors) and has nothing worth evicting.

Preprocessing follows the torch recipe for these checkpoints (they were
trained on BGR input): composite over white, pad to a centered white square,
resize per the checkpoint's ``pretrained_cfg`` (448 bicubic), normalize with
its mean/std ([-1, 1]), then swap RGB->BGR. Activation is sigmoid
(multi-label), never softmax.
"""

import csv
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.platform.filesystem.model_weights import dir_size
from src.platform.runtime.model_lifecycle.lifecycle import (
    ModelLifecycle,
    get_model_lifecycle,
)

if TYPE_CHECKING:
    from PIL import Image
    from src.features.downloads import DownloadQueue
    from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

RATING_NAMES = ("general", "sensitive", "questionable", "explicit")

_CATEGORY_NAMES = {0: "general", 4: "character", 9: "rating"}

_LIFECYCLE_KEY_PREFIX = "media/tagger/"
_BYTES_PER_GB = 1024 ** 3


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


@dataclass
class SystemTagPrediction:
    tag: str
    category: str
    confidence: float


@dataclass
class TaggingResult:
    """Content tags above threshold plus all four rating scores."""

    tags: List[SystemTagPrediction] = field(default_factory=list)
    ratings: Dict[str, float] = field(default_factory=dict)


class WDTaggerProvider:
    """Tags images with a local wd-*-tagger-v3 checkpoint."""

    DEFAULT_MODEL = "SmilingWolf/wd-vit-tagger-v3"
    _LOCAL_SUBDIR = "taggers"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        models_dir: str = "models",
        device: str = "cpu",
        auto_download: bool = True,
        tag_threshold: float = 0.35,
        character_threshold: float = 0.75,
        download_queue: Optional["DownloadQueue"] = None,
        model_lifecycle: Optional[ModelLifecycle] = None,
    ):
        self.model_name = model_name
        self.models_dir = models_dir
        self.device = device
        self.auto_download = auto_download
        self.downloads = download_queue
        self.tag_threshold = tag_threshold
        self.character_threshold = character_threshold
        self._model_lifecycle = model_lifecycle
        self._model_config: Optional[Dict[str, Any]] = None
        self._tag_names: List[str] = []
        self._tag_categories: List[int] = []
        self._input_size = 448
        self._mean: Tuple[float, ...] = (0.5, 0.5, 0.5)
        self._std: Tuple[float, ...] = (0.5, 0.5, 0.5)
        self._load_lock = threading.Lock()

    @property
    def provenance(self) -> str:
        """Stable identity of the producing model, stored on every system tag."""
        return _slugify(self.model_name)

    @classmethod
    def local_dir_for(cls, model_name: str, models_dir: str) -> Path:
        return Path(models_dir) / cls._LOCAL_SUBDIR / _slugify(model_name)

    @staticmethod
    def _weights_present_at(path: Path) -> bool:
        return (path / "model.safetensors").is_file() and (path / "selected_tags.csv").is_file()

    def _local_path(self) -> Path:
        return self.local_dir_for(self.model_name, self.models_dir)

    def _weights_present(self) -> bool:
        return self._weights_present_at(self._local_path())

    def is_available(self) -> bool:
        return self._weights_present() or self.auto_download

    @classmethod
    def resolve_status(cls, model_name: str, models_dir: str) -> Dict[str, object]:
        """Presence/path/size for `model_name` without loading anything.

        Used by the admin settings status endpoint - and by the Fetch action to
        derive the exact destination the lazy loader above would use - so both
        agree on a single slug derivation.
        """
        path = cls.local_dir_for(model_name, models_dir)
        present = cls._weights_present_at(path)
        return {"present": present, "path": str(path), "size": dir_size(path) if present else None}

    def _download(self) -> Path:
        if self.downloads is None:
            raise RuntimeError(
                f"Tagger weights for '{self.model_name}' were not found under "
                f"{self._local_path()} and no download manager is wired."
            )
        target = self._local_path()
        target.mkdir(parents=True, exist_ok=True)
        self.downloads.ensure_local_hf_repo(
            self.model_name,
            str(target),
            allow_patterns=["model.safetensors", "config.json", "selected_tags.csv"],
        )
        return target

    def _ensure_loaded(self) -> None:
        """Downloads weights (if needed) and reads tag metadata - NOT the
        model itself, which ``_load_model`` builds on demand under
        ``ModelLifecycle`` (see ``tag_image``)."""
        if self._tag_names:
            return
        with self._load_lock:
            if self._tag_names:
                return

            if not self._weights_present():
                if not self.auto_download:
                    raise RuntimeError(
                        f"Tagger weights for '{self.model_name}' were not found under "
                        f"{self._local_path()} and auto-download is disabled."
                    )
                self._download()

            source = self._local_path()
            config = json.loads((source / "config.json").read_text())

            pretrained_cfg = config.get("pretrained_cfg", {})
            input_size = pretrained_cfg.get("input_size", [3, 448, 448])
            self._input_size = int(input_size[1])
            self._mean = tuple(pretrained_cfg.get("mean", (0.5, 0.5, 0.5)))
            self._std = tuple(pretrained_cfg.get("std", (0.5, 0.5, 0.5)))

            names: List[str] = []
            categories: List[int] = []
            with open(source / "selected_tags.csv", newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    names.append(row["name"])
                    categories.append(int(row["category"]))
            if len(names) != config.get("num_classes", len(names)):
                raise RuntimeError(
                    f"selected_tags.csv has {len(names)} rows but the model head "
                    f"has {config.get('num_classes')} classes."
                )

            self._model_config = config
            self._tag_names = names
            self._tag_categories = categories

    def _load_model(self) -> Any:
        """The ``ModelLifecycle`` loader: builds the timm checkpoint
        and places it on ``self.device``. ``_ensure_loaded`` must have run
        first (``tag_image`` guarantees this) so ``_model_config`` and the
        weights on disk are ready."""
        import timm
        from safetensors.torch import load_file

        config = self._model_config
        model = timm.create_model(
            config["architecture"],
            pretrained=False,
            num_classes=config.get("num_classes", 0),
            **config.get("model_args", {}),
        )
        state_dict = load_file(str(self._local_path() / "model.safetensors"))
        model.load_state_dict(state_dict)
        model.to(self.device)
        model.eval()
        return model

    def _cache_key(self) -> str:
        return f"{_LIFECYCLE_KEY_PREFIX}{self.provenance}"

    def _fingerprint(self) -> str:
        return f"{self.model_name}|{self.device}"

    def _estimated_size_gb(self) -> Optional[float]:
        size = dir_size(self._local_path())
        return size / _BYTES_PER_GB if size else None

    def _models(self) -> ModelLifecycle:
        lifecycle = self._model_lifecycle or get_model_lifecycle()
        if lifecycle is None:
            raise RuntimeError(
                f"Tagger '{self.model_name}': no ModelLifecycle available yet "
                f"(the app container hasn't finished composing)"
            )
        return lifecycle

    def is_loaded(self) -> bool:
        """Whether the checkpoint is currently resident in
        ModelLifecycle - distinct from ``is_available()`` (on-disk
        weights presence). A tagger can be on disk but evicted (not loaded)
        or on disk and loaded; a status caller needing both must check both,
        never infer one from the other."""
        lifecycle = self._model_lifecycle or get_model_lifecycle()
        if lifecycle is None:
            return False
        return lifecycle.is_cached(self._cache_key())

    def _preprocess(self, image: "Image.Image"):
        import numpy as np
        import torch
        from PIL import Image as PILImage

        if image.mode != "RGBA":
            image = image.convert("RGBA")
        canvas = PILImage.new("RGBA", image.size, (255, 255, 255, 255))
        canvas.alpha_composite(image)
        rgb = canvas.convert("RGB")

        width, height = rgb.size
        side = max(width, height)
        square = PILImage.new("RGB", (side, side), (255, 255, 255))
        square.paste(rgb, ((side - width) // 2, (side - height) // 2))
        resized = square.resize((self._input_size, self._input_size), PILImage.BICUBIC)

        tensor = torch.from_numpy(
            np.asarray(resized, dtype=np.float32) / 255.0
        ).permute(2, 0, 1)
        mean = torch.tensor(self._mean).view(-1, 1, 1)
        std = torch.tensor(self._std).view(-1, 1, 1)
        tensor = (tensor - mean) / std
        return tensor[[2, 1, 0], :, :]

    def _predictions_to_result(self, probs) -> TaggingResult:
        result = TaggingResult()
        for index, prob in enumerate(probs):
            category = self._tag_categories[index]
            name = self._tag_names[index]
            confidence = float(prob)
            if category == 9:
                result.ratings[name] = confidence
                continue
            threshold = (
                self.character_threshold if category == 4 else self.tag_threshold
            )
            if confidence >= threshold:
                result.tags.append(
                    SystemTagPrediction(
                        tag=name,
                        category=_CATEGORY_NAMES.get(category, "general"),
                        confidence=confidence,
                    )
                )
        result.tags.sort(key=lambda t: t.confidence, reverse=True)
        return result

    def tag_image_file(self, path: str) -> TaggingResult:
        from PIL import Image as PILImage

        with PILImage.open(path) as image:
            image.load()
            return self.tag_image(image)

    def tag_image(self, image: "Image.Image") -> TaggingResult:
        self._ensure_loaded()
        import torch

        models = self._models()
        lease_id = f"tagger-{uuid.uuid4().hex}"
        # Clears any stale native-generation owner tag on this thread/context
        # before acquiring - see NativeLLMClient._leased's docstring for why
        # this explicit clear (not "nothing else sets it") is load-bearing.
        models.begin_generation(None)
        models.begin_lease(lease_id)
        try:
            model = models.acquire(
                self._cache_key(),
                self._fingerprint(),
                self._load_model,
                estimated_vram_gb=self._estimated_size_gb(),
            )
            batch = self._preprocess(image).unsqueeze(0).to(self.device)
            with torch.inference_mode():
                logits = model(batch)
                probs = torch.sigmoid(logits)[0].float().cpu().numpy()
        finally:
            models.end_lease(lease_id)
        return self._predictions_to_result(probs)


def build_tagger_provider(
    settings: "Settings",
    download_queue: Optional["DownloadQueue"] = None,
    model_lifecycle: Optional[ModelLifecycle] = None,
) -> WDTaggerProvider:
    """Construct the configured tagger from settings (see migration 098)."""
    return WDTaggerProvider(
        model_name=settings.get_setting(
            "media_tagger_model", WDTaggerProvider.DEFAULT_MODEL
        ),
        models_dir=settings.get_models_dir(),
        device=settings.get_setting("media_tagger_device", "cpu"),
        auto_download=bool(
            settings.get_setting("media_tagger_auto_download", False)
        ),
        tag_threshold=float(
            settings.get_setting("media_tagger_tag_threshold", 0.35)
        ),
        character_threshold=float(
            settings.get_setting("media_tagger_character_threshold", 0.75)
        ),
        download_queue=download_queue,
        model_lifecycle=model_lifecycle,
    )
