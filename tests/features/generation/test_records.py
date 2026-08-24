import unittest
from datetime import datetime
import json
from src.features.generation.records import Generation, File, GenerationFile


class TestGenerationModel(unittest.TestCase):
    
    def setUp(self):
        """Set up test data"""
        self.test_form_data = {
            "prompt": "test prompt",
            "steps": 20,
            "cfg_scale": 7.5
        }
        
        self.test_generation = Generation(
            id="test_gen_123",
            preset_id="test_preset",
            form_data=self.test_form_data,
            user_id="user_123",
            status="pending",
            preset_version="1.0",
            backend_id="comfy-1",
            tab_id="tab_1",
            progress=0.5,
            mode="img2img",
            prompt_state={"segments": [{"text": "a cat"}]},
            form_name="advanced",
            rating=3,
            is_favorite=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            started_at=datetime(2024, 1, 1, 12, 1, 0),
            completed_at=None,
            updated_at=datetime(2024, 1, 1, 12, 2, 0),
            duration_ms=1500
        )

    def test_generation_creation(self):
        """Test Generation model creation"""
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"key": "value"},
            user_id="user_1"
        )

        self.assertEqual(gen.id, "test_id")
        self.assertEqual(gen.preset_id, "preset_1")
        self.assertEqual(gen.form_data, {"key": "value"})
        self.assertEqual(gen.user_id, "user_1")
        self.assertEqual(gen.status, "pending")
        self.assertEqual(gen.progress, 0.0)
        self.assertIsNone(gen.backend_id)
        self.assertIsNone(gen.tab_id)
        self.assertEqual(gen.mode, "txt2img")
        self.assertIsNone(gen.prompt_state)
        self.assertIsNone(gen.form_name)
        self.assertEqual(gen.rating, 0)
        self.assertFalse(gen.is_favorite)
        self.assertIsNone(gen.duration_ms)

    def test_from_row(self):
        """Test creating Generation from database row"""
        mock_row = {
            'id': 'test_gen_123',
            'preset_id': 'test_preset',
            'preset_version': '1.0',
            'form_data': json.dumps(self.test_form_data),
            'user_id': 'user_123',
            'status': 'running',
            'backend_id': 'comfy-1',
            'tab_id': 'tab_1',
            'progress': 0.75,
            'mode': 'img2img',
            'prompt_state': json.dumps({'segments': [{'text': 'a cat'}]}),
            'form_name': 'advanced',
            'rating': 4,
            'is_favorite': 1,
            'duration_ms': 2500,
            'created_at': '2024-01-01T12:00:00',
            'started_at': '2024-01-01T12:01:00',
            'completed_at': None,
            'updated_at': '2024-01-01T12:02:00'
        }

        generation = Generation.from_row(mock_row)

        self.assertEqual(generation.id, 'test_gen_123')
        self.assertEqual(generation.preset_id, 'test_preset')
        self.assertEqual(generation.preset_version, '1.0')
        self.assertEqual(generation.form_data, self.test_form_data)
        self.assertEqual(generation.user_id, 'user_123')
        self.assertEqual(generation.status, 'running')
        self.assertEqual(generation.backend_id, 'comfy-1')
        self.assertEqual(generation.tab_id, 'tab_1')
        self.assertEqual(generation.progress, 0.75)
        self.assertEqual(generation.mode, 'img2img')
        self.assertEqual(generation.prompt_state, {'segments': [{'text': 'a cat'}]})
        self.assertEqual(generation.form_name, 'advanced')
        self.assertEqual(generation.rating, 4)
        self.assertTrue(generation.is_favorite)
        self.assertEqual(generation.duration_ms, 2500)
        self.assertEqual(generation.created_at, datetime(2024, 1, 1, 12, 0, 0))
        self.assertEqual(generation.started_at, datetime(2024, 1, 1, 12, 1, 0))
        self.assertIsNone(generation.completed_at)
        self.assertEqual(generation.updated_at, datetime(2024, 1, 1, 12, 2, 0))

    def test_to_dict_without_files(self):
        """Test converting Generation to dictionary without files"""
        result = self.test_generation.to_dict(include_files=False)

        expected = {
            'id': 'test_gen_123',
            'preset_id': 'test_preset',
            'preset_version': '1.0',
            'form_data': self.test_form_data,
            'user_id': 'user_123',
            'status': 'pending',
            'backend_id': 'comfy-1',
            'tab_id': 'tab_1',
            'progress': 0.5,
            'mode': 'img2img',
            'prompt_state': {'segments': [{'text': 'a cat'}]},
            'form_name': 'advanced',
            'source_prompt_id': None,
            'seed': None,
            'rating': 3,
            'is_favorite': True,
            'duration_ms': 1500,
            'error_message': None,
            'created_at': '2024-01-01T12:00:00',
            'started_at': '2024-01-01T12:01:00',
            'completed_at': None,
            'updated_at': '2024-01-01T12:02:00'
        }

        self.assertEqual(result, expected)
    
    def test_to_dict_with_files(self):
        """Test converting Generation to dictionary with files"""
        test_file = File(
            id="file_1",
            file_path="/test/image.jpg",
            file_type="image",
            user_id="user_123",
            file_size=1024,
            pipe_name="generator",
            is_final=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        )
        
        self.test_generation.files = [test_file]
        result = self.test_generation.to_dict(include_files=True)
        
        self.assertIn('files', result)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['files'][0]['id'], "file_1")
        self.assertEqual(result['files'][0]['file_path'], "/test/image.jpg")
    
    def test_serialize_form_data(self):
        """Test serializing form data to JSON"""
        json_str = self.test_generation.serialize_form_data()
        parsed = json.loads(json_str)
        self.assertEqual(parsed, self.test_form_data)
    
    def test_is_active(self):
        """Test checking if generation is active"""
        # Test pending status
        self.test_generation.status = 'pending'
        self.assertTrue(self.test_generation.is_active())
        
        # Test running status
        self.test_generation.status = 'running'
        self.assertTrue(self.test_generation.is_active())
        
        # Test completed status
        self.test_generation.status = 'completed'
        self.assertFalse(self.test_generation.is_active())
        
        # Test failed status
        self.test_generation.status = 'failed'
        self.assertFalse(self.test_generation.is_active())
    
    def test_is_completed(self):
        """Test checking if generation is completed"""
        # Test pending status
        self.test_generation.status = 'pending'
        self.assertFalse(self.test_generation.is_completed())
        
        # Test running status
        self.test_generation.status = 'running'
        self.assertFalse(self.test_generation.is_completed())
        
        # Test completed status
        self.test_generation.status = 'completed'
        self.assertTrue(self.test_generation.is_completed())
        
        # Test failed status
        self.test_generation.status = 'failed'
        self.assertTrue(self.test_generation.is_completed())
        
        # Test cancelled status
        self.test_generation.status = 'cancelled'
        self.assertTrue(self.test_generation.is_completed())


