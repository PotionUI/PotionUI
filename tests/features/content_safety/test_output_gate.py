from PIL import Image

from src.features.content_safety.output_gate import apply_verdicts, gate_items
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    ImageGenerationOutput,
    SeedGenerationOutput,
    VideoGenerationOutput,
)


def final_image():
    return ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=False)


def preview_image():
    return ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=True)


def run(output, scores, mode, threshold=0.6, unavailable=False):
    return apply_verdicts(output, gate_items(output), scores, threshold, mode, unavailable)


def test_blocked_final_image_is_dropped_and_counted():
    outcome = run(final_image(), [0.9], "blocked")

    assert outcome.output is None
    assert outcome.blocked == 1
    assert outcome.finals == 0


def test_blocked_safe_final_image_is_delivered_unflagged():
    output = final_image()

    outcome = run(output, [0.1], "blocked")

    assert outcome.output is output
    assert outcome.finals == 1
    assert output._content_nsfw is False
    assert output._preview_suppressed is False


def test_blocked_flagged_preview_is_suppressed_not_counted():
    output = preview_image()

    outcome = run(output, [0.9], "blocked")

    assert outcome.output is output
    assert output._preview_suppressed is True
    assert outcome.blocked == 0


def test_blur_flags_a_final_and_keeps_it():
    output = final_image()

    outcome = run(output, [0.9], "blur")

    assert outcome.output is output
    assert output._content_nsfw is True
    assert output._content_flagged is True
    assert output._preview_suppressed is False
    assert outcome.blocked == 0


def test_blur_leaves_a_safe_preview_unflagged():
    output = preview_image()

    run(output, [0.1], "blur")

    assert output._content_flagged is False


def test_threshold_boundary_is_flagged():
    assert run(final_image(), [0.6], "blocked").blocked == 1


def test_unavailable_blocked_final_fails_the_output():
    outcome = run(final_image(), None, "blocked", unavailable=True)

    assert outcome.unavailable is True
    assert outcome.output is None


def test_unavailable_blocked_preview_is_suppressed():
    output = preview_image()

    outcome = run(output, None, "blocked", unavailable=True)

    assert outcome.unavailable is False
    assert output._preview_suppressed is True


def test_unavailable_blur_flags_conservatively():
    output = final_image()

    outcome = run(output, None, "blur", unavailable=True)

    assert outcome.output is output
    assert output._content_flagged is True
    assert output._content_nsfw is True


def test_gallery_drops_only_the_blocked_finals():
    keep, drop = final_image(), final_image()
    gallery = GalleryGenerationOutput(images=[keep, drop])

    outcome = run(gallery, [0.1, 0.9], "blocked")

    assert outcome.output is gallery
    assert gallery.images == [keep]
    assert outcome.blocked == 1
    assert outcome.finals == 1


def test_gallery_with_everything_blocked_is_dropped():
    gallery = GalleryGenerationOutput(images=[final_image(), final_image()])

    outcome = run(gallery, [0.9, 0.9], "blocked")

    assert outcome.output is None
    assert outcome.blocked == 2


def test_gallery_mixes_images_and_videos():
    video = VideoGenerationOutput(video_path="clip.mp4", temporary=False)
    gallery = GalleryGenerationOutput(images=[final_image()], videos=[video])

    outcome = run(gallery, [0.1, 0.95], "blocked")

    assert gallery.videos == []
    assert len(gallery.images) == 1
    assert outcome.blocked == 1


def test_non_visual_output_has_no_items():
    assert gate_items(SeedGenerationOutput(index=0, seed=1)) == []
