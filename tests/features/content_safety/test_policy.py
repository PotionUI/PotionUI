from types import SimpleNamespace

from src.features.content_safety.constants import RESTRICTED_GROUP_ID
from src.features.content_safety.policy import ContentPolicyResolver


class FakeSettings:
    def __init__(self, policy="allowed"):
        self.policy = policy

    def get_setting(self, key, default=None, user_id=None):
        return self.policy if key == "content_policy_nsfw" else default


class FakeGroups:
    def __init__(self, groups=None):
        self.groups = groups or {}

    def get_user_groups(self, user_id):
        return self.groups.get(user_id, [])


def group(group_id, policy=None):
    return SimpleNamespace(id=group_id, content_policy=policy)


def resolver(instance="allowed", groups=None):
    return ContentPolicyResolver(FakeSettings(instance), FakeGroups(groups))


def test_falls_back_to_the_instance_default():
    assert resolver("blur").resolve("u1").mode == "blur"
    assert resolver("allowed").resolve("u1").mode == "allowed"


def test_restricted_group_is_always_blocked_even_over_a_permissive_group():
    groups = {"u1": [group(RESTRICTED_GROUP_ID, "blocked"), group("trusted", "allowed")]}

    policy = resolver("allowed", groups).resolve("u1")

    assert policy.mode == "blocked"
    assert policy.restricted


def test_group_policy_wins_over_the_instance_default_in_both_directions():
    assert resolver("blur", {"u1": [group("trusted", "allowed")]}).resolve("u1").mode == "allowed"
    assert resolver("allowed", {"u1": [group("strict", "blocked")]}).resolve("u1").mode == "blocked"


def test_strictest_group_wins_among_groups():
    groups = {"u1": [group("a", "allowed"), group("b", "blur")]}

    assert resolver("blocked", groups).resolve("u1").mode == "blur"


def test_groups_without_a_policy_are_ignored():
    groups = {"u1": [group("plain", None)]}

    assert resolver("blur", groups).resolve("u1").mode == "blur"


def test_group_policy_alone_does_not_make_a_user_restricted():
    policy = resolver("allowed", {"u1": [group("strict", "blocked")]}).resolve("u1")

    assert policy.mode == "blocked"
    assert not policy.restricted


def test_unknown_instance_value_fails_closed():
    assert resolver("garbage").resolve(None).mode == "blocked"


def test_allows_nsfw_only_for_allowed():
    assert resolver("allowed").allows_nsfw("u1")
    assert not resolver("blur").allows_nsfw("u1")