class TestGenerationModeAndPromptState(unittest.TestCase):
    """Tests for the `mode`/`prompt_state` fields (added for the "Reuse generation" feature)."""

    def test_defaults(self):
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"key": "value"},
            user_id="user_1"
        )
        self.assertEqual(gen.mode, 'txt2img')
        self.assertIsNone(gen.prompt_state)

    def test_from_row_maps_mode_and_prompt_state(self):
        prompt_state = {'segments': [{'text': 'a cat'}], 'chips': ['a', 'b']}
        mock_row = {
            'id': 'test_gen_123',
            'preset_id': 'test_preset',
            'preset_version': '1.0',
            'form_data': json.dumps({"prompt": "test"}),
            'user_id': 'user_123',
            'status': 'running',
            'progress': 0.75,
            'mode': 'img2img',
            'prompt_state': json.dumps(prompt_state),
            'created_at': None,
            'completed_at': None,
            'updated_at': None
        }

        generation = Generation.from_row(mock_row)

        self.assertEqual(generation.mode, 'img2img')
        self.assertEqual(generation.prompt_state, prompt_state)

    def test_from_row_null_prompt_state(self):
        mock_row = {
            'id': 'test_gen_123',
            'preset_id': 'test_preset',
            'preset_version': '1.0',
            'form_data': json.dumps({"prompt": "test"}),
            'user_id': 'user_123',
            'status': 'pending',
            'progress': 0.0,
            'mode': 'txt2img',
            'prompt_state': None,
            'created_at': None,
            'completed_at': None,
            'updated_at': None
        }

        generation = Generation.from_row(mock_row)

        self.assertIsNone(generation.prompt_state)

    def test_to_dict_includes_mode_and_prompt_state(self):
        prompt_state = {'segments': []}
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"key": "value"},
            user_id="user_1",
            mode='img2img',
            prompt_state=prompt_state
        )
        result = gen.to_dict()
        self.assertEqual(result['mode'], 'img2img')
        self.assertEqual(result['prompt_state'], prompt_state)

    def test_serialize_prompt_state_round_trip(self):
        prompt_state = {'segments': [{'text': 'a cat', 'weight': 1.0}], 'nested': {'a': [1, 2, 3]}}
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={},
            user_id="user_1",
            prompt_state=prompt_state
        )
        serialized = gen.serialize_prompt_state()
        self.assertEqual(json.loads(serialized), prompt_state)

    def test_serialize_prompt_state_none(self):
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={},
            user_id="user_1"
        )
        self.assertIsNone(gen.serialize_prompt_state())


