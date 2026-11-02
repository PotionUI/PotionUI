import pytest

from src.features.chat.apply_tags import ApplyTargets, Target, normalize_apply_tags

CANONICAL_SEG1 = '<tool_action type="update_director_segment" segment_index="0" segment_id="seg-1">'


def _simple_director(mode="t2v"):
    return {
        "form_state": {
            "mode": "video",
            "video_director": {"active": True, "doc": {"mode": mode, "global_prompt": "a cat"}, "capabilities": {}},
        }
    }


def _chain_director():
    return {
        "form_state": {
            "mode": "video",
            "video_director": {
                "active": True,
                "doc": {"mode": "director", "chain": {"fps": 16, "segments": [
                    {"id": "c1", "prompt": "one", "duration": 3},
                    {"id": "c2", "prompt": "two", "duration": 3},
                ]}},
                "capabilities": {"segment_routing": True},
            },
        }
    }


def _timeline_director():
    return {
        "form_state": {
            "mode": "video",
            "video_director": {
                "active": True,
                "doc": {"mode": "director", "timeline": {"fps": 24, "shots": [
                    {"id": "shot-1", "duration": 4, "segments": [{"id": "b1", "text": "x", "start": 0, "end": 2}]},
                    {"id": "shot-2", "duration": 4, "segments": [
                        {"id": "b1", "text": "y", "start": 0, "end": 2},
                        {"id": "b2", "text": "z", "start": 2, "end": 4},
                    ]},
                ]}},
                "capabilities": {},
            },
        }
    }


def _segments():
    return {"segments": [{"id": "s-a", "content": "a"}, {"id": "s-b", "content": "b"}]}


@pytest.fixture
def simple():
    return ApplyTargets.from_context(_simple_director())


@pytest.mark.parametrize("raw", [
    '<tool_action update_director_segment segment_id="seg-1">a dog running</tool_action>',
    '<tool_action update_director_segment segment_id=seg-1>a dog running</tool_action>',
    "<tool_action type='update_director_segment' segment_id='seg-1'>a dog running</tool_action>",
    "<tool_action type=“update_director_segment” segment_id=“seg-1”>a dog running</tool_action>",
    '<tool_action update_director_segment segment_id="seg-1">a dog running',
    '<tool_action type="update_director_segment" segment_id="seg-1">a dog running</tool_action>',
    '<tool_action type="update_director_segment" segment_index="0">a dog running</tool_action>',
    '<tool_action update_director_segment>a dog running</tool_action>',
    '<tool_action type="UPDATE_DIRECTOR_SEGMENT" segment_id="seg-1">a dog running</tool_action>',
    "<tool_action type='update_director_segment' segment_index='0' segment_id='seg-1'>a dog running</tool_action>",
])
def test_malformed_director_tag_becomes_canonical(simple, raw):
    assert normalize_apply_tags(raw, simple) == CANONICAL_SEG1 + "a dog running</tool_action>"


def test_the_reported_tag_with_surrounding_prose(simple):
    raw = 'Here is a version:\n\n<tool_action update_director_segment segment_id="seg-1">a dog running</tool_action>\n\nWant more?'
    assert normalize_apply_tags(raw, simple) == (
        "Here is a version:\n\n" + CANONICAL_SEG1 + "a dog running</tool_action>\n\nWant more?"
    )


def test_unclosed_tag_closes_at_the_paragraph_break(simple):
    raw = '<tool_action update_director_segment segment_id="seg-1">\na dog\nrunning  \n\nWant more?'
    assert normalize_apply_tags(raw, simple) == CANONICAL_SEG1 + "\na dog\nrunning</tool_action>  \n\nWant more?"


def test_unclosed_tag_closes_before_the_next_tag(simple):
    raw = '<tool_action update_director_segment segment_id="seg-1">one <tool_action update_director_segment>two'
    assert normalize_apply_tags(raw, simple) == (
        CANONICAL_SEG1 + "one</tool_action> " + CANONICAL_SEG1 + "two</tool_action>"
    )


def test_a_close_after_a_paragraph_break_still_ends_the_block(simple):
    raw = '<tool_action update_director_segment segment_id="seg-1">one\n\ntwo</tool_action>'
    assert normalize_apply_tags(raw, simple) == CANONICAL_SEG1 + "one\n\ntwo</tool_action>"


def test_unknown_id_becomes_a_cant_apply_note_keeping_the_text(simple):
    raw = 'Try:\n<tool_action type="update_director_segment" segment_id="seg-9">wide\nharbor</tool_action>'
    assert normalize_apply_tags(raw, simple) == "Try:\n_Can't apply: shot \"seg-9\" not found._\n\n> wide\n> harbor"


def test_unknown_id_with_a_valid_index_is_repaired_from_the_index(simple):
    raw = '<tool_action type="update_director_segment" segment_index="0" segment_id="seg-9">x</tool_action>'
    assert normalize_apply_tags(raw, simple) == CANONICAL_SEG1 + "x</tool_action>"


