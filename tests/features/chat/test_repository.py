import unittest
from datetime import datetime
import json
from unittest.mock import patch, MagicMock
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.chat.records import ChatSession, ChatMessage
from src.features.chat.repository import (
    ChatMessageRepository, ChatSessionRepository, ChatRepository
)

try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestChatMessageRepository(PersistenceTestBase):
    """Tests for ChatMessageRepository"""

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = ChatMessageRepository()
        self.session_repo = ChatSessionRepository()
        self.test_user_id = self.create_test_user()

        # Create a test session
        self.test_session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test Session',
            status='active',
            llm_config_id='test-llm',
            original_text='Test prompt text'
        )
        self.session_repo.create(self.test_session)

    def tearDown(self):
        """Clean up test data"""
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM chat_messages")
                    cursor.execute("DELETE FROM chat_sessions")
                    cursor.execute("DELETE FROM users")
        except:
            pass
        super().tearDown()

    def test_create_message(self):
        """Test creating a new message"""
        message = ChatMessage(
            id=generate_ulid(),
            session_id=self.test_session.id,
            role='user',
            content='Hello, AI!'
        )

        result = self.repo.create(message)
        self.assertTrue(result)

        # Verify message was created
        retrieved = self.repo.get_by_id(message.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.content, 'Hello, AI!')
        self.assertEqual(retrieved.role, 'user')
        self.assertEqual(retrieved.session_id, self.test_session.id)

    def test_create_message_with_parsed_content(self):
        """Test creating a message with parsed content"""
        parsed_content = {
            'modifiedPrompt': 'Enhanced prompt',
            'explanation': 'I improved the prompt'
        }

        message = ChatMessage(
            id=generate_ulid(),
            session_id=self.test_session.id,
            role='assistant',
            content='Raw response',
            parsed_content=parsed_content,
            metadata={'tokens': 100}
        )

        result = self.repo.create(message)
        self.assertTrue(result)

        retrieved = self.repo.get_by_id(message.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.parsed_content['modifiedPrompt'], 'Enhanced prompt')
        self.assertEqual(retrieved.metadata['tokens'], 100)

    def test_get_by_session(self):
        """Test getting all messages for a session"""
        # Create multiple messages
        for i in range(3):
            message = ChatMessage(
                id=generate_ulid(),
                session_id=self.test_session.id,
                role='user' if i % 2 == 0 else 'assistant',
                content=f'Message {i}'
            )
            self.repo.create(message)

        messages = self.repo.get_by_session(self.test_session.id)
        self.assertEqual(len(messages), 3)

    def test_get_by_session_order(self):
        """Test that messages are returned in creation order"""
        for i in range(3):
            message = ChatMessage(
                id=generate_ulid(),
                session_id=self.test_session.id,
                role='user',
                content=f'Message {i}'
            )
            self.repo.create(message)

        messages = self.repo.get_by_session(self.test_session.id)
        for i, msg in enumerate(messages):
            self.assertEqual(msg.content, f'Message {i}')

    def test_count_by_session(self):
        """Test counting messages in a session"""
        for i in range(5):
            message = ChatMessage(
                id=generate_ulid(),
                session_id=self.test_session.id,
                role='user',
                content=f'Message {i}'
            )
            self.repo.create(message)

        count = self.repo.count_by_session(self.test_session.id)
        self.assertEqual(count, 5)

    def test_delete_by_session(self):
        """Test deleting all messages for a session"""
        for i in range(3):
            message = ChatMessage(
                id=generate_ulid(),
                session_id=self.test_session.id,
                role='user',
                content=f'Message {i}'
            )
            self.repo.create(message)

        self.repo.delete_by_session(self.test_session.id)
        messages = self.repo.get_by_session(self.test_session.id)
        self.assertEqual(len(messages), 0)

    def _create_messages(self, count):
        ids = []
        for i in range(count):
            message = ChatMessage(
                id=generate_ulid(),
                session_id=self.test_session.id,
                role='user',
                content=f'Message {i}'
            )
            self.repo.create(message)
            ids.append(message.id)
        return ids

    def test_get_tail(self):
        """Tail returns only the last N messages, ascending"""
        self._create_messages(5)

        messages, has_earlier = self.repo.get_tail(self.test_session.id, 3)

        self.assertEqual([m.content for m in messages], ['Message 2', 'Message 3', 'Message 4'])
        self.assertTrue(has_earlier)

    def test_get_tail_no_earlier_when_limit_covers_all(self):
        """has_earlier is False once the window covers the whole session"""
        self._create_messages(3)

        messages, has_earlier = self.repo.get_tail(self.test_session.id, 5)

        self.assertEqual(len(messages), 3)
        self.assertFalse(has_earlier)

    def test_get_before_pages_through_session(self):
        """Paging with `before` in pages of 3 walks a 7-message session exactly"""
        ids = self._create_messages(7)

        page1, earlier1 = self.repo.get_before(self.test_session.id, ids[6], 3)
        self.assertEqual([m.content for m in page1], ['Message 3', 'Message 4', 'Message 5'])
        self.assertTrue(earlier1)

        page2, earlier2 = self.repo.get_before(self.test_session.id, ids[3], 3)
        self.assertEqual([m.content for m in page2], ['Message 0', 'Message 1', 'Message 2'])
        self.assertFalse(earlier2)

    def test_get_before_unknown_message_returns_none(self):
        """`before` not belonging to this session yields None (caller 404s)"""
        self._create_messages(2)

        result = self.repo.get_before(self.test_session.id, 'not-a-real-id', 3)

        self.assertIsNone(result)

    def test_get_before_message_from_other_session_returns_none(self):
        """`before` from a different session is also treated as not found"""
        self._create_messages(2)
        other_session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Other Session',
            status='active',
        )
        self.session_repo.create(other_session)
        other_message = ChatMessage(
            id=generate_ulid(),
            session_id=other_session.id,
            role='user',
            content='Elsewhere'
        )
        self.repo.create(other_message)

        result = self.repo.get_before(self.test_session.id, other_message.id, 3)

        self.assertIsNone(result)

    def test_ordering_tie_breaks_on_insertion_order(self):
        """Two messages sharing created_at still come back in insertion order"""
        ids = self._create_messages(2)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT created_at FROM chat_messages WHERE id = ?", (ids[0],)
            )
            shared_ts = cursor.fetchone()['created_at']
            cursor.execute(
                "UPDATE chat_messages SET created_at = ? WHERE id = ?", (shared_ts, ids[1])
            )

        messages = self.repo.get_by_session(self.test_session.id)

        self.assertEqual([m.id for m in messages], ids)

        tail_messages, _ = self.repo.get_tail(self.test_session.id, 2)
        self.assertEqual([m.id for m in tail_messages], ids)