class TestGenerationFormNameAndSeed(unittest.TestCase):
    """Tests for `form_name` (migration 093) and the honest, no-new-capture
    `seed` exposed by `to_dict()` (the submitted `form_data['seed']` — the only durable
    record of it; see records.py's `to_dict()` docstring comment for why)."""

    def test_defaults(self):
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"key": "value"},
            user_id="user_1"
        )
        self.assertIsNone(gen.form_name)

    def test_from_row_maps_form_name(self):
        mock_row = {
            'id': 'test_gen_123',
            'preset_id': 'test_preset',
            'preset_version': '1.0',
            'form_data': json.dumps({"prompt": "test"}),
            'user_id': 'user_123',
            'status': 'running',
            'progress': 0.75,
            'mode': 'txt2img',
            'prompt_state': None,
            'form_name': 'advanced',
            'created_at': None,
            'completed_at': None,
            'updated_at': None
        }

        generation = Generation.from_row(mock_row)

        self.assertEqual(generation.form_name, 'advanced')

    def test_from_row_null_form_name_reads_as_unknown(self):
        """Rows written before migration 093 have no form_name — NULL, not a guessed default."""
        mock_row = {
            'id': 'test_gen_123',
            'preset_id': 'test_preset',
            'preset_version': '1.0',
            'form_data': json.dumps({"prompt": "test"}),
            'user_id': 'user_123',
            'status': 'pending',
            'progress': 0.0,
            'mode': 'txt2img',
            'prompt_state': None,
            'created_at': None,
            'completed_at': None,
            'updated_at': None
        }

        generation = Generation.from_row(mock_row)

        self.assertIsNone(generation.form_name)

    def test_to_dict_includes_form_name(self):
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"key": "value"},
            user_id="user_1",
            form_name='advanced'
        )
        result = gen.to_dict()
        self.assertEqual(result['form_name'], 'advanced')

    def test_to_dict_seed_reflects_form_data_seed(self):
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"seed": 123456},
            user_id="user_1"
        )
        result = gen.to_dict()
        self.assertEqual(result['seed'], 123456)

    def test_to_dict_seed_minus_one_means_was_randomized(self):
        """A -1 submission is surfaced as -1, not fabricated into a concrete value —
        the concrete roll is never persisted (transport-only pipe_artifact)."""
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"seed": -1},
            user_id="user_1"
        )
        result = gen.to_dict()
        self.assertEqual(result['seed'], -1)

    def test_to_dict_seed_none_when_form_has_no_seed_field(self):
        gen = Generation(
            id="test_id",
            preset_id="preset_1",
            form_data={"prompt": "test"},
            user_id="user_1"
        )
        result = gen.to_dict()
        self.assertIsNone(result['seed'])


