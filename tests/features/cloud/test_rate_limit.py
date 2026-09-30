import pytest

from src.features.cloud.rate_limit import TokenBucket
from src.features.cloud.testing.fake import FakeClock


async def test_burst_is_free_then_requests_wait_for_refill():
    clock = FakeClock()
    bucket = TokenBucket(2.0, 3, clock)
    for _ in range(3):
        await bucket.acquire()
    assert clock.slept == []
    await bucket.acquire()
    assert clock.slept == [pytest.approx(0.5)]


async def test_tokens_refill_with_elapsed_time_up_to_capacity():
    clock = FakeClock()
    bucket = TokenBucket(1.0, 2, clock)
    await bucket.acquire()
    await bucket.acquire()
    clock.advance(100)
    await bucket.acquire()
    await bucket.acquire()
    assert clock.slept == []
    await bucket.acquire()
    assert clock.slept == [pytest.approx(1.0)]


async def test_pause_blocks_until_the_deadline():
    clock = FakeClock()
    bucket = TokenBucket(None, 1, clock)
    bucket.pause(3.0)
    await bucket.acquire()
    assert clock.slept == [3.0]


async def test_unlimited_bucket_never_waits():
    clock = FakeClock()
    bucket = TokenBucket(None, 1, clock)
    for _ in range(50):
        await bucket.acquire()
    assert clock.slept == []


def test_invalid_arguments_are_rejected():
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        TokenBucket(1.0, 0)
