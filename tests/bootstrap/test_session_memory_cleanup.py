from src.bootstrap.composition.chat import register_session_memory_cleanup
from src.features.llm_memory.records import LLMMemoryNote
from src.features.llm_memory.repository import LLMMemoryRepository
from src.features.sessions.dto import Session
from src.features.sessions.operations.delete import delete_session
from src.features.sessions.repository import SessionRepository
from src.platform.plugins.hooks import HookChain
from tests.fixtures.persistence_base import PersistenceTestBase


class HookOnlyRegistry:
    def __init__(self):
        self.hook_chain = HookChain()

    def execute_hook(self, hook_name, context=None, initial_data=None):
        final_context, results = self.hook_chain.execute(hook_name, context=context, initial_data=initial_data)
        return final_context, all(r.success for r in results)


class TestSessionMemoryCleanup(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_test_user()
        self.sessions = SessionRepository()
        self.memory = LLMMemoryRepository()
        self.registry = HookOnlyRegistry()
        register_session_memory_cleanup(self.registry, self.memory)
        self.kept = self.sessions.create(Session(id="sess-keep", user_id=self.user, preset_id="p", name="Keep"))
        self.doomed = self.sessions.create(Session(id="sess-gone", user_id=self.user, preset_id="p", name="Gone"))
        for key, ref in [("a", "sess-gone"), ("b", "sess-gone"), ("c", "sess-keep")]:
            self.memory.upsert(LLMMemoryNote(user_id=self.user, key=key, content="note text", scope="session", scope_ref=ref))
        self.memory.upsert(LLMMemoryNote(user_id=self.user, key="g", content="global note text", scope="global"))

    def test_deleting_a_session_removes_only_its_notes(self):
        delete_session(self.sessions, self.registry, self.user, "sess-gone")

        remaining = sorted((n.scope, n.scope_ref, n.key) for n in self.memory.list_notes(user_id=self.user))
        assert remaining == [("global", None, "g"), ("session", "sess-keep", "c")]
        assert self.sessions.get_by_id("sess-gone") is None