class TestChatSessionRepository(PersistenceTestBase):
    """Tests for ChatSessionRepository"""

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = ChatSessionRepository()
        self.test_user_id = self.create_test_user()

    def tearDown(self):
        """Clean up test data"""
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM chat_messages")
                    cursor.execute("DELETE FROM chat_sessions")
                    cursor.execute("DELETE FROM users")
        except:
            pass
        super().tearDown()

    def test_create_session(self):
        """Test creating a new session"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test Session',
            status='active',
            llm_config_id='test-llm',
            original_text='Original prompt'
        )

        result = self.repo.create(session)
        self.assertTrue(result)

        retrieved = self.repo.get_by_id(session.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, 'Test Session')
        self.assertEqual(retrieved.status, 'active')
        self.assertEqual(retrieved.llm_config_id, 'test-llm')
        self.assertEqual(retrieved.original_text, 'Original prompt')

    def test_get_by_id(self):
        """Test getting a session by ID"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test Session',
            status='active'
        )
        self.repo.create(session)

        retrieved = self.repo.get_by_id(session.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, session.id)

    def test_get_by_id_nonexistent(self):
        """Test getting a nonexistent session"""
        result = self.repo.get_by_id('nonexistent-id')
        self.assertIsNone(result)

    def test_get_with_messages(self):
        """Test getting a session with its messages"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test Session',
            status='active'
        )
        self.repo.create(session)

        # Add messages
        message_repo = ChatMessageRepository()
        for i in range(3):
            message = ChatMessage(
                id=generate_ulid(),
                session_id=session.id,
                role='user',
                content=f'Message {i}'
            )
            message_repo.create(message)

        retrieved = self.repo.get_with_messages(session.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved.messages), 3)
        self.assertEqual(retrieved.message_count, 3)
        self.assertFalse(retrieved.has_earlier)

    def test_get_with_messages_tail(self):
        """Tail window on the session carries the true total and has_earlier"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test Session',
            status='active'
        )
        self.repo.create(session)

        message_repo = ChatMessageRepository()
        for i in range(5):
            message_repo.create(ChatMessage(
                id=generate_ulid(),
                session_id=session.id,
                role='user',
                content=f'Message {i}'
            ))

        retrieved = self.repo.get_with_messages_tail(session.id, 2)

        self.assertIsNotNone(retrieved)
        self.assertEqual([m.content for m in retrieved.messages], ['Message 3', 'Message 4'])
        self.assertEqual(retrieved.message_count, 5)
        self.assertTrue(retrieved.has_earlier)

    def test_get_with_messages_tail_nonexistent(self):
        """A tail lookup on a missing session returns None"""
        self.assertIsNone(self.repo.get_with_messages_tail('nonexistent', 2))

    def test_list_sessions(self):
        """Test getting recent sessions for a user"""
        # Create multiple sessions
        for i in range(5):
            session = ChatSession(
                id=generate_ulid(),
                user_id=self.test_user_id,
                mode='generation',
                name=f'Session {i}',
                status='active'
            )
            self.repo.create(session)

        sessions, total = self.repo.list_sessions(self.test_user_id, mode='generation')
        self.assertEqual(len(sessions), 5)
        self.assertEqual(total, 5)

    def test_list_sessions_with_limit(self):
        """Test getting recent sessions with a limit"""
        for i in range(5):
            session = ChatSession(
                id=generate_ulid(),
                user_id=self.test_user_id,
                mode='generation',
                name=f'Session {i}',
                status='active'
            )
            self.repo.create(session)

        sessions, total = self.repo.list_sessions(self.test_user_id, mode='generation', limit=3)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(total, 5)

    def test_list_sessions_with_status_filter(self):
        """Test getting recent sessions filtered by status"""
        # Create active sessions
        for i in range(3):
            session = ChatSession(
                id=generate_ulid(),
                user_id=self.test_user_id,
                mode='generation',
                name=f'Active {i}',
                status='active'
            )
            self.repo.create(session)

        # Create closed sessions
        for i in range(2):
            session = ChatSession(
                id=generate_ulid(),
                user_id=self.test_user_id,
                mode='generation',
                name=f'Accepted {i}',
                status='accepted'
            )
            self.repo.create(session)

        active_sessions, active_total = self.repo.list_sessions(
            self.test_user_id, mode='generation', status='active'
        )
        self.assertEqual(len(active_sessions), 3)
        self.assertEqual(active_total, 3)

        accepted_sessions, accepted_total = self.repo.list_sessions(
            self.test_user_id, mode='generation', status='accepted'
        )
        self.assertEqual(len(accepted_sessions), 2)
        self.assertEqual(accepted_total, 2)

    def test_list_sessions_status_filter_active(self):
        """Test getting only active sessions via status filter"""
        # Create active session
        active_session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Active',
            status='active'
        )
        self.repo.create(active_session)

        # Create closed session
        closed_session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Closed',
            status='accepted'
        )
        self.repo.create(closed_session)

        sessions, _total = self.repo.list_sessions(self.test_user_id, status='active')
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, 'Active')

    def test_update_status(self):
        """Test updating session status"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test',
            status='active'
        )
        self.repo.create(session)

        result = self.repo.update_status(session.id, 'accepted', close=True)
        self.assertTrue(result)

        retrieved = self.repo.get_by_id(session.id)
        self.assertEqual(retrieved.status, 'accepted')
        self.assertIsNotNone(retrieved.closed_at)

    def test_update_name(self):
        """Test updating session name"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Original Name',
            status='active'
        )
        self.repo.create(session)

        result = self.repo.update_name(session.id, 'New Name')
        self.assertTrue(result)

        retrieved = self.repo.get_by_id(session.id)
        self.assertEqual(retrieved.name, 'New Name')

    def test_delete_session(self):
        """Test deleting a session"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='To Delete',
            status='active'
        )
        self.repo.create(session)

        result = self.repo.delete(session.id)
        self.assertTrue(result)

        retrieved = self.repo.get_by_id(session.id)
        self.assertIsNone(retrieved)

    def test_exists(self):
        """Test checking if session exists"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=self.test_user_id,
            mode='generation',
            name='Test',
            status='active'
        )
        self.repo.create(session)

        self.assertTrue(self.repo.exists(session.id))
        self.assertFalse(self.repo.exists('nonexistent-id'))