def test_out_of_range_index_without_id_is_a_note():
    targets = ApplyTargets.from_context(_chain_director())
    raw = '<tool_action type="update_director_segment" segment_index="5">x</tool_action>'
    assert normalize_apply_tags(raw, targets) == "_Can't apply: shot #5 not found._\n\n> x"


def test_well_formed_tag_is_left_byte_for_byte(simple):
    raw = '<tool_action type="update_director_segment" segment_id="seg-1" segment_index="0">x</tool_action>'
    assert normalize_apply_tags(raw, simple) == raw


def test_chain_director_resolves_the_index_from_the_id():
    targets = ApplyTargets.from_context(_chain_director())
    raw = '<tool_action update_director_segment segment_id="c2">x</tool_action>'
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="update_director_segment" segment_index="1" segment_id="c2">x</tool_action>'
    )


def test_chain_director_without_an_address_is_ambiguous():
    targets = ApplyTargets.from_context(_chain_director())
    raw = '<tool_action update_director_segment>x</tool_action>'
    assert normalize_apply_tags(raw, targets) == "_Can't apply: shot not found._\n\n> x"


def test_timeline_director_names_the_shot_of_a_flat_index():
    targets = ApplyTargets.from_context(_timeline_director())
    raw = '<tool_action update_director_segment segment_index="2">x</tool_action>'
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="update_director_segment" segment_index="1" segment_id="b2" shot_id="shot-2">x</tool_action>'
    )


def test_timeline_director_scopes_a_beat_id_to_the_named_shot():
    targets = ApplyTargets.from_context(_timeline_director())
    raw = "<tool_action type='update_director_segment' segment_id='b1' shot_id='shot-2'>x</tool_action>"
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="update_director_segment" segment_index="0" segment_id="b1" shot_id="shot-2">x</tool_action>'
    )


def test_update_segment_fills_the_missing_index_from_context_segments():
    targets = ApplyTargets.from_context(_segments())
    raw = '<tool_action update_segment segment_id="s-b">new</tool_action>'
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="update_segment" segment_index="1" segment_id="s-b">new</tool_action>'
    )


def test_update_segment_fills_the_missing_id_from_the_index():
    targets = ApplyTargets.from_context(_segments())
    raw = '<tool_action type="update_segment" segment_index="0">new</tool_action>'
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="update_segment" segment_index="0" segment_id="s-a">new</tool_action>'
    )


def test_update_segment_with_an_unknown_id_is_a_note():
    targets = ApplyTargets.from_context(_segments())
    raw = '<tool_action type="update_segment" segment_id="nope">new</tool_action>'
    assert normalize_apply_tags(raw, targets) == "_Can't apply: segment \"nope\" not found._\n\n> new"


def test_update_segment_targets_another_prompt_in_the_batch():
    targets = ApplyTargets.from_context({
        **_segments(),
        "prompt_batch": {"prompts": [{"index": 1, "segments": [{"id": "p1-a"}, {"id": "p1-b"}]}]},
    })
    raw = '<tool_action update_segment prompt=1 segment_index=1>new</tool_action>'
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="update_segment" segment_index="1" segment_id="p1-b" prompt="1">new</tool_action>'
    )


def test_self_closing_delete_swallows_a_stray_close():
    targets = ApplyTargets.from_context(_segments())
    raw = '<tool_action delete_segment segment_id="s-a"/></tool_action> done'
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="delete_segment" segment_index="0" segment_id="s-a"></tool_action> done'
    )


def test_add_segment_and_prompt_actions_are_only_canonicalized():
    targets = ApplyTargets.from_context(_segments())
    raw = "<tool_action add_segment list=negative>blurry</tool_action><tool_action add_prompt name='Night'>moon"
    assert normalize_apply_tags(raw, targets) == (
        '<tool_action type="add_segment" list="negative">blurry</tool_action>'
        '<tool_action type="add_prompt" name="Night">moon</tool_action>'
    )


def test_without_context_targets_only_the_markup_is_repaired():
    raw = "<tool_action update_segment segment_id='zzz'>new"
    assert normalize_apply_tags(raw, ApplyTargets()) == (
        '<tool_action type="update_segment" segment_id="zzz">new</tool_action>'
    )


@pytest.mark.parametrize("raw", [
    '<tool_action type="search" segment_index="0">q</tool_action>',
    "<tool_action launch_rocket segment_id='x'>go</tool_action>",
    "<tool_action>plain</tool_action>",
    '<tool_action update_director_segment segment_id="seg-1"',
    "no tags at all",
])
def test_unknown_or_truncated_tags_are_left_alone(simple, raw):
    assert normalize_apply_tags(raw, simple) == raw


def test_targets_from_context_read_every_source():
    targets = ApplyTargets.from_context({**_segments(), **_timeline_director()})
    assert targets.segments == [Target("s-a", 0), Target("s-b", 1)]
    assert targets.director == [Target("b1", 0, "shot-1"), Target("b1", 0, "shot-2"), Target("b2", 1, "shot-2")]
    assert ApplyTargets.from_context(None) == ApplyTargets()
