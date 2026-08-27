"""`src.plugin_api` must expose every output type a plugin pipe can emit.

The layering guard forbids a plugin from importing `src.pipelines.outputs`, so
an output class that is not re-exported here is one a plugin has no legal way
to emit at all. This catches that gap at the surface rather than leaving it to
be discovered as a layering violation in whichever plugin needs it first.
"""

import pytest

from src.pipelines import outputs as pipeline_outputs

# Emittable by a pipe through the `generation_outputs` callable. Excludes
# ErrorGenerationOutput, which GenerationEngine raises on a pipe's behalf.
EMITTABLE = [
    "AudioGenerationOutput",
    "ComfyUIWorkflowGenerationOutput",
    "GalleryGenerationOutput",
    "GenerationOutput",
    "ImageGenerationOutput",
    "MeshGenerationOutput",
    "ProgressGenerationOutput",
    "VideoGenerationOutput",
]


@pytest.mark.parametrize("name", EMITTABLE)
def test_exported_from_plugin_api_pipes(name):
    from src.plugin_api import pipes

    assert hasattr(pipes, name), f"{name} is not importable from src.plugin_api.pipes"
    assert name in pipes.__all__, f"{name} is missing from src.plugin_api.pipes.__all__"
    assert getattr(pipes, name) is getattr(pipeline_outputs, name)


@pytest.mark.parametrize("name", EMITTABLE)
def test_exported_from_plugin_api_root(name):
    import src.plugin_api as plugin_api

    assert hasattr(plugin_api, name), f"{name} is not importable from src.plugin_api"
    assert name in plugin_api.__all__, f"{name} is missing from src.plugin_api.__all__"


def test_mesh_io_type_reaches_the_plugin_surface():
    """A pipe declares what it emits with IOType, so MESH must arrive with it."""
    from src.plugin_api.pipes import IOType

    assert IOType.MESH.value == "MESH"


def test_mesh_output_is_usable_through_the_plugin_surface():
    """A plugin builds the output the same way a core pipe does."""
    from pathlib import Path

    from src.plugin_api.pipes import GalleryGenerationOutput, MeshGenerationOutput

    mesh = MeshGenerationOutput(mesh_path=Path("/tmp/x.glb"), temporary=False, seed=1)
    gallery = GalleryGenerationOutput(images=[], meshes=[mesh])

    assert gallery.meshes == [mesh]
    assert isinstance(mesh, pipeline_outputs.GenerationOutput)


def test_audio_io_type_reaches_the_plugin_surface():
    """A pipe declares what it emits with IOType, so AUDIO must arrive with it."""
    from src.plugin_api.pipes import IOType

    assert IOType.AUDIO.value == "AUDIO"


def test_audio_output_is_usable_through_the_plugin_surface():
    """A plugin builds the output the same way a core pipe does - the
    concrete gap this closed: `content/plugins/marketplace/stable-audio` could not
    construct `AudioGenerationOutput` at all without reaching into
    `src.pipelines.outputs`, which the layering test forbids."""
    from pathlib import Path

    from src.plugin_api.pipes import AudioGenerationOutput, GalleryGenerationOutput

    audio = AudioGenerationOutput(audio_path=Path("/tmp/x.wav"), temporary=False, seed=1)
    gallery = GalleryGenerationOutput(images=[], audios=[audio])

    assert gallery.audios == [audio]
    assert isinstance(audio, pipeline_outputs.GenerationOutput)
