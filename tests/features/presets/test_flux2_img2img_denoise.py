from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import torch
from PIL import Image

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe
from src.pipelines.pipes.media_loader.main import MediaLoaderPipe
from src.platform.runtime.native.sampling.denoise_loop import schedule_from_settings
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data


@pytest.fixture(scope="module")
def flux2_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/Flux2")), None)
    if template is None:
        pytest.fail("marketplace/Flux2 preset not present")
    return template


class _Settings:
    def __init__(self, root: Path):
        self._root = str(root)

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._root


class _FakeFlux2:
    spec = None

    def __init__(self):
        self.calls = []

    def snap_resolution(self, width, height):
        return width - width % 32, height - height % 32

    def encode_image(self, image, vram_free_gb=None):
        height, width = np.asarray(image).shape[:2]
        return torch.zeros(1, 128, height // 16, width // 16)

    def sample(self, conditioning, latents_shape, *, steps, denoise_strength, sigmas=None, **_):
        used = sigmas if sigmas is not None else schedule_from_settings(
            steps, {"shift": 2.02}, denoise_strength=denoise_strength,
        )
        self.calls.append({"steps": steps, "denoise": denoise_strength, "sigmas": used})
        return torch.zeros(latents_shape)

    def decode(self, latent, vram_free_gb=None):
        return np.zeros((1, latent.shape[2] * 16, latent.shape[3] * 16, 3), dtype=np.uint8)


def _upload(storage: Path) -> dict:
    path = storage / "uploads" / "bottle.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (128, 96), "purple").save(path)
    return {
        "path": str(path),
        "relative_path": "uploads/bottle.png",
        "url": "/api/media/uploads/bottle.png",
        "name": "bottle.png",
        "type": "image",
        "metadata": {"width": 128, "height": 96},
    }


def _pipes(template, source, denoise):
    form = bound_form_data(template, "img2img", {
        "diffusion_model": "/models/flux-2-klein-4b.safetensors",
        "text_encoder": "/models/qwen_3_4b.safetensors",
        "vae": "/models/flux2-vae.safetensors",
        "source_image": source,
        "denoise": denoise,
        "steps": 30,
        "seed": 12345,
    })
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    return processor.process(template, {"prompts": [], "mode": "img2img", "form_data": form})


def _config(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)["config"]


def _run(template, storage, source, denoise):
    pipes = _pipes(template, source, denoise)
    loader_cfg = {**MediaLoaderPipe.get_default_config(), **_config(pipes, "media_loader")}
    loaded = MediaLoaderPipe(config=loader_cfg).process(
        PipeInput(input={"SETTINGS": _Settings(storage)}), lambda o: None,
    )
    generator_cfg = _config(pipes, "generator/flux")
    pipe = GeneratorFluxPipe(config={**GeneratorFluxPipe.get_default_config(), **generator_cfg})
    gen = _FakeFlux2()
    ctx = GeneratorContext(quantity=1, input_seeds=[12345], extra={
        "steps": int(generator_cfg["steps"]),
        "guidance": 3.5,
        "sampler": "euler",
        "width": 128,
        "height": 96,
        **pipe.img2img_context(PipeInput(input={"image": loaded.output["image"]})),
    })
    progress = SimpleNamespace(step=lambda *a, **k: None, preview=lambda *a, **k: None)
    output = pipe.maybe_img2img(gen, conditioning=None, ctx=ctx, index=0, seed=12345, progress=progress)
    return output, gen.calls


@pytest.mark.parametrize("shape", ["reference", "path"])
def test_the_source_image_reaches_img2img_in_both_request_shapes(flux2_template, tmp_path, shape):
    ref = _upload(tmp_path / "storage")
    source = ref if shape == "reference" else ref["path"]
    output, calls = _run(flux2_template, tmp_path / "storage", source, 0.4)
    assert output is not None, "img2img fell through to txt2img: the source image never loaded"
    assert [c["denoise"] for c in calls] == [0.4]


def test_denoise_changes_the_sigmas_actually_sampled(flux2_template, tmp_path):
    ref = _upload(tmp_path / "storage")
    _, low = _run(flux2_template, tmp_path / "storage", ref, 0.4)
    _, high = _run(flux2_template, tmp_path / "storage", ref, 0.55)
    assert low and high
    assert len(low[0]["sigmas"]) == len(high[0]["sigmas"]) == 31
    assert float(low[0]["sigmas"][0]) < float(high[0]["sigmas"][0]) < 1.0
    assert not torch.equal(low[0]["sigmas"], high[0]["sigmas"])
