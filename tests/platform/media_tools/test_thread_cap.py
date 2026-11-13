from __future__ import annotations

import io

import pytest

from src.platform.media_tools import FfmpegRunner
from src.platform.media_tools import ffmpeg as ffmpeg_module
from src.platform.media_tools.ffmpeg import apply_thread_cap, set_thread_cap_provider


class FakeProcess:
    returncode = 0

    def __init__(self, command, **kwargs):
        self.command = command
        self.stdout = io.StringIO("")

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


@pytest.fixture(autouse=True)
def reset_provider():
    yield
    set_thread_cap_provider(None)


@pytest.fixture
def popen(monkeypatch):
    launched = []

    def factory(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(ffmpeg_module.subprocess, "Popen", factory)
    return launched


def test_no_cap_leaves_the_command_untouched():
    command = ["ffmpeg", "-i", "in.mp4", "out.mp4"]
    assert apply_thread_cap(command, 0) == command


def test_the_cap_reaches_the_decoder_the_encoder_and_the_filters():
    capped = apply_thread_cap(["ffmpeg", "-y", "-i", "in.mp4", "-vf", "scale=2:2", "out.mp4"], 3)
    assert capped == [
        "ffmpeg", "-filter_threads", "3", "-filter_complex_threads", "3",
        "-y", "-threads", "3", "-i", "in.mp4", "-vf", "scale=2:2", "-threads", "3", "out.mp4",
    ]


def test_every_input_gets_its_own_decoder_cap_and_a_pipe_output_is_capped():
    capped = apply_thread_cap(["ffmpeg", "-i", "a.mp4", "-i", "pipe:0", "-f", "rawvideo", "pipe:1"], 2)
    assert capped.count("-threads") == 3
    assert capped[-2:] == ["-threads", "2"] or capped[-3:-1] == ["-threads", "2"]
    assert capped[-1] == "pipe:1"


def test_an_explicit_threads_option_is_respected():
    command = ["ffmpeg", "-threads", "1", "-i", "in.mp4", "out.mp4"]
    assert apply_thread_cap(command, 4) == command


def test_the_runner_adds_the_live_cap_to_every_command(popen):
    cap = {"value": 4}
    set_thread_cap_provider(lambda: cap["value"])
    runner = FfmpegRunner(ffmpeg="ffmpeg")

    runner.run(["-i", "in.mp4", "out.mp4"])
    cap["value"] = 0
    runner.run(["-i", "in.mp4", "out.mp4"])

    first, second = (process.command for process in popen)
    assert first.count("-threads") == 2 and "4" in first
    assert "-threads" not in second and "-filter_threads" not in second
    assert runner.commands == [first, second]


def test_a_failing_provider_means_no_cap(popen):
    def broken():
        raise RuntimeError("settings gone")

    set_thread_cap_provider(broken)
    FfmpegRunner(ffmpeg="ffmpeg").run(["-i", "in.mp4", "out.mp4"])

    assert "-threads" not in popen[0].command
