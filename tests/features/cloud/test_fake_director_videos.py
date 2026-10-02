from pathlib import Path

import pytest

from src.features.cloud.contracts import CloudError, CloudRequest, LocalMedia
from src.features.cloud.testing import fake
from src.features.cloud.testing.fake import FakeBehaviour, build_fake_provider, fake_director_specs, fake_video_bytes

FULL, START, TEXT = fake_director_specs()


@pytest.fixture
def ffmpeg_encoder(monkeypatch):
    calls = []

    def encode(frames, path, fps, audio=None):
        calls.append({"frames": frames, "fps": fps, "audio": audio})
        Path(path).write_bytes(b"h264")

    import src.pipelines.pipes._shared.media.video_encode as video_encode

    monkeypatch.setattr(fake.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(video_encode, "encode_frames_to_mp4", encode)
    return calls


def test_with_ffmpeg_the_clip_fades_from_the_start_to_the_end_colour(ffmpeg_encoder):
    data = fake_video_bytes(2, start=(255, 0, 0), end=(0, 0, 255), fps=4, size=(16, 12))

    (call,) = ffmpeg_encoder
    frames = call["frames"]
    assert data == b"h264" and call["fps"] == 4.0 and call["audio"] is None
    assert frames.shape == (8, 12, 16, 3)
    assert tuple(frames[0, 0, 0]) == (255, 0, 0) and tuple(frames[-1, 0, 0]) == (0, 0, 255)


def test_a_clip_asked_to_have_sound_gets_a_stereo_track_as_long_as_the_picture(ffmpeg_encoder):
    fake_video_bytes(2, start=(0, 0, 0), end=(9, 9, 9), fps=4, sound=True)

    audio = ffmpeg_encoder[0]["audio"]
    assert audio.waveform.shape == (2, 2 * fake.SAMPLE_RATE) and audio.sample_rate == fake.SAMPLE_RATE


def request(spec, task="txt2video", **fields):
    return CloudRequest(model=spec, task=task, prompt="a", **fields)


async def test_a_strict_provider_refuses_what_the_model_does_not_offer(tmp_path):
    provider = build_fake_provider(FakeBehaviour(strict_capabilities=True))
    picture = tmp_path / "p.png"
    picture.write_bytes(fake.PNG_1X1)
    media = LocalMedia(path=picture, media_type="image/png", size=picture.stat().st_size)

    for bad in (
        request(TEXT, params={"duration_s": 4}),
        request(TEXT, task="img2video"),
        request(START, task="img2video", inputs={"last_frame": [media]}),
    ):
        with pytest.raises(CloudError) as raised:
            await provider.submit(bad)
        assert raised.value.kind == "invalid_request"

    await provider.submit(request(START, task="img2video", params={"duration_s": 5}, inputs={"first_frame": [media]}))


async def test_failing_from_a_given_request_lets_the_earlier_ones_through():
    provider = build_fake_provider(FakeBehaviour(fail_from_submit=3, fail_kind="unavailable"))

    await provider.submit(request(FULL))
    await provider.submit(request(FULL))
    for _ in range(2):
        with pytest.raises(CloudError) as raised:
            await provider.submit(request(FULL))
        assert raised.value.kind == "unavailable"


async def test_the_specs_switch_replaces_the_discovered_models():
    provider = build_fake_provider(FakeBehaviour(specs=[TEXT]))

    assert [spec.provider_model_id for spec in await provider.discover()] == ["fake/director-text-1"]


async def test_an_async_real_video_job_is_fetched_as_the_video_it_made(tmp_path):
    pytest.importorskip("cv2", reason="the fake writes its videos with cv2", exc_type=ImportError)
    clock = fake.FakeClock()
    provider = build_fake_provider(FakeBehaviour(mode="async", duration_s=1, real_video=True), clock=clock)

    job = await provider.submit(request(FULL, params={"duration_s": 2}))
    clock.advance(5)
    status = await provider.poll(job)
    written = await provider.fetch(status.result.artifacts[0], tmp_path / "v.mp4")

    assert written.read_bytes()[4:8] == b"ftyp"