class TestChatRepository(PersistenceTestBase):
    """Tests for high-level ChatRepository"""

    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = ChatRepository()
        self.test_user_id = self.create_test_user()

    def tearDown(self):
        """Clean up test data"""
        try:
            if hasattr(self, 'db'):
                with self.db.get_cursor() as cursor:
                    cursor.execute("DELETE FROM chat_messages")
                    cursor.execute("DELETE FROM chat_sessions")
                    cursor.execute("DELETE FROM users")
        except:
            pass
        super().tearDown()

    def test_create_session(self):
        """Test creating a session via high-level API"""
        session = self.repo.create_session(
            user_id=self.test_user_id,
            original_text='My prompt text',
            llm_config_id='llm-config-1',
            mode='generation',
            name='Custom Name'
        )

        self.assertIsNotNone(session)
        self.assertEqual(session.name, 'Custom Name')
        self.assertEqual(session.llm_config_id, 'llm-config-1')
        self.assertEqual(session.original_text, 'My prompt text')
        self.assertEqual(session.status, 'active')

    def test_create_session_auto_name(self):
        """Test that session name is auto-generated from original text"""
        session = self.repo.create_session(
            user_id=self.test_user_id,
            original_text='This is a very long original text that should be truncated for the name'
        )

        self.assertIsNotNone(session)
        self.assertTrue(session.name.startswith('This is a very long original'))
        self.assertTrue('...' in session.name)

    def test_create_session_auto_name_no_text(self):
        """Test that session name is generated when no text is provided"""
        session = self.repo.create_session(
            user_id=self.test_user_id
        )

        self.assertIsNotNone(session)
        self.assertTrue(session.name.startswith('Chat '))

    def test_add_message(self):
        """Test adding a message to a session"""
        session = self.repo.create_session(user_id=self.test_user_id)

        message = self.repo.add_message(
            session_id=session.id,
            role='user',
            content='Hello!'
        )

        self.assertIsNotNone(message)
        self.assertEqual(message.role, 'user')
        self.assertEqual(message.content, 'Hello!')

    def test_completion_outcome_round_trips_through_persisted_metadata(self):
        """`assistant_metadata['behavior_trace']['completion']` (see
        ConversationRunner._build_behavior_trace — LLM-08's normalized
        completion contract) must survive a real DB round-trip: it is what
        the frontend's output-limit note reads
        (metadata.behavior_trace.completion.reason), so a fetch that only
        echoes back the object `add_message` was handed, without a real
        write, would not be caught by asserting on that return value alone."""
        session = self.repo.create_session(user_id=self.test_user_id)

        self.repo.add_message(
            session_id=session.id,
            role='assistant',
            content='cut off mid',
            metadata={'behavior_trace': {'completion': {'reason': 'length', 'raw': 'length'}}},
        )

        retrieved = self.repo.get_messages(session.id)
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(
            retrieved[0].metadata['behavior_trace']['completion'],
            {'reason': 'length', 'raw': 'length'},
        )

    def test_add_message_to_nonexistent_session(self):
        """Test adding a message to a nonexistent session"""
        message = self.repo.add_message(
            session_id='nonexistent',
            role='user',
            content='Hello!'
        )

        self.assertIsNone(message)

    def test_get_conversation_history(self):
        """Test getting conversation history for LLM"""
        session = self.repo.create_session(user_id=self.test_user_id)

        self.repo.add_message(session.id, 'user', 'Hello')
        self.repo.add_message(session.id, 'assistant', 'Hi there!')
        self.repo.add_message(session.id, 'user', 'How are you?')

        history = self.repo.get_conversation_history(session.id)

        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]['role'], 'user')
        self.assertEqual(history[0]['content'], 'Hello')
        self.assertEqual(history[1]['role'], 'assistant')
        self.assertEqual(history[2]['role'], 'user')

    def test_accept_session(self):
        """Test accepting a session"""
        session = self.repo.create_session(user_id=self.test_user_id)

        result = self.repo.accept_session(session.id)
        self.assertTrue(result)

        retrieved = self.repo.get_session(session.id)
        self.assertEqual(retrieved.status, 'accepted')
        self.assertIsNotNone(retrieved.closed_at)

    def test_reject_session(self):
        """Test rejecting a session"""
        session = self.repo.create_session(user_id=self.test_user_id)

        result = self.repo.reject_session(session.id)
        self.assertTrue(result)

        retrieved = self.repo.get_session(session.id)
        self.assertEqual(retrieved.status, 'rejected')
        self.assertIsNotNone(retrieved.closed_at)

    def test_update_session_name(self):
        """Test updating session name"""
        session = self.repo.create_session(
            user_id=self.test_user_id,
            name='Original'
        )

        result = self.repo.update_session_name(session.id, 'Updated Name')
        self.assertTrue(result)

        retrieved = self.repo.get_session(session.id)
        self.assertEqual(retrieved.name, 'Updated Name')

    def test_update_session_llm_config(self):
        """Test rebinding a session to a different LLM config"""
        session = self.repo.create_session(
            user_id=self.test_user_id,
            llm_config_id='llm-a'
        )

        result = self.repo.update_session_llm_config(session.id, 'llm-b')
        self.assertTrue(result)

        retrieved = self.repo.get_session(session.id)
        self.assertEqual(retrieved.llm_config_id, 'llm-b')

    def test_delete_all_sessions_wipes_every_user(self):
        """Admin wipe removes every session (and, via CASCADE, its messages)
        regardless of owner, and reports how many went."""
        a = self.repo.create_session(user_id=self.test_user_id)
        b = self.repo.create_session(user_id=self.test_user_id)
        self.repo.add_message(a.id, 'user', 'Hello')

        self.assertEqual(self.repo.delete_all_sessions(), 2)

        self.assertIsNone(self.repo.get_session(a.id))
        self.assertIsNone(self.repo.get_session(b.id))
        self.assertEqual(len(self.repo.get_messages(a.id)), 0)
        self.assertEqual(self.repo.delete_all_sessions(), 0)

    def test_delete_session(self):
        """Test deleting a session"""
        session = self.repo.create_session(user_id=self.test_user_id)
        self.repo.add_message(session.id, 'user', 'Hello')
        self.repo.add_message(session.id, 'assistant', 'Hi!')

        result = self.repo.delete_session(session.id)
        self.assertTrue(result)

        retrieved = self.repo.get_session(session.id)
        self.assertIsNone(retrieved)

        # Messages should also be deleted (CASCADE)
        messages = self.repo.get_messages(session.id)
        self.assertEqual(len(messages), 0)

    def test_list_sessions_includes_closed(self):
        """Listing without a status filter includes closed sessions"""
        # Create active session
        self.repo.create_session(user_id=self.test_user_id, name='Active')

        # Create and close a session
        session = self.repo.create_session(user_id=self.test_user_id, name='Closed')
        self.repo.accept_session(session.id)

        sessions, total = self.repo.list_sessions(user_id=self.test_user_id)

        self.assertEqual(len(sessions), 2)
        self.assertEqual(total, 2)

    def test_list_sessions_excludes_closed_via_status(self):
        """Listing with status='active' excludes closed sessions"""
        # Create active session
        self.repo.create_session(user_id=self.test_user_id, name='Active')

        # Create and close a session
        session = self.repo.create_session(user_id=self.test_user_id, name='Closed')
        self.repo.accept_session(session.id)

        sessions, total = self.repo.list_sessions(
            user_id=self.test_user_id,
            status='active'
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(total, 1)
        self.assertEqual(sessions[0].name, 'Active')

    def test_list_sessions_search_and_pagination(self):
        """Name search and limit/offset pagination work together"""
        for i in range(4):
            self.repo.create_session(user_id=self.test_user_id, name=f'Fox chat {i}')
        self.repo.create_session(user_id=self.test_user_id, name='Unrelated')

        sessions, total = self.repo.list_sessions(
            user_id=self.test_user_id, search='Fox', limit=2, offset=0
        )
        self.assertEqual(total, 4)
        self.assertEqual(len(sessions), 2)

        page2, total2 = self.repo.list_sessions(
            user_id=self.test_user_id, search='Fox', limit=2, offset=2
        )
        self.assertEqual(total2, 4)
        self.assertEqual(len(page2), 2)
        self.assertNotEqual({s.id for s in sessions}, {s.id for s in page2})

    def test_list_sessions_message_count(self):
        """Session listing includes per-session message counts"""
        session = self.repo.create_session(user_id=self.test_user_id, name='Counted')
        self.repo.add_message(session.id, 'user', 'Hello')
        self.repo.add_message(session.id, 'assistant', 'Hi!')

        sessions, _total = self.repo.list_sessions(user_id=self.test_user_id)
        self.assertEqual(sessions[0].message_count, 2)

    def test_set_session_title(self):
        """set_session_title sets the name and title_generated flag"""
        session = self.repo.create_session(
            user_id=self.test_user_id, original_text='some placeholder text'
        )
        self.assertFalse(session.title_generated)

        updated = self.repo.set_session_title(session.id, 'A Neat Title')
        self.assertIsNotNone(updated)
        self.assertEqual(updated.name, 'A Neat Title')
        self.assertTrue(updated.title_generated)

    def test_session_mode_persisted(self):
        """Sessions persist their chat mode"""
        session = self.repo.create_session(user_id=self.test_user_id, mode='generation')
        retrieved = self.repo.get_session(session.id)
        self.assertEqual(retrieved.mode, 'generation')


if __name__ == '__main__':
    unittest.main()
