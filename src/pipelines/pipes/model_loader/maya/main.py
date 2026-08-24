"""
Maya Model Loader Pipe

This pipe loads the Maya text-to-speech model components:
- Maya1: 3B parameter Llama-style transformer for emotionally expressive TTS
- Tokenizer: HuggingFace tokenizer for text processing
- SNAC: Streaming Neural Audio Codec for 24kHz audio output
"""

from pathlib import Path
from typing import Dict, Any, List
import logging

from src.platform.assets import asset_subdir
from src.pipelines.outputs import ModelGenerationOutput
from src.pipelines.contracts import PipeInput, IOType, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.models import BaseModel
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes._shared.models.maya.maya_model import MayaModel

logger = logging.getLogger(__name__)


class ModelLoaderMayaPipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load Maya text-to-speech model components (Model, Tokenizer, SNAC Codec)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model_id": "maya-research/maya1",
            "snac_model": "hubertsiuzdak/snac_24khz",
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("model_id", str, "maya-research/maya1",
                          "Maya model path or HF model ID", required=True),
            PipeConfigSpec("snac_model", str, "hubertsiuzdak/snac_24khz",
                          "SNAC codec model ID", required=True),
            PipeConfigSpec("device", str, "cuda", "Device to load model on", required=False,
                          choices=["cuda", "cpu"]),
            PipeConfigSpec("dtype", str, "bfloat16", "Data type for model weights", required=False,
                          choices=["bfloat16", "float16", "float32"]),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """ModelLoader uses the MODELS lifecycle service for caching"""
        return [
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for cross-generation model reuse", is_array=False),
            PipeInputSpec("ASSETS", IOType.SERVICE, False, "Asset fetcher, to mirror the model and codec repos into the depot", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """ModelLoader produces the Maya model output"""
        return [
            PipeOutputSpec("model", IOType.MODEL, "Loaded Maya model with all components", is_array=False),
        ]

    def progress_message(self) -> str:
        model_id = self.config.get("model_id")
        return f"Loading Maya model <<MODEL:{model_id.split('/')[-1] if '/' in model_id else model_id}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        model_id = self.config.get("model_id")
        snac_model = self.config.get("snac_model")
        return [
            ModelGenerationOutput(
                name=model_id.split('/')[-1] if '/' in model_id else model_id,
                type="other"  # Maya TTS model
            ),
            ModelGenerationOutput(
                name=snac_model.split('/')[-1] if '/' in snac_model else snac_model,
                type="other"  # SNAC audio codec
            ),
        ]

    def cache_key(self) -> str:
        return "model_loader/maya"

    def fingerprint(self) -> str:
        model_id = self.config.get("model_id")
        snac_model = self.config.get("snac_model")
        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        return f"{model_id}|{snac_model}|{device}|{dtype}"

    @staticmethod
    def _resolve_repo(assets, repo_id: str, category: str, label: str) -> str:
        """`repo_id` as a local directory, mirroring it into the depot first.

        `MayaModel` hands whatever this returns straight to `from_pretrained`,
        so resolving here is what keeps the fetch inside the download manager:
        a repo id reaching the library would make the library fetch it, with
        no history, containment or progress.
        """
        if Path(repo_id).is_dir():
            return repo_id
        if assets is None:
            raise RuntimeError(
                f"{label} '{repo_id}' is a Hugging Face repo id and no ASSETS "
                f"service is available to mirror it into the model depot. "
                f"Point this pipe's config at a local directory instead."
            )
        return str(assets.ensure_asset_repo(repo_id, subdir=asset_subdir(category, repo_id)))

    def load_model(self, pipe_input: PipeInput) -> MayaModel:
        model_id = self.config.get("model_id")
        snac_model = self.config.get("snac_model")
        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")

        assets = pipe_input.input.get("ASSETS")
        model_path = self._resolve_repo(assets, model_id, "tts", "Maya model")
        snac_path = self._resolve_repo(
            assets, snac_model, "tts", "SNAC codec",
        )

        logger.info("[MODEL LOADER MAYA] Loading new Maya model")
        new_model = MayaModel(
            template={"base": BaseModel.MAYA},
            config={
                "model_id": model_path,
                "snac_model": snac_path,
                "device": device,
                "dtype": dtype,
            }
        )
        new_model.load(mode="txt2speech")
        return new_model
