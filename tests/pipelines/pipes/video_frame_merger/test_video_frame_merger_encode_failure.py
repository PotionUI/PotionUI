"""A video_frame_merger that cannot encode must fail the generation.

Every failure inside the encode used to be caught and turned into `None`, which
`process` reported as `{"video": []}` - a pipe output the pipeline treats as
success. The generation completed, the status went to COMPLETED, and the user
got an empty gallery with nothing in the logs pointing at the encoder. These
tests pin the three ways the encode can fail, plus the end-to-end consequence:
GenerationEngine must emit a generation error, not a completion.
"""

from typing import Any, Dict
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("cv2", reason="cv2 required by video frame merging", exc_type=ImportError)

from src.features.generation.engine import GenerationEngine
from src.pipelines.contracts import IOType, PipeInput, PipeOutput, PipeOutputSpec
from src.pipelines.outputs import (
    ErrorGenerationOutput,
    GenerationExecutionError,
    VideoGenerationOutput,
)
from src.pipelines.pipes.video_frame_merger.main import VideoFrameMergerPipe


def _frames(count: int = 3, size=(64, 48)):
    width, height = size
    return [
        Image.fromarray(np.full((height, width, 3), i * 40, dtype=np.uint8), 'RGB')
        for i in range(count)
    ]


def _pipe_input(frames):
    return PipeInput(input={"image": frames})


class _WriterThatNeverOpens:
    def __init__(self, *args, **kwargs):
        pass

    def isOpened(self):
        return False


class _WriterThatFailsOnWrite:
    def __init__(self, *args, **kwargs):
        pass

    def isOpened(self):
        return True

    def write(self, frame):
        raise RuntimeError("codec rejected the frame")

    def release(self):
        pass


class _WriterThatEncodesNothing:
    """Opens, accepts every frame, and leaves a zero-byte file behind - the
    shape a missing system codec actually takes with some OpenCV builds."""

    def __init__(self, path, *args, **kwargs):
        self.path = path

    def isOpened(self):
        return True

    def write(self, frame):
        pass

    def release(self):
        open(self.path, "wb").close()


class FramesPipe:
    """Stands in for the frame extractor feeding the merger."""

    name = "frames"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @staticmethod
    def get_default_config():
        return {}

    @staticmethod
    def inputs():
        return []

    @staticmethod
    def outputs():
        return [PipeOutputSpec(name="image", io_type=IOType.IMAGE, is_array=True)]

    @staticmethod
    def configuration():
        return []

    def process(self, pipe_input, generation_outputs, is_cancelled=None):
        return PipeOutput(output={"image": _frames()})


def test_a_writer_that_never_opens_raises():
    pipe = VideoFrameMergerPipe({})

    with patch("cv2.VideoWriter", _WriterThatNeverOpens):
        with pytest.raises(GenerationExecutionError) as caught:
            pipe.process(_pipe_input(_frames()), Mock())

    assert "codec" in str(caught.value).lower()


def test_a_write_failure_raises_and_keeps_the_cause_in_the_detail_body():
    pipe = VideoFrameMergerPipe({})

    with patch("cv2.VideoWriter", _WriterThatFailsOnWrite):
        with pytest.raises(GenerationExecutionError) as caught:
            pipe.process(_pipe_input(_frames()), Mock())

    assert "codec rejected the frame" in str(caught.value)
    assert "Traceback" in caught.value.detail


def test_an_encoder_that_produces_an_empty_file_raises():
    """The quietest failure: nothing errors, and the video is 0 bytes."""
    pipe = VideoFrameMergerPipe({})

    with patch("cv2.VideoWriter", _WriterThatEncodesNothing):
        with pytest.raises(GenerationExecutionError) as caught:
            pipe.process(_pipe_input(_frames()), Mock())

    assert "empty" in str(caught.value).lower()


def test_no_video_output_is_emitted_when_the_encode_fails():
    """A VideoGenerationOutput for a file that does not exist would put a broken
    entry in the gallery even once the pipeline fails."""
    pipe = VideoFrameMergerPipe({})
    emitted = []

    with patch("cv2.VideoWriter", _WriterThatFailsOnWrite):
        with pytest.raises(GenerationExecutionError):
            pipe.process(_pipe_input(_frames()), emitted.append)

    assert not any(isinstance(o, VideoGenerationOutput) for o in emitted)


def test_a_failed_encode_fails_the_whole_generation():
    """The behaviour the defect was reported as: the generation used to complete
    with no video instead of erroring."""
    manager = GenerationEngine(
        gpu=Mock(), model_directories=Mock(), pipe_catalog=Mock(), settings=Mock(),
        system_monitor=Mock(), memory_advisor=Mock(), llm_service=Mock(),
    )
    manager.pipe_catalog.get_pipe.side_effect = lambda name: {
        "frames": FramesPipe,
        "video_frame_merger": VideoFrameMergerPipe,
    }[name]

    pipes = [
        {'name': 'frames', 'enabled': True, 'input': [], 'cache': [], 'config': {}},
        {
            'name': 'video_frame_merger',
            'enabled': True,
            'input': [["image", "frames", "image"]],
            'cache': [],
            'config': {},
        },
    ]

    outputs = []
    with patch("cv2.VideoWriter", _WriterThatFailsOnWrite):
        with pytest.raises(GenerationExecutionError):
            manager.generate(pipes, outputs.append, "merger_failure_test")

    errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
    assert len(errors) == 1
    assert "codec rejected the frame" in errors[0].error
    assert not any(isinstance(o, VideoGenerationOutput) for o in outputs)
