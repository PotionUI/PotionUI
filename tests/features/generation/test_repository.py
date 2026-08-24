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
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository
try:
    from src.platform.util.ids import generate_ulid
except ImportError:
    # Mock for testing
    def generate_ulid():
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=26))


class TestGenerationRepository(PersistenceTestBase):
    
    def setUp(self):
        """Set up test data"""
        super().setUp()
        self.repo = GenerationRepository()
        self.test_user_id = self.create_test_user()
        
        self.test_form_data = {
            "prompt": "test prompt",
            "steps": 20,
            "cfg_scale": 7.5
        }
        
        self.test_generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="pending",
            preset_version="1.0"
        )
    
    def test_create_generation(self):
        """Test creating a new generation"""
        created = self.repo.create(self.test_generation)
        
        self.assertIsNotNone(created)
        self.assertEqual(created.id, self.test_generation.id)
        self.assertEqual(created.preset_id, self.test_generation.preset_id)
        self.assertEqual(created.form_data, self.test_generation.form_data)
        self.assertEqual(created.user_id, self.test_generation.user_id)
        self.assertEqual(created.status, "pending")
        self.assertIsNotNone(created.created_at)

    def test_create_generation_persists_backend_id(self):
        """The backend that ran a generation must survive to history, otherwise two
        backends of the same engine are indistinguishable after the fact."""
        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="pending",
            backend_id="comfy-runpod-1"
        )

        self.repo.create(generation)
        fetched = self.repo.get_by_id(generation.id)

        self.assertEqual(fetched.backend_id, "comfy-runpod-1")
        self.assertEqual(fetched.to_dict()['backend_id'], "comfy-runpod-1")

    def test_create_generation_without_backend_id(self):
        """Imported/uploaded generations have no backend; the column stays NULL."""
        created = self.repo.create(self.test_generation)

        self.assertIsNone(created.backend_id)

    def test_create_generation_persists_mode_and_prompt_state(self):
        """Test that a non-default mode and nested prompt_state round-trip via get_by_id"""
        prompt_state = {
            'segments': [{'text': 'a cat', 'weight': 1.0}],
            'chips': ['chip_a', 'chip_b'],
            'timeline': {'steps': [1, 2, 3]}
        }
        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="pending",
            preset_version="1.0",
            mode="img2img",
            prompt_state=prompt_state
        )

        created = self.repo.create(generation)
        retrieved = self.repo.get_by_id(created.id)

        self.assertEqual(created.mode, "img2img")
        self.assertEqual(created.prompt_state, prompt_state)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.mode, "img2img")
        self.assertEqual(retrieved.prompt_state, prompt_state)

    def test_create_generation_persists_form_name(self):
        """The resolved preset form variant (migration 093) must round-trip via
        get_by_id, otherwise history/reuse can't tell which variant actually ran."""
        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="pending",
            form_name="advanced"
        )

        created = self.repo.create(generation)
        retrieved = self.repo.get_by_id(created.id)

        self.assertEqual(created.form_name, "advanced")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.form_name, "advanced")
        self.assertEqual(retrieved.to_dict()['form_name'], "advanced")

    def test_create_generation_without_form_name(self):
        """Rows created with no resolved variant (or before migration 093) stay NULL —
        never guessed to a default variant name."""
        created = self.repo.create(self.test_generation)

        self.assertIsNone(created.form_name)

    def test_get_by_id(self):
        """Test getting generation by ID"""
        created = self.repo.create(self.test_generation)
        retrieved = self.repo.get_by_id(created.id)
        
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, created.id)
        self.assertEqual(retrieved.preset_id, created.preset_id)
        self.assertEqual(retrieved.form_data, created.form_data)
    
    def test_get_by_id_with_user_filter(self):
        """Test getting generation by ID with user filter"""
        created = self.repo.create(self.test_generation)
        
        # Should find with correct user_id
        retrieved = self.repo.get_by_id(created.id, user_id=self.test_user_id)
        self.assertIsNotNone(retrieved)
        
        # Should not find with incorrect user_id
        retrieved = self.repo.get_by_id(created.id, user_id="wrong_user")
        self.assertIsNone(retrieved)
    
    def test_get_by_id_nonexistent(self):
        """Test getting nonexistent generation"""
        result = self.repo.get_by_id("nonexistent_id")
        self.assertIsNone(result)
    
    def test_get_all(self):
        """Test getting all generations"""
        # Create multiple generations
        gen1 = self.repo.create(self.test_generation)
        
        gen2_data = self.test_generation
        gen2_data.id = generate_ulid()
        gen2_data.preset_id = "another_preset"
        gen2 = self.repo.create(gen2_data)
        
        all_generations = self.repo.get_all()
        
        self.assertGreaterEqual(len(all_generations), 2)
        gen_ids = [g.id for g in all_generations]
        self.assertIn(gen1.id, gen_ids)
        self.assertIn(gen2.id, gen_ids)
    
    def test_get_all_with_user_filter(self):
        """Test getting generations filtered by user"""
        # Create generation for test user
        gen1 = self.repo.create(self.test_generation)
        
        # Create another user and generation
        other_user_id = self.create_test_user("other_user", "other@example.com", "other@example.com")
        gen2_data = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=other_user_id
        )
        gen2 = self.repo.create(gen2_data)
        
        # Filter by first user
        user_generations = self.repo.get_all(user_id=self.test_user_id)
        gen_ids = [g.id for g in user_generations]
        
        self.assertIn(gen1.id, gen_ids)
        self.assertNotIn(gen2.id, gen_ids)
    
    def test_get_all_with_status_filter(self):
        """Test getting generations filtered by status"""
        # Create generations with different statuses
        gen1 = self.repo.create(self.test_generation)
        
        gen2_data = self.test_generation
        gen2_data.id = generate_ulid()
        gen2_data.status = "completed"
        gen2 = self.repo.create(gen2_data)
        
        # Filter by status
        pending_generations = self.repo.get_all(status="pending")
        completed_generations = self.repo.get_all(status="completed")
        
        pending_ids = [g.id for g in pending_generations]
        completed_ids = [g.id for g in completed_generations]
        
        self.assertIn(gen1.id, pending_ids)
        self.assertNotIn(gen1.id, completed_ids)
        self.assertIn(gen2.id, completed_ids)
        self.assertNotIn(gen2.id, pending_ids)
    
    def test_get_all_with_limit_offset(self):
        """Test getting generations with pagination"""
        # Create multiple generations
        generations = []
        for i in range(5):
            gen_data = Generation(
                id=generate_ulid(),
                preset_id=f"preset_{i}",
                form_data={"index": i},
                user_id=self.test_user_id
            )
            generations.append(self.repo.create(gen_data))
        
        # Test limit
        limited = self.repo.get_all(limit=3)
        self.assertEqual(len(limited), 3)
        
        # Test offset
        offset_results = self.repo.get_all(limit=2, offset=2)
        self.assertEqual(len(offset_results), 2)
        
        # Results should be different due to offset
        limited_ids = {g.id for g in limited[:2]}
        offset_ids = {g.id for g in offset_results}
        self.assertNotEqual(limited_ids, offset_ids)
    
    def test_update_status(self):
        """Test updating generation status"""
        created = self.repo.create(self.test_generation)
        
        # Update to running
        success = self.repo.update_status(created.id, "running")
        self.assertTrue(success)
        
        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.status, "running")
        self.assertIsNotNone(updated.started_at)
        
        # Update to completed
        success = self.repo.update_status(created.id, "completed")
        self.assertTrue(success)
        
        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.status, "completed")
        self.assertIsNotNone(updated.completed_at)
    
    def test_update_status_nonexistent(self):
        """Test updating status of nonexistent generation"""
        success = self.repo.update_status("nonexistent_id", "running")
        self.assertFalse(success)

    def test_update_status_failed_persists_error_message(self):
        """A failed transition's error_message must actually land in the
        database - GenerationStatusTracker.transition() passes it through
        unconditionally, so any generation that fails with a reason must
        never read back with error_message NULL."""
        created = self.repo.create(self.test_generation)

        success = self.repo.update_status(created.id, "failed", error_message="checkpoint not found: foo.safetensors")
        self.assertTrue(success)

        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.error_message, "checkpoint not found: foo.safetensors")

    def test_update_status_cancelled_persists_error_message(self):
        created = self.repo.create(self.test_generation)

        self.repo.update_status(created.id, "cancelled", error_message="cancelled by user")

        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.status, "cancelled")
        self.assertEqual(updated.error_message, "cancelled by user")

    def test_update_status_completed_leaves_error_message_null(self):
        created = self.repo.create(self.test_generation)

        self.repo.update_status(created.id, "completed")

        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.status, "completed")
        self.assertIsNone(updated.error_message)
    
    def test_update_progress(self):
        """Test updating generation progress"""
        created = self.repo.create(self.test_generation)
        
        success = self.repo.update_progress(created.id, 0.75)
        self.assertTrue(success)

        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.progress, 0.75)
    
    def test_update_progress_partial(self):
        """Test updating only progress without other fields"""
        created = self.repo.create(self.test_generation)

        success = self.repo.update_progress(created.id, 0.5)
        self.assertTrue(success)

        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.progress, 0.5)

    def test_update_preset_version(self):
        """Test recording the preset version that actually rendered the pipeline"""
        created = self.repo.create(self.test_generation)

        success = self.repo.update_preset_version(created.id, "2.3")
        self.assertTrue(success)

        updated = self.repo.get_by_id(created.id)
        self.assertEqual(updated.preset_version, "2.3")

    def test_update_preset_version_nonexistent(self):
        """Test updating preset version of nonexistent generation"""
        success = self.repo.update_preset_version("nonexistent_id", "2.3")
        self.assertFalse(success)

    def test_delete(self):
        """Test deleting generation"""
        created = self.repo.create(self.test_generation)
        
        success = self.repo.delete(created.id)
        self.assertTrue(success)
        
        # Should not be found after deletion
        deleted = self.repo.get_by_id(created.id)
        self.assertIsNone(deleted)
    
    def test_delete_nonexistent(self):
        """Test deleting nonexistent generation"""
        success = self.repo.delete("nonexistent_id")
        self.assertFalse(success)
    
    def test_get_active_generations(self):
        """Test getting active generations"""
        # Create generations with different statuses
        pending_gen = self.repo.create(self.test_generation)
        
        running_gen_data = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="running"
        )
        running_gen = self.repo.create(running_gen_data)
        
        completed_gen_data = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="completed"
        )
        completed_gen = self.repo.create(completed_gen_data)
        
        active = self.repo.get_active_generations()
        active_ids = [g.id for g in active]
        
        self.assertIn(pending_gen.id, active_ids)
        self.assertIn(running_gen.id, active_ids)
        self.assertNotIn(completed_gen.id, active_ids)
    
    def test_count_by_status(self):
        """Test counting generations by status"""
        # Create generations with different statuses
        for i in range(3):
            gen_data = Generation(
                id=generate_ulid(),
                preset_id="test_preset",
                form_data=self.test_form_data,
                user_id=self.test_user_id,
                status="pending"
            )
            self.repo.create(gen_data)
        
        for i in range(2):
            gen_data = Generation(
                id=generate_ulid(),
                preset_id="test_preset",
                form_data=self.test_form_data,
                user_id=self.test_user_id,
                status="completed"
            )
            self.repo.create(gen_data)
        
        pending_count = self.repo.count_by_status(user_id=self.test_user_id, status="pending")
        completed_count = self.repo.count_by_status(user_id=self.test_user_id, status="completed")
        total_count = self.repo.count_by_status(user_id=self.test_user_id)
        
        self.assertEqual(pending_count, 3)
        self.assertEqual(completed_count, 2)
        self.assertEqual(total_count, 5)

    def test_reconcile_interrupted_generations_fails_pending_and_running(self):
        """A restart can't mean a pending/running row is still executing -- generation
        state lives only in-process. The boot-time sweep must fail both."""
        pending_data = self.test_generation
        pending = self.repo.create(pending_data)

        running_data = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="running"
        )
        running = self.repo.create(running_data)

        count = self.repo.reconcile_interrupted_generations()
        self.assertEqual(count, 2)

        reconciled_pending = self.repo.get_by_id(pending.id)
        reconciled_running = self.repo.get_by_id(running.id)

        self.assertEqual(reconciled_pending.status, "failed")
        self.assertIsNotNone(reconciled_pending.completed_at)
        self.assertEqual(reconciled_running.status, "failed")
        self.assertIsNotNone(reconciled_running.completed_at)

    def test_reconcile_interrupted_generations_leaves_terminal_rows_untouched(self):
        """Rows that already reached a terminal status must not be touched by the
        sweep, and their `updated_at` must not be bumped."""
        for status in ("completed", "failed", "cancelled"):
            gen_data = Generation(
                id=generate_ulid(),
                preset_id="test_preset",
                form_data=self.test_form_data,
                user_id=self.test_user_id,
                status=status
            )
            self.repo.create(gen_data)

        before = {g.id: g.updated_at for g in self.repo.get_all(user_id=self.test_user_id)}

        count = self.repo.reconcile_interrupted_generations()
        self.assertEqual(count, 0)

        after = self.repo.get_all(user_id=self.test_user_id)
        for gen in after:
            self.assertIn(gen.status, ("completed", "failed", "cancelled"))
            self.assertEqual(gen.updated_at, before[gen.id])

    def test_reconcile_interrupted_generations_noop_when_nothing_stranded(self):
        """No pending/running rows at all -- the sweep must return 0."""
        gen_data = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id=self.test_user_id,
            status="completed"
        )
        self.repo.create(gen_data)

        count = self.repo.reconcile_interrupted_generations()
        self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()