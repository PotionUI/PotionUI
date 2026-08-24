"""SigLIP embedder for gallery visual search.

Embeds gallery images and free-text queries into SigLIP's shared
post-projection space (``get_image_features`` / ``get_text_features``, never
``pooler_output``), L2-normalized on both sides so plain cosine ranks them.

Two properties of SigLIP are load-bearing here:

- The text tower was trained with fixed 64-token padding. Queries MUST be
  tokenized with ``padding="max_length", max_length=64, truncation=True`` -
  dynamic padding materially degrades retrieval quality.
- Matching image/text cosines are LOW (~0.0-0.3). Callers rank by relative
  order (top-K plus a relative cutoff), never by an absolute floor, and never
  apply the trained logit scale/bias.

``torch``/``transformers`` are only imported inside ``_ensure_processor``/
``_load_model``, called from the first embed, so importing this module never
pays their import cost at process boot (mirrors ``LocalEmbeddingProvider``).

The model weights (``AutoModel``) are never held on this instance - they go
through ``ModelLifecycleManager.acquire()`` on every embed call under a
per-call lease (mirrors ``NativeLLMClient._leased`` in
``src/features/llm/clients/native.py``), so the embedder participates in the
same cache/eviction/RAM-admission machinery as every diffusion model. The
processor (tokenizer + image preprocessor) IS kept on the instance - it's
lightweight and has nothing worth evicting.
"""

import logging
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from src.platform.filesystem.model_weights import dir_size, weights_status
from src.platform.runtime.model_lifecycle.manager import (
    ModelLifecycleManager,
    get_model_lifecycle_manager,
)

if TYPE_CHECKING:
    from src.features.downloads import DownloadManager
    from PIL import Image
    from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)

_LIFECYCLE_KEY_PREFIX = "media/vision-embedder/"
_BYTES_PER_GB = 1024 ** 3


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


