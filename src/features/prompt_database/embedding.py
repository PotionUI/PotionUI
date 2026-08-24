"""Embedding providers for prompt vector storage."""

import asyncio
import logging
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import httpx

from src.platform.filesystem.model_weights import weights_status

if TYPE_CHECKING:
    from src.features.downloads import DownloadManager
    from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    """Turn a model name/path into a filesystem- and namespace-safe token."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the embedding provider is ready."""
        ...

    @property
    @abstractmethod
    def embedder_slug(self) -> str:
        """Stable identity of this provider+model, used to namespace vector storage."""
        ...


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Generates embeddings via Ollama /api/embed endpoint."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    @property
    def embedder_slug(self) -> str:
        return f"ollama-{_slugify(self.model)}"

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for texts via Ollama.

        Uses POST {base_url}/api/embed with {"model": model, "input": texts}
        Returns list of embedding vectors.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("embeddings", [])

    async def is_available(self) -> bool:
        """Check if Ollama is running and model is available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code != 200:
                    return False
                data = response.json()
                model_names = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                return self.model in model_names
        except Exception:
            return False


class LocalEmbeddingProvider(EmbeddingProvider):
    """Generates embeddings in-process with a local transformers encoder.

    ``torch``/``transformers`` are only imported inside ``_ensure_loaded``,
    called from the first ``embed()``, so importing this module (and building
    this provider) never pays their import cost at process boot.
    """

    DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
    _LOCAL_SUBDIR = "text_embeddings"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        models_dir: str = "models",
        device: str = "cpu",
        auto_download: bool = True,
        download_manager: Optional["DownloadManager"] = None,
    ):
        self.model_name = model_name
        self.models_dir = models_dir
        self.device = device
        self.auto_download = auto_download
        self.downloads = download_manager
        self._tokenizer = None
        self._model = None
        self._load_lock = threading.Lock()

    @property
    def embedder_slug(self) -> str:
        return f"local-{_slugify(self.model_name)}"

    @classmethod
    def local_dir_for(cls, model_name: str, models_dir: str) -> Path:
        return Path(models_dir) / cls._LOCAL_SUBDIR / _slugify(model_name)

    def _local_path(self) -> Path:
        return self.local_dir_for(self.model_name, self.models_dir)

    def _weights_present(self) -> bool:
        path = self._local_path()
        return path.is_dir() and any(path.iterdir())

    @classmethod
    def resolve_status(cls, model_name: str, models_dir: str) -> Dict[str, object]:
        """Presence/path/size for `model_name` without loading anything.

        Used by the admin settings status endpoint - and by the Fetch action to
        derive the exact destination the lazy loader above would use - so both
        agree on a single slug derivation.
        """
        return weights_status(cls.local_dir_for(model_name, models_dir))

    async def is_available(self) -> bool:
        return self._weights_present() or self.auto_download

    def is_loaded(self) -> bool:
        """Whether the model is currently held in memory - distinct from
        `is_available()` (on-disk weights presence). Unlike the tagger/vision
        providers, this instance holds its own model directly rather than
        through `ModelLifecycleManager` (no eviction), so "loaded" here is
        just "has this instance loaded it since process start"."""
        return self._model is not None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return

            from transformers import AutoModel, AutoTokenizer

            source: Optional[str] = str(self._local_path()) if self._weights_present() else None
            if source is None:
                if not self.auto_download:
                    raise RuntimeError(
                        f"Local embedding weights for '{self.model_name}' were not found "
                        f"under {self._local_path()} and auto-download is disabled."
                    )
                if self.downloads is None:
                    raise RuntimeError(
                        f"Local embedding weights for '{self.model_name}' were not found "
                        f"under {self._local_path()} and no download manager is wired."
                    )
                target = self._local_path()
                target.mkdir(parents=True, exist_ok=True)
                self.downloads.ensure_local_hf_repo(self.model_name, str(target))
                source = str(target)

            tokenizer = AutoTokenizer.from_pretrained(source)
            model = AutoModel.from_pretrained(source)
            model.to(self.device)
            model.eval()
            self._tokenizer = tokenizer
            self._model = model

    def _embed_sync(self, texts: List[str]) -> List[List[float]]:
        self._ensure_loaded()
        import torch

        encoded = self._tokenizer(
            texts, padding=True, truncation=True, max_length=512, return_tensors="pt",
        )
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        with torch.no_grad():
            output = self._model(**encoded)
        token_embeddings = output.last_hidden_state
        mask = encoded["attention_mask"].unsqueeze(-1).expand(token_embeddings.size()).float()
        summed = torch.sum(token_embeddings * mask, dim=1)
        counts = torch.clamp(mask.sum(dim=1), min=1e-9)
        pooled = summed / counts
        normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
        return normalized.cpu().tolist()

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, texts)


def build_embedding_provider(
    settings_manager: "SettingsManager",
    download_manager: Optional["DownloadManager"] = None,
) -> EmbeddingProvider:
    """Construct the configured embedding provider from settings.

    ``prompt_embedding_provider`` selects between ``'local'`` (default, an
    in-process transformers encoder) and ``'ollama'`` (opt-in, an external
    Ollama server).
    """
    if settings_manager.get_setting("prompt_embedding_provider", "local") == "ollama":
        return OllamaEmbeddingProvider(
            base_url=settings_manager.get_setting(
                "prompt_embedding_ollama_base_url", "http://localhost:11434"
            ),
            model=settings_manager.get_setting("prompt_embedding_ollama_model", "nomic-embed-text"),
        )
    return LocalEmbeddingProvider(
        model_name=settings_manager.get_setting("prompt_embedding_model", LocalEmbeddingProvider.DEFAULT_MODEL),
        models_dir=settings_manager.get_models_dir(),
        device=settings_manager.get_setting("prompt_embedding_device", "cpu"),
        auto_download=bool(settings_manager.get_setting("prompt_embedding_auto_download", False)),
        download_manager=download_manager,
    )
