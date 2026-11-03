import pytest

from backend.type_mapping import (
    CIVITAI_TYPES,
    HF_TEXT_OUTPUT_TASKS,
    civitai_mb_type,
    huggingface_mb_type,
)
from src.plugin_api.models import model_types

from . import huggingface_fakes
from .civitai_fakes import body


def test_every_mapped_type_is_a_potionui_model_type():
    assert set(CIVITAI_TYPES.values()) <= set(model_types())


@pytest.mark.parametrize(
    "civitai_type, expected",
    [
        ("Checkpoint", "checkpoint"),
        ("LORA", "lora"),
        ("LoCon", "lora"),
        ("DoRA", "lora"),
        ("TextualInversion", "embedding"),
        ("Controlnet", "controlnet"),
        ("Upscaler", "upscaler"),
        ("VAE", "vae"),
        ("Detection", "detection_bbox"),
        ("TextEncoder", "text_encoder"),
        ("CLIP", "text_encoder"),
        ("UNet", "unet"),
        ("LLM", "llm"),
        ("lora", "lora"),
        (" Checkpoint ", "checkpoint"),
    ],
)
def test_mapped_types(civitai_type, expected):
    assert civitai_mb_type(civitai_type) == expected


@pytest.mark.parametrize(
    "civitai_type",
    ["Hypernetwork", "AestheticGradient", "MotionModule", "Poses", "Wildcards", "Workflows", "Other", "Unknown", "", None, 3],
)
def test_types_without_a_potionui_folder_map_to_none(civitai_type):
    assert civitai_mb_type(civitai_type) is None


def test_every_mapped_type_is_a_live_civitai_type():
    assert set(CIVITAI_TYPES) <= set(body("enums")["ModelType"])


@pytest.mark.parametrize(
    "tags, pipeline_tag, filenames, repo_id, expected",
    [
        (["lora"], "text-to-image", ["a.safetensors"], None, "lora"),
        (["LoRA", "controlnet"], None, [], None, "lora"),
        (["controlnet", "gguf"], "image-to-image", [], None, "controlnet"),
        (["vae", "gguf"], None, [], None, "vae"),
        (["super-resolution"], "image-to-image", [], None, "upscaler"),
        (["image_upscaling"], None, [], None, "upscaler"),
        (["PyTorch"], None, ["RealESRGAN_x4.pth"], "ai-forever/Real-ESRGAN", "upscaler"),
        (["gguf", "text-to-image"], "text-to-image", ["model-Q4_K_M.gguf"], None, "diffusion_model"),
        (["diffusers"], "text-to-image", ["sd_xl_base_1.0.safetensors", "unet/x.safetensors"], None, "checkpoint"),
        (["diffusers"], "text-to-image", ["model.ckpt"], None, "checkpoint"),
        (["diffusers"], "text-to-image", ["unet/diffusion_pytorch_model.safetensors"], None, None),
        (["diffusers"], "text-to-image", ["model.onnx", "model.gguf", "README.md"], None, None),
        (["diffusers"], "image-to-image", ["model.safetensors"], None, None),
        ([], None, ["model.safetensors"], None, None),
        (["lora", "transformers"], "text-generation", ["adapter_model.safetensors"], None, None),
        (["lora"], "image-text-to-text", [], None, None),
        (["gguf"], "text-generation", ["llama-Q4_K_M.gguf"], None, None),
        (["super-resolution"], "image-classification", [], "x/esrgan-judge", None),
        (None, None, None, None, None),
        ([3, None, "lora"], 7, [None, 5], 9, "lora"),
    ],
)
def test_hugging_face_types(tags, pipeline_tag, filenames, repo_id, expected):
    assert huggingface_mb_type(tags, pipeline_tag, filenames, repo_id=repo_id) == expected


def test_text_output_tasks_do_not_include_generation_tasks():
    generation = {"text-to-image", "image-to-image", "text-to-video", "image-to-video", "text-to-audio", "text-to-3d", "image-to-3d"}
    assert not generation & HF_TEXT_OUTPUT_TASKS


@pytest.mark.parametrize(
    "fixture",
    ["models_search_lora", "models_search_lora_page2", "models_text_to_image_top", "models_filter_lora_tag",
     "models_filter_nsfw_tag", "models_author"],
)
def test_every_recorded_hugging_face_repo_maps_to_a_potionui_type_or_none(fixture):
    for raw in huggingface_fakes.body(fixture):
        filenames = [sibling["rfilename"] for sibling in raw.get("siblings", [])]
        mapped = huggingface_mb_type(raw.get("tags"), raw.get("pipeline_tag"), filenames, repo_id=raw["id"])
        assert mapped is None or mapped in model_types()
