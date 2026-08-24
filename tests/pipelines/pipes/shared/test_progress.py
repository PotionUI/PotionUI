"""Tests for the shared ProgressEmitter."""
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput
from src.pipelines.outputs import Icon


class TestProgressEmitterStep:

    def test_step_emits_progress_output_with_current_and_max(self):
        emitted = []
        progress = ProgressEmitter(emitted.append, title="generator")
        progress.step(3, 10, state="TXT2IMG")

        assert len(emitted) == 1
        out = emitted[0]
        assert isinstance(out, ProgressGenerationOutput)
        assert out.title == "generator"
        assert out.state == "TXT2IMG"
        assert out.progress.current == 3
        assert out.progress.max == 10

    def test_step_forwards_icon(self):
        emitted = []
        progress = ProgressEmitter(emitted.append)
        icon = Icon(name="bolt", effect="pulse")
        progress.step(1, 5, icon=icon)

        assert emitted[0].icon is icon


class TestProgressEmitterState:

    def test_state_emits_message_without_progress(self):
        emitted = []
        progress = ProgressEmitter(emitted.append, title="generator")
        progress.state("Decoding audio...")

        assert len(emitted) == 1
        assert emitted[0].state == "Decoding audio..."
        assert emitted[0].progress is None


class TestProgressEmitterPreview:

    def test_preview_emits_temporary_image_output(self):
        emitted = []
        progress = ProgressEmitter(emitted.append)
        progress.preview("fake-image", seed=42, resolution=(8, 8), cfg=3.0, step=5)

        assert len(emitted) == 1
        out = emitted[0]
        assert isinstance(out, ImageGenerationOutput)
        assert out.temporary is True
        assert out.image == "fake-image"
        assert out.seed == 42
        assert out.resolution == (8, 8)
        assert out.cfg == 3.0
        assert out.step == 5


class TestProgressEmitterPublicEmit:

    def test_emit_attribute_exposes_raw_callback(self):
        received = []

        def cb(output):
            received.append(output)

        progress = ProgressEmitter(cb)
        assert progress.emit is cb

        progress.state("hello")
        assert len(received) == 1