class SiglipVisionEmbedder:
    """Lazy-loaded SigLIP image/text embedder shared by indexing and search."""

    DEFAULT_MODEL = "google/siglip-base-patch16-224"
    _LOCAL_SUBDIR = "vision_embeddings"
    TEXT_MAX_LENGTH = 64

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        models_dir: str = "models",
        device: str = "cpu",
        auto_download: bool = True,
        download_manager: Optional["DownloadManager"] = None,
        model_lifecycle_manager: Optional[ModelLifecycleManager] = None,
    ):
        self.model_name = model_name
        self.models_dir = models_dir
        self.device = device
        self.auto_download = auto_download
        self.downloads = download_manager
        self._model_lifecycle_manager = model_lifecycle_manager
        self._source_path: Optional[str] = None
        self._processor = None
        self._load_lock = threading.Lock()

    @property
    def embedder_slug(self) -> str:
        """Stable identity of this model, used to namespace vector storage."""
        return f"local-{_slugify(self.model_name)}"

    @classmethod
    def local_dir_for(cls, model_name: str, models_dir: str) -> Path:
        return Path(models_dir) / cls._LOCAL_SUBDIR / _slugify(model_name)

    def _local_path(self) -> Path:
        return self.local_dir_for(self.model_name, self.models_dir)

    def _weights_present(self) -> bool:
        path = self._local_path()
        return path.is_dir() and any(path.iterdir())

    def is_available(self) -> bool:
        return self._weights_present() or self.auto_download

    @classmethod
    def resolve_status(cls, model_name: str, models_dir: str) -> Dict[str, object]:
        """Presence/path/size for `model_name` without loading anything.

        Used by the admin settings status endpoint - and by the Fetch action to
        derive the exact destination the lazy loader above would use - so both
        agree on a single slug derivation.
        """
        return weights_status(cls.local_dir_for(model_name, models_dir))

    def _ensure_processor(self) -> None:
        """Downloads weights (if needed) and loads the (lightweight)
        processor - NOT the model itself, which ``_load_model`` builds on
        demand under ``ModelLifecycleManager`` (see ``embed_images``/
        ``embed_texts``)."""
        if self._processor is not None:
            return
        with self._load_lock:
            if self._processor is not None:
                return

            from transformers import AutoProcessor

            source: Optional[str] = str(self._local_path()) if self._weights_present() else None
            if source is None:
                if not self.auto_download:
                    raise RuntimeError(
                        f"Vision-embedder weights for '{self.model_name}' were not found "
                        f"under {self._local_path()} and auto-download is disabled."
                    )
                if self.downloads is None:
                    raise RuntimeError(
                        f"Vision-embedder weights for '{self.model_name}' were not found "
                        f"under {self._local_path()} and no download manager is wired."
                    )
                target = self._local_path()
                target.mkdir(parents=True, exist_ok=True)
                self.downloads.ensure_local_hf_repo(self.model_name, str(target))
                source = str(target)

            self._processor = AutoProcessor.from_pretrained(source)
            self._source_path = source

    def _load_model(self) -> Any:
        """The ``ModelLifecycleManager`` loader: builds the ``AutoModel``
        checkpoint and places it on ``self.device``. ``_ensure_processor``
        must have run first (every embed call guarantees this) so
        ``_source_path`` is resolved."""
        import torch
        from transformers import AutoModel

        dtype = torch.float16 if str(self.device).startswith("cuda") else torch.float32
        model = AutoModel.from_pretrained(self._source_path, torch_dtype=dtype)
        model.to(self.device)
        model.eval()
        return model

    def _cache_key(self) -> str:
        return f"{_LIFECYCLE_KEY_PREFIX}{self.embedder_slug}"

    def _fingerprint(self) -> str:
        return f"{self.model_name}|{self.device}"

    def _estimated_size_gb(self) -> Optional[float]:
        size = dir_size(self._local_path())
        return size / _BYTES_PER_GB if size else None

    def _models(self) -> ModelLifecycleManager:
        manager = self._model_lifecycle_manager or get_model_lifecycle_manager()
        if manager is None:
            raise RuntimeError(
                f"Vision embedder '{self.model_name}': no ModelLifecycleManager available "
                f"yet (the app container hasn't finished composing)"
            )
        return manager

    def is_loaded(self) -> bool:
        """Whether the model is currently resident in ModelLifecycleManager -
        distinct from ``is_available()`` (on-disk weights presence). The
        embedder can be on disk but evicted (not loaded) or on disk and
        loaded; a status caller needing both must check both, never infer
        one from the other."""
        manager = self._model_lifecycle_manager or get_model_lifecycle_manager()
        if manager is None:
            return False
        return manager.is_cached(self._cache_key())

    @staticmethod
    def prepare_query(text: str) -> str:
        """Normalize a free-text query for SigLIP's text tower.

        Lowercases (SigLIP was trained on lowercased text) and wraps bare
        1-2 word queries as "a photo of X", which matches the training
        caption distribution far better than a naked noun.
        """
        normalized = " ".join(str(text).strip().lower().split())
        if 0 < len(normalized.split()) <= 2:
            return f"a photo of {normalized}"
        return normalized

    @staticmethod
    def _extract_features(output):
        """The shared-space embedding tensor from a ``get_*_features`` result.

        transformers <5 returned the tensor directly; transformers 5.x returns
        the tower's ``BaseModelOutputWithPooling`` whose ``pooler_output`` is
        the same contrastive embedding (``SiglipModel.forward`` matches
        ``vision_outputs.pooler_output`` against ``text_outputs.pooler_output``
        - in SigLIP both towers project inside the model, so this is the
        post-projection vector, not a CLIP-style pre-projection pool).
        """
        import torch

        if isinstance(output, torch.Tensor):
            return output
        pooled = getattr(output, "pooler_output", None)
        if pooled is not None:
            return pooled
        raise RuntimeError(
            f"Unexpected SigLIP feature output type: {type(output).__name__}"
        )

    def _normalize(self, output) -> List[List[float]]:
        import torch

        features = self._extract_features(output)
        return torch.nn.functional.normalize(features.float(), p=2, dim=-1).cpu().tolist()

    @contextmanager
    def _leased_model(self):
        """Acquire the model through ModelLifecycleManager for the duration
        of one forward pass: unevictable while leased, evictable under RAM
        pressure the instant it's released. Mirrors ``WDTaggerProvider.
        tag_image`` and ``NativeLLMClient._leased``."""
        models = self._models()
        lease_id = f"vision-embedder-{uuid.uuid4().hex}"
        models.begin_generation(None)
        models.begin_lease(lease_id)
        try:
            yield models.acquire(
                self._cache_key(),
                self._fingerprint(),
                self._load_model,
                estimated_vram_gb=self._estimated_size_gb(),
            )
        finally:
            models.end_lease(lease_id)

    def embed_images(self, images: List[Union[str, "Image.Image"]]) -> List[List[float]]:
        """Embed a batch of images (paths or PIL images) into the shared space."""
        if not images:
            return []
        self._ensure_processor()
        import torch
        from PIL import Image as PILImage

        pil_images = []
        for image in images:
            if isinstance(image, (str, Path)):
                with PILImage.open(image) as handle:
                    pil_images.append(handle.convert("RGB"))
            else:
                pil_images.append(image.convert("RGB"))

        inputs = self._processor(images=pil_images, return_tensors="pt")
        with self._leased_model() as model:
            pixel_values = inputs["pixel_values"].to(device=self.device, dtype=model.dtype)
            with torch.no_grad():
                features = model.get_image_features(pixel_values=pixel_values)
        return self._normalize(features)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed query texts with SigLIP's trained fixed-length padding."""
        if not texts:
            return []
        self._ensure_processor()
        import torch

        prepared = [self.prepare_query(text) for text in texts]
        inputs = self._processor(
            text=prepared,
            padding="max_length",
            max_length=self.TEXT_MAX_LENGTH,
            truncation=True,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._leased_model() as model:
            with torch.no_grad():
                features = model.get_text_features(**inputs)
        return self._normalize(features)


def build_vision_embedder(
    settings_manager: "SettingsManager",
    download_manager: Optional["DownloadManager"] = None,
    model_lifecycle_manager: Optional[ModelLifecycleManager] = None,
) -> SiglipVisionEmbedder:
    """Construct the configured gallery vision embedder (see migration 099)."""
    return SiglipVisionEmbedder(
        model_name=settings_manager.get_setting(
            "media_vision_model", SiglipVisionEmbedder.DEFAULT_MODEL
        ),
        models_dir=settings_manager.get_models_dir(),
        device=settings_manager.get_setting("media_vision_device", "cpu"),
        auto_download=bool(
            settings_manager.get_setting("media_vision_auto_download", False)
        ),
        download_manager=download_manager,
        model_lifecycle_manager=model_lifecycle_manager,
    )