class TestFileModel(unittest.TestCase):
    
    def test_file_creation(self):
        """Test File model creation"""
        file = File(
            file_path="/test/image.jpg",
            file_type="image",
            user_id="user_123"
        )
        
        self.assertEqual(file.file_path, "/test/image.jpg")
        self.assertEqual(file.file_type, "image")
        self.assertEqual(file.user_id, "user_123")
        self.assertIsNone(file.file_size)
        self.assertIsNone(file.pipe_name)
        self.assertFalse(file.is_final)
        self.assertIsNone(file.id)
        self.assertIsNone(file.created_at)
    
    def test_from_row(self):
        """Test creating File from database row"""
        mock_row = {
            'id': 'file_123',
            'file_path': '/uploads/image.png',
            'file_type': 'image',
            'user_id': 'user_456',
            'file_size': 2048,
            'pipe_name': 'upscaler',
            'is_final': 1,
            'created_at': '2024-01-01T12:00:00'
        }
        
        file = File.from_row(mock_row)
        
        self.assertEqual(file.id, 'file_123')
        self.assertEqual(file.file_path, '/uploads/image.png')
        self.assertEqual(file.file_type, 'image')
        self.assertEqual(file.user_id, 'user_456')
        self.assertEqual(file.file_size, 2048)
        self.assertEqual(file.pipe_name, 'upscaler')
        self.assertTrue(file.is_final)
        # Row predates migration 104 (no is_derived column) - reads as False.
        self.assertFalse(file.is_derived)
        self.assertEqual(file.created_at, datetime(2024, 1, 1, 12, 0, 0))

    def test_from_row_is_derived(self):
        mock_row = {
            'id': 'file_123',
            'file_path': '/uploads/image.png',
            'file_type': 'image',
            'user_id': 'user_456',
            'file_size': 2048,
            'pipe_name': 'gallery',
            'is_final': 1,
            'is_derived': 1,
            'created_at': '2024-01-01T12:00:00'
        }

        file = File.from_row(mock_row)

        self.assertTrue(file.is_derived)

    def test_to_dict(self):
        """Test converting File to dictionary"""
        file = File(
            id="file_123",
            file_path="/test/image.jpg",
            file_type="image",
            user_id="user_123",
            file_size=1024,
            pipe_name="generator",
            is_final=True,
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        )
        
        result = file.to_dict()
        
        expected = {
            'id': 'file_123',
            'file_path': '/test/image.jpg',
            'file_type': 'image',
            'user_id': 'user_123',
            'mime_type': None,
            'file_size': 1024,
            'pipe_name': 'generator',
            'is_final': True,
            'is_derived': False,
            'created_at': '2024-01-01T12:00:00',
            'thumbnail_small': None,
            'thumbnail_medium': None,
            'thumbnail_large': None,
            'width': None,
            'height': None,
            'duration_seconds': None,
            'fps': None
        }

        self.assertEqual(result, expected)


class TestGenerationFileModel(unittest.TestCase):
    
    def test_generation_file_creation(self):
        """Test GenerationFile model creation"""
        gen_file = GenerationFile(
            generation_id="gen_123",
            file_id="file_456"
        )
        
        self.assertEqual(gen_file.generation_id, "gen_123")
        self.assertEqual(gen_file.file_id, "file_456")
        self.assertIsNone(gen_file.id)
        self.assertIsNone(gen_file.created_at)
    
    def test_from_row(self):
        """Test creating GenerationFile from database row"""
        mock_row = {
            'id': 'genfile_123',
            'generation_id': 'gen_456',
            'file_id': 'file_789',
            'created_at': '2024-01-01T12:00:00'
        }
        
        gen_file = GenerationFile.from_row(mock_row)
        
        self.assertEqual(gen_file.id, 'genfile_123')
        self.assertEqual(gen_file.generation_id, 'gen_456')
        self.assertEqual(gen_file.file_id, 'file_789')
        self.assertEqual(gen_file.created_at, datetime(2024, 1, 1, 12, 0, 0))
    
    def test_to_dict(self):
        """Test converting GenerationFile to dictionary"""
        gen_file = GenerationFile(
            id="genfile_123",
            generation_id="gen_456",
            file_id="file_789",
            created_at=datetime(2024, 1, 1, 12, 0, 0)
        )
        
        result = gen_file.to_dict()
        
        expected = {
            'id': 'genfile_123',
            'generation_id': 'gen_456',
            'file_id': 'file_789',
            'created_at': '2024-01-01T12:00:00'
        }
        
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()