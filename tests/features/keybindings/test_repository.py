import unittest
import sys
import os

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.keybindings.repository import KeybindingRepository


class TestKeybindingRepository(PersistenceTestBase):
    """Test keybinding repository operations"""

    def setUp(self):
        super().setUp()
        self.repo = KeybindingRepository()
        import src.features.keybindings.repository
        src.features.keybindings.repository.db = self.db
        self.user_id = self.create_test_user("user-1", "user1", "user1@test.com")

    # ========== Default Keybinding Tests ==========

    def test_get_all_defaults(self):
        """Test retrieving all default keybindings"""
        defaults = self.repo.get_all_defaults()
        self.assertIsInstance(defaults, list)
        self.assertGreater(len(defaults), 0)

    def test_defaults_seeded_correctly(self):
        """Test that migration seeds the expected defaults"""
        defaults = self.repo.get_all_defaults()
        ids = [d.id for d in defaults]
        self.assertIn('show_help', ids)
        self.assertIn('open_chat', ids)
        self.assertIn('start_generation', ids)
        self.assertIn('go_generate', ids)
        self.assertIn('go_history', ids)
        self.assertIn('go_models', ids)
        self.assertIn('go_phrasebook', ids)
        self.assertIn('go_prompts', ids)
        self.assertIn('go_library', ids)
        self.assertIn('go_inspirations', ids)
        self.assertIn('new_tab', ids)
        self.assertIn('close_tab', ids)
        self.assertIn('toggle_sidebar', ids)

    def test_default_keybinding_fields(self):
        """Test that default keybindings have all expected fields"""
        defaults = self.repo.get_all_defaults()
        show_help = next(d for d in defaults if d.id == 'show_help')
        self.assertEqual(show_help.key, '?')
        self.assertEqual(show_help.modifiers, '')
        self.assertEqual(show_help.label, 'Show Keyboard Shortcuts')
        self.assertEqual(show_help.category, 'general')
        self.assertEqual(show_help.context, 'global')
        self.assertTrue(show_help.enabled)
        self.assertEqual(show_help.source, 'system')

    def test_defaults_sorted_by_sort_order(self):
        """Test that defaults are returned sorted by sort_order"""
        defaults = self.repo.get_all_defaults()
        sort_orders = [d.sort_order for d in defaults]
        self.assertEqual(sort_orders, sorted(sort_orders))

    # ========== User Override Tests ==========

    def test_set_user_keybinding(self):
        """Test creating a user keybinding override"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        overrides = self.repo.get_user_overrides(self.user_id)
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0].action_id, 'show_help')
        self.assertEqual(overrides[0].key, 'h')
        self.assertEqual(overrides[0].modifiers, 'ctrl')

    def test_set_user_keybinding_upsert(self):
        """Test that setting a keybinding twice updates instead of duplicating"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'x', 'alt')
        overrides = self.repo.get_user_overrides(self.user_id)
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0].key, 'x')
        self.assertEqual(overrides[0].modifiers, 'alt')

    def test_get_user_overrides_empty(self):
        """Test getting overrides when none exist"""
        overrides = self.repo.get_user_overrides(self.user_id)
        self.assertEqual(len(overrides), 0)

    def test_get_user_overrides_multiple(self):
        """Test getting multiple user overrides"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        self.repo.set_user_keybinding(self.user_id, 'open_chat', 'o', '')
        overrides = self.repo.get_user_overrides(self.user_id)
        self.assertEqual(len(overrides), 2)

    # ========== Effective Keybindings Tests ==========

    def test_effective_keybindings_no_overrides(self):
        """Test effective keybindings without any user overrides"""
        effective = self.repo.get_effective_keybindings(self.user_id)
        self.assertIsInstance(effective, list)
        self.assertGreater(len(effective), 0)
        for kb in effective:
            self.assertFalse(kb['is_custom'])

    def test_effective_keybindings_with_override(self):
        """Test effective keybindings with a user override"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        effective = self.repo.get_effective_keybindings(self.user_id)

        show_help = next(kb for kb in effective if kb['action_id'] == 'show_help')
        self.assertTrue(show_help['is_custom'])
        self.assertEqual(show_help['key'], 'h')
        self.assertEqual(show_help['modifiers'], 'ctrl')
        # Label should still come from the default
        self.assertEqual(show_help['label'], 'Show Keyboard Shortcuts')

    def test_effective_keybindings_partial_override(self):
        """Test that non-overridden keybindings retain default values"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        effective = self.repo.get_effective_keybindings(self.user_id)

        open_chat = next(kb for kb in effective if kb['action_id'] == 'open_chat')
        self.assertFalse(open_chat['is_custom'])
        self.assertEqual(open_chat['key'], 'c')

    def test_effective_keybindings_has_all_fields(self):
        """Test that effective keybindings contain all required fields"""
        effective = self.repo.get_effective_keybindings(self.user_id)
        required_fields = ['action_id', 'key', 'modifiers', 'label', 'category',
                          'context', 'description', 'enabled', 'is_custom']
        for kb in effective:
            for field in required_fields:
                self.assertIn(field, kb)

    # ========== Reset Tests ==========

    def test_reset_user_keybinding(self):
        """Test resetting a single user keybinding"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        result = self.repo.reset_user_keybinding(self.user_id, 'show_help')
        self.assertTrue(result)
        overrides = self.repo.get_user_overrides(self.user_id)
        self.assertEqual(len(overrides), 0)

    def test_reset_user_keybinding_nonexistent(self):
        """Test resetting a keybinding that has no override"""
        result = self.repo.reset_user_keybinding(self.user_id, 'show_help')
        self.assertFalse(result)

    def test_reset_all_user_keybindings(self):
        """Test resetting all user keybindings"""
        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        self.repo.set_user_keybinding(self.user_id, 'open_chat', 'o', '')
        self.repo.set_user_keybinding(self.user_id, 'start_generation', 'g', 'ctrl')

        count = self.repo.reset_all_user_keybindings(self.user_id)
        self.assertEqual(count, 3)
        overrides = self.repo.get_user_overrides(self.user_id)
        self.assertEqual(len(overrides), 0)

    def test_reset_all_user_keybindings_empty(self):
        """Test resetting when no overrides exist"""
        count = self.repo.reset_all_user_keybindings(self.user_id)
        self.assertEqual(count, 0)

    # ========== Plugin Registration Tests ==========

    def test_register_default(self):
        """Test registering a new default keybinding"""
        self.repo.register_default(
            id='plugin_action',
            key='p',
            modifiers='ctrl+shift',
            label='Plugin Action',
            category='plugins',
            context='global',
            description='A plugin action',
            source='my-plugin',
            sort_order=100
        )
        defaults = self.repo.get_all_defaults()
        plugin_action = next((d for d in defaults if d.id == 'plugin_action'), None)
        self.assertIsNotNone(plugin_action)
        self.assertEqual(plugin_action.key, 'p')
        self.assertEqual(plugin_action.modifiers, 'ctrl+shift')
        self.assertEqual(plugin_action.source, 'my-plugin')

    def test_register_default_replaces_existing(self):
        """Test that registering with same id replaces the existing one"""
        self.repo.register_default(
            id='show_help',
            key='F1',
            modifiers='',
            label='Help (Replaced)',
            source='plugin-override'
        )
        defaults = self.repo.get_all_defaults()
        show_help = next(d for d in defaults if d.id == 'show_help')
        self.assertEqual(show_help.key, 'F1')
        self.assertEqual(show_help.label, 'Help (Replaced)')
        self.assertEqual(show_help.source, 'plugin-override')

    def test_unregister_defaults_by_source(self):
        """Test unregistering all defaults from a specific source"""
        self.repo.register_default(id='pa1', key='a', label='PA1', source='test-plugin')
        self.repo.register_default(id='pa2', key='b', label='PA2', source='test-plugin')
        self.repo.register_default(id='pa3', key='c', label='PA3', source='other-plugin')

        count = self.repo.unregister_defaults_by_source('test-plugin')
        self.assertEqual(count, 2)

        defaults = self.repo.get_all_defaults()
        ids = [d.id for d in defaults]
        self.assertNotIn('pa1', ids)
        self.assertNotIn('pa2', ids)
        self.assertIn('pa3', ids)

    def test_unregister_defaults_by_source_nonexistent(self):
        """Test unregistering from a source with no defaults"""
        count = self.repo.unregister_defaults_by_source('nonexistent-plugin')
        self.assertEqual(count, 0)

    # ========== User Isolation Tests ==========

    def test_user_overrides_isolated_per_user(self):
        """Test that user overrides are isolated per user"""
        user2_id = self.create_test_user("user-2", "user2", "user2@test.com")

        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        self.repo.set_user_keybinding(user2_id, 'show_help', 'x', 'alt')

        user1_overrides = self.repo.get_user_overrides(self.user_id)
        user2_overrides = self.repo.get_user_overrides(user2_id)

        self.assertEqual(len(user1_overrides), 1)
        self.assertEqual(user1_overrides[0].key, 'h')

        self.assertEqual(len(user2_overrides), 1)
        self.assertEqual(user2_overrides[0].key, 'x')

    def test_reset_all_only_affects_target_user(self):
        """Test that reset all only affects the target user"""
        user2_id = self.create_test_user("user-2", "user2", "user2@test.com")

        self.repo.set_user_keybinding(self.user_id, 'show_help', 'h', 'ctrl')
        self.repo.set_user_keybinding(user2_id, 'show_help', 'x', 'alt')

        self.repo.reset_all_user_keybindings(self.user_id)

        user1_overrides = self.repo.get_user_overrides(self.user_id)
        user2_overrides = self.repo.get_user_overrides(user2_id)

        self.assertEqual(len(user1_overrides), 0)
        self.assertEqual(len(user2_overrides), 1)


if __name__ == '__main__':
    unittest.main()
