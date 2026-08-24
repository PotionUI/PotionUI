"""The terminal ``gallery`` pipe is the single node that persists a
generation's final artifact.

Producer pipes (generators, upscaler, seedvr2, ...) emit their gallery media
as ``temporary`` live previews; only this pipe emits ``temporary=False`` media,
so the handler persists each image/video exactly once. If a producer also
emitted ``temporary=False`` (or this pipe stopped), files would be saved twice
(or not at all).
"""

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    AudioGenerationOutput,
    GalleryGenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
)
from src.pipelines.pipes.gallery.main import GalleryPipe


def _run(pipe_input, config=None):
    emitted = []
    pipe = GalleryPipe(config=config or {})
    pipe.process(pipe_input, emitted.append)
    return emitted


def test_images_emitted_for_persistence():
    emitted = _run(PipeInput(input={"image": ["img-a", "img-b"], "seed": [1, 2]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert len(gallery.images) == 2
    assert all(isinstance(i, ImageGenerationOutput) for i in gallery.images)
    # temporary=False is what makes the handler save + create a DB record.
    assert all(i.temporary is False for i in gallery.images)


def test_videos_emitted_for_persistence():
    emitted = _run(PipeInput(input={"video": ["/tmp/out.mp4"], "seed": [7]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert len(gallery.videos) == 1
    assert isinstance(gallery.videos[0], VideoGenerationOutput)
    assert gallery.videos[0].temporary is False


def test_no_media_emits_no_gallery():
    emitted = _run(PipeInput(input={"seed": [1]}))
    assert not any(isinstance(o, GalleryGenerationOutput) for o in emitted)


def test_derived_defaults_to_false():
    emitted = _run(PipeInput(input={"image": ["img-a"], "video": ["/tmp/out.mp4"], "seed": [1]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert all(i.derived is False for i in gallery.images)
    assert all(v.derived is False for v in gallery.videos)


def test_derived_config_marks_all_media():
    emitted = _run(
        PipeInput(input={"image": ["img-a", "img-b"], "video": ["/tmp/out.mp4"], "seed": [1, 2]}),
        config={"derived": True},
    )

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert all(i.derived is True for i in gallery.images)
    assert all(v.derived is True for v in gallery.videos)


def test_derived_config_accepts_templated_string():
    emitted = _run(
        PipeInput(input={"image": ["img-a"], "seed": [1]}),
        config={"derived": "true"},
    )
    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert gallery.images[0].derived is True

    emitted = _run(
        PipeInput(input={"image": ["img-a"], "seed": [1]}),
        config={"derived": "false"},
    )
    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert gallery.images[0].derived is False


# --- Audio ------------------------------------------------------------------
#
# Mirrors the image/video coverage above. Unlike image/video, `audio` items
# arriving on `pipe_input.input["audio"]` are bare path strings too - every
# audio-producing pipe (`audio_trim`, `generator/maya`, the Stable Audio 3
# generator) builds its `PipeOutput` the same way the image/video generators
# do: `{"audio": [r.audio_path for r in results]}`, metadata stripped. So
# `AudioGenerationOutput` has no `derived` field (unlike Image/Video), and
# per-item metadata (duration/sample_rate/channels/track_type/seed/segment)
# is *not* carried through this pipe for audio, exactly as it already isn't
# for image (resolution/sampler/cfg/...) or video (resolution/duration/fps/
# ...) - those fields stay at their dataclass defaults after passing through
# `GalleryPipe` today, for every media type. This is not a new gap: the
# `duration` field specifically - the one that actually gets persisted and
# drives the history card's duration badge - is safe regardless, because
# `AudioGenerationOutputHandler._create_file_record` probes it from the
# saved file whenever the pipe didn't already set it (see
# `tests/features/generation/handlers/test_audio_handler.py::
# test_duration_is_probed_and_persisted_when_not_set_by_the_pipe`, which
# covers exactly this None-duration shape end to end).

def test_audio_emitted_for_persistence(minimal_wav_file):
    emitted = _run(PipeInput(input={"audio": [str(minimal_wav_file)], "seed": [3]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert len(gallery.audios) == 1
    assert isinstance(gallery.audios[0], AudioGenerationOutput)
    assert gallery.audios[0].audio_path == str(minimal_wav_file)
    # temporary=False is what makes the handler save + create a DB record.
    assert gallery.audios[0].temporary is False


def test_multiple_audio_files_emitted_for_persistence(minimal_wav_file):
    emitted = _run(PipeInput(input={"audio": [str(minimal_wav_file), str(minimal_wav_file)]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert len(gallery.audios) == 2
    assert all(isinstance(a, AudioGenerationOutput) for a in gallery.audios)
    assert all(a.temporary is False for a in gallery.audios)


def test_mixed_image_and_audio_are_kept_separate(minimal_wav_file):
    emitted = _run(PipeInput(input={
        "image": ["img-a"],
        "audio": [str(minimal_wav_file)],
        "seed": [1],
    }))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert len(gallery.images) == 1
    assert len(gallery.audios) == 1
    assert gallery.videos == []
    assert isinstance(gallery.images[0], ImageGenerationOutput)
    assert isinstance(gallery.audios[0], AudioGenerationOutput)


def test_audio_alone_triggers_gallery_without_image_or_video(minimal_wav_file):
    emitted = _run(PipeInput(input={"audio": [str(minimal_wav_file)]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert gallery.images == []
    assert gallery.videos == []
    assert len(gallery.audios) == 1


def test_absent_audio_leaves_gallery_audios_empty():
    emitted = _run(PipeInput(input={"image": ["img-a"], "seed": [1]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert gallery.audios == []


def test_no_media_including_audio_emits_no_gallery():
    emitted = _run(PipeInput(input={"audio": [], "seed": [1]}))
    assert not any(isinstance(o, GalleryGenerationOutput) for o in emitted)


def test_audio_output_has_no_derived_field(minimal_wav_file):
    """Unlike Image/VideoGenerationOutput, AudioGenerationOutput carries no
    `derived` field - the `derived` config must not be passed to it."""
    emitted = _run(
        PipeInput(input={"audio": [str(minimal_wav_file)]}),
        config={"derived": True},
    )
    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    assert not hasattr(gallery.audios[0], "derived")


def test_audio_metadata_is_not_fabricated_by_the_pipe(minimal_wav_file):
    """`GalleryPipe` receives a bare path for audio (see module comment
    above) and has no per-item duration/sample_rate/channels to attach, so
    it must not invent values - these stay at the dataclass default (None)
    on the way out, exactly like image/video's untouched metadata fields."""
    emitted = _run(PipeInput(input={"audio": [str(minimal_wav_file)]}))

    gallery = next(o for o in emitted if isinstance(o, GalleryGenerationOutput))
    audio_output = gallery.audios[0]
    assert audio_output.duration is None
    assert audio_output.sample_rate is None
    assert audio_output.channels is None
