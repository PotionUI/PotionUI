import io
import json
import urllib.error

import pytest

from scripts import potionui_cli as cli


class _Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body):
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    return urllib.error.HTTPError("http://127.0.0.1:26730/health", code, "Service Unavailable", {}, io.BytesIO(raw))


def _answering(monkeypatch, outcome):
    def urlopen(url, timeout):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)


def test_a_200_health_is_ready(monkeypatch):
    _answering(monkeypatch, _Answer(200))

    assert cli.health_state("http://127.0.0.1:26730/health") == "ready"


def test_a_503_that_says_starting_is_a_backend_still_warming_up(monkeypatch):
    _answering(monkeypatch, _http_error(503, {"status": "starting", "service": "potionui-api"}))

    assert cli.health_state("http://127.0.0.1:26730/health") == cli.STARTING


@pytest.mark.parametrize("outcome", [
    _http_error(503, {"status": "degraded"}),
    _http_error(503, b"<html>bad gateway</html>"),
    _http_error(500, {"status": "starting"}),
    urllib.error.URLError("connection refused"),
    OSError("timed out"),
])
def test_anything_else_is_no_answer(monkeypatch, outcome):
    _answering(monkeypatch, outcome)

    assert cli.health_state("http://127.0.0.1:26730/health") is None


def _fake_clock():
    fake_time = {"t": 0.0}

    def clock():
        return fake_time["t"]

    def sleeper(interval):
        fake_time["t"] += interval

    return clock, sleeper


def test_a_backend_that_keeps_saying_starting_is_waited_for_past_the_timeout_and_announced_once():
    clock, sleeper = _fake_clock()
    answers = iter([None, None] + [cli.STARTING] * 41 + ["ready"])
    said = []

    ready = cli.wait_for_ready(lambda: next(answers), timeout=10.0, interval=1.0, clock=clock, sleeper=sleeper, report=said.append)

    assert ready is True
    assert clock() == 43.0
    assert said == [cli.STARTING_MESSAGE]


def test_a_backend_that_stops_answering_after_starting_is_given_the_timeout_again_then_abandoned():
    clock, sleeper = _fake_clock()
    answers = iter([cli.STARTING, cli.STARTING] + [None] * 50)
    said = []

    ready = cli.wait_for_ready(lambda: next(answers), timeout=5.0, interval=1.0, clock=clock, sleeper=sleeper, report=said.append)

    assert ready is False
    assert clock() == 6.0
    assert said == [cli.STARTING_MESSAGE]


def test_a_backend_that_never_answers_is_abandoned_at_the_timeout_without_a_word():
    clock, sleeper = _fake_clock()
    said = []

    ready = cli.wait_for_ready(lambda: None, timeout=3.0, interval=1.0, clock=clock, sleeper=sleeper, report=said.append)

    assert ready is False
    assert clock() == 3.0
    assert said == []
