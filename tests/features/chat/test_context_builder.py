"""Tests for the per-turn PROMPT STATE context block."""

from src.features.chat.context_builder import ChatContextBuilder


def _inject(segments):
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_prompt_state_block(history, {"segments": segments})
    return history


def _block(segments):
    history = _inject(segments)
    if len(history) == 1:
        return None
    return history[0]["content"]


def test_renders_positive_negative_break_and_template_slot():
    block = _block([
        {
            "index": 0, "id": "abc123", "name": "SUBJECT", "type": "content",
            "enabled": True, "content": "a lone woman in a yellow raincoat",
            "template": {"id": "t1", "name": "Krea-2 photoreal", "slot": "subject", "position": 1},
        },
        {
            "index": 1, "id": "def456", "name": "LIGHTING", "type": "content",
            "enabled": True, "content": "golden hour backlight",
            "template": {"id": "t1", "name": "Krea-2 photoreal", "slot": "light", "position": 4},
        },
        {"index": 2, "id": "brk", "type": "break", "enabled": True, "content": ""},
        {"index": 3, "id": "ghi789", "type": "content", "enabled": False,
         "content": "blurry, low quality", "negative": True},
    ])
    assert block.startswith("PROMPT STATE (current editor contents;")
    assert 'Template: "Krea-2 photoreal"' in block
    assert "Positive:" in block and "Negative:" in block
    assert "01 SUBJECT (slot 1) [on] id=abc123:" in block
    assert "02 LIGHTING (slot 4) [on] id=def456:" in block
    assert "03 ── break ──" in block
    # negatives restart their own numbering
    assert '01 [off] id=ghi789: "blurry, low quality"' in block


def test_truncation_appends_total_char_count():
    content = "word " * 60  # 300 chars, well over the 100 limit
    total = len(" ".join(content.split()))
    block = _block([
        {"index": 0, "id": "x", "type": "content", "enabled": True, "content": content},
    ])
    assert "…" in block
    assert f"({total} ch)" in block
    # whole-word truncation: no dangling partial word before the ellipsis
    line = next(ln for ln in block.splitlines() if "id=x" in ln)
    shown = line.split('"')[1].rstrip("…")
    assert not shown.endswith("wor")


def test_short_content_not_truncated_and_no_char_count():
    block = _block([
        {"index": 0, "id": "x", "type": "content", "enabled": True, "content": "short"},
    ])
    assert '"short"' in block
    assert " ch)" not in block


def test_on_off_state_rendered():
    block = _block([
        {"index": 0, "id": "a", "type": "content", "enabled": True, "content": "on one"},
        {"index": 1, "id": "b", "type": "content", "enabled": False, "content": "off one"},
    ])
    assert "[on] id=a" in block
    assert "[off] id=b" in block


def test_template_line_only_when_provenance_present():
    block = _block([
        {"index": 0, "id": "a", "type": "content", "enabled": True, "content": "no template"},
    ])
    assert "Template" not in block


def test_multiple_templates_listed():
    block = _block([
        {"index": 0, "id": "a", "type": "content", "enabled": True, "content": "one",
         "template": {"id": "t1", "name": "Alpha", "slot": "s", "position": 1}},
        {"index": 1, "id": "b", "type": "content", "enabled": True, "content": "two",
         "template": {"id": "t2", "name": "Beta", "slot": "s", "position": 1}},
    ])
    assert 'Templates: "Alpha", "Beta"' in block


def test_overflow_cap():
    segments = [
        {"index": i, "id": f"s{i}", "type": "content", "enabled": True, "content": f"seg {i}"}
        for i in range(25)
    ]
    block = _block(segments)
    assert "…and 5 more segments — call get_current_segments." in block
    assert "id=s19" in block
    assert "id=s20" not in block


def test_absent_or_empty_segments_injects_nothing():
    assert _block([]) is None
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_prompt_state_block(history, {})
    assert len(history) == 1
    ChatContextBuilder.inject_prompt_state_block(history, None)
    assert len(history) == 1


def test_determinism():
    segments = [
        {"index": 0, "id": "a", "name": "S", "type": "content", "enabled": True,
         "content": "a" * 200,
         "template": {"id": "t", "name": "T", "slot": "s", "position": 2}},
        {"index": 1, "id": "b", "type": "content", "enabled": False,
         "content": "neg", "negative": True},
    ]
    assert _block(segments) == _block([dict(s) for s in segments])


def test_inserted_before_last_user_message():
    history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "current"},
    ]
    ChatContextBuilder.inject_prompt_state_block(
        history, {"segments": [{"index": 0, "id": "a", "type": "content", "enabled": True, "content": "x"}]}
    )
    assert history[-1]["content"] == "current"
    assert history[-2]["role"] == "system"
    assert history[-2]["content"].startswith("PROMPT STATE")


def test_skipped_when_video_director_active():
    """"Segment #N" means a shot once the Video Director is active -- this
    block must not compete with that meaning, even if a caller still sent
    segments alongside it."""
    segments = [{"index": 0, "id": "a", "type": "content", "enabled": True, "content": "x"}]
    history = _inject_with_form_state(
        segments, {"video_director": {"active": True, "doc": {}, "capabilities": {}}}
    )
    assert len(history) == 1


def test_not_skipped_when_video_director_inactive():
    segments = [{"index": 0, "id": "a", "type": "content", "enabled": True, "content": "x"}]
    history = _inject_with_form_state(
        segments, {"video_director": {"active": False, "doc": None, "capabilities": None}}
    )
    assert len(history) == 2
    assert history[0]["content"].startswith("PROMPT STATE")


def _inject_with_form_state(segments, form_state):
    history = [{"role": "user", "content": "hi"}]
    ChatContextBuilder.inject_prompt_state_block(
        history, {"segments": segments, "form_state": form_state}
    )
    return history
