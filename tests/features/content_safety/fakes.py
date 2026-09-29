from types import SimpleNamespace

from src.features.content_safety.gate import ContentGate
from src.features.content_safety.manager import ContentSafetyManager
from src.features.content_safety.policy import EffectivePolicy


class FakeTagger:
    provenance = "fake-tagger"
    model_name = "fake/tagger"
    device = "cpu"

    def __init__(self, scores=None, present=True, error=None, on_call=None):
        self.scores = list(scores or [])
        self.present = present
        self.error = error
        self.on_call = on_call
        self.calls = []

    def has_weights(self):
        return self.present

    def tag_images(self, images):
        self.calls.append(len(images))
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        results = []
        for _ in images:
            score = self.scores.pop(0) if self.scores else 0.0
            results.append(SimpleNamespace(ratings={"questionable": score, "explicit": 0.0, "general": 1.0 - score}))
        return results


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def get_setting(self, key, default=None, user_id=None):
        return self.values.get(key, default)


class FakeLedger:
    def __init__(self, threshold=0.6, generation_states=None):
        self._threshold = threshold
        self.events = []
        self.scores = {}
        self.unrated = []
        self._generation_states = generation_states or {}

    def threshold(self):
        return self._threshold

    def log_event(self, kind, **kwargs):
        self.events.append((kind, kwargs))

    def record_score(self, key, score, rater=None, source="gate"):
        self.scores[key] = score

    def record_unrated(self, key, source="gate"):
        self.unrated.append(key)

    def generation_states(self, ids):
        return {gid: self._generation_states[gid] for gid in ids if gid in self._generation_states}

    def unrated_files(self, limit, after=""):
        rows = [row for row in getattr(self, "pending_files", []) if row["id"] > after and row["file_path"] not in self.scores]
        return rows[:limit]

    def backfill_progress(self):
        return {"total": len(getattr(self, "pending_files", [])), "rated": len(self.scores)}


class FakeResolver:
    def __init__(self, mode="blocked", restricted=False):
        self.policy = EffectivePolicy(mode, restricted)
        self.groups = SimpleNamespace(get_group_members=lambda group_id: [])

    def resolve(self, user_id):
        return self.policy

    def instance_policy(self):
        return self.policy.mode


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def build(mode="blocked", scores=None, present=True, restricted=False, error=None, words=None, ledger=None):
    clock = Clock()
    tagger = FakeTagger(scores, present=present, error=error, on_call=lambda: setattr(clock, "now", clock.now + 2.0))
    settings = FakeSettings(content_banned_words=words if words is not None else [])
    manager = ContentSafetyManager(
        settings=settings,
        resolver=FakeResolver(mode, restricted),
        ledger=ledger or FakeLedger(),
        gate=ContentGate(tagger, settings),
        clock=clock,
    )
    return manager, tagger, clock
