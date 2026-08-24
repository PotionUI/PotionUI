"""
Test fixtures for generation-related data.

Provides fixtures for creating sample generations, form data,
and generation-related test data.
"""

import pytest
import json
from datetime import datetime
from typing import Dict, Any

from src.features.generation.records import Generation, File, GenerationFile
from src.platform.util.ids import generate_ulid


@pytest.fixture
def fake_form_data() -> Dict[str, Any]:
    """
    Provide sample form data for generation testing.

    Returns a dictionary representing typical form data that would
    be submitted for an image generation request.

    Returns:
        dict: Sample form data with common generation parameters
    """
    return {
        "prompt": "A beautiful sunset over mountains",
        "negative_prompt": "blurry, low quality",
        "width": 1024,
        "height": 1024,
        "steps": 30,
        "cfg_scale": 7.5,
        "seed": -1,
        "sampler": "euler_a",
        "batch_size": 1,
        "batch_count": 1
    }


@pytest.fixture
def sample_generation(test_db, fake_form_data) -> Generation:
    """
    Create a sample generation record in the test database.

    Provides a basic generation instance with common fields populated.
    The generation is in 'pending' status by default.

    Args:
        test_db: Test database fixture
        fake_form_data: Sample form data fixture

    Returns:
        Generation: Sample generation instance
    """
    generation_id = generate_ulid()
    user_id = generate_ulid()

    # Insert a test user first
    with test_db.get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO users (id, username, email, password_hash)
            VALUES (?, ?, ?, ?)
        """, (user_id, 'testuser', 'test@example.com', 'hashed_password'))

    generation = Generation(
        id=generation_id,
        preset_id='workbench/sdxl/realistic',
        preset_version='1.0.0',
        form_data=fake_form_data,
        user_id=user_id,
        status='pending',
        progress=0.0,
        created_at=datetime.now(),
        started_at=None,
        completed_at=None,
        updated_at=datetime.now()
    )

    # Insert generation into database
    with test_db.get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO generations (
                id, preset_id, preset_version, form_data, user_id, status, progress,
                mode, prompt_state, backend_id, tab_id, form_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            generation.id,
            generation.preset_id,
            generation.preset_version,
            generation.serialize_form_data(),
            generation.user_id,
            generation.status,
            generation.progress,
            generation.mode,
            generation.serialize_prompt_state(),
            generation.backend_id,
            generation.tab_id,
            generation.form_name,
            generation.created_at.isoformat() if generation.created_at else None
        ))

    return generation


@pytest.fixture
def sample_running_generation(sample_generation, test_db) -> Generation:
    """
    Create a sample generation in 'running' status.

    Args:
        sample_generation: Base generation fixture
        test_db: Test database fixture

    Returns:
        Generation: Running generation instance
    """
    with test_db.get_cursor() as cursor:
        cursor.execute("""
            UPDATE generations
            SET status = 'running',
                progress = 0.35,
                started_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), sample_generation.id))

    sample_generation.status = 'running'
    sample_generation.progress = 0.35
    sample_generation.started_at = datetime.now()

    return sample_generation


@pytest.fixture
def sample_completed_generation(sample_generation, test_db) -> Generation:
    """
    Create a sample generation in 'completed' status.

    Args:
        sample_generation: Base generation fixture
        test_db: Test database fixture

    Returns:
        Generation: Completed generation instance
    """
    with test_db.get_cursor() as cursor:
        cursor.execute("""
            UPDATE generations
            SET status = 'completed',
                progress = 1.0,
                started_at = ?,
                completed_at = ?
            WHERE id = ?
        """, (
            datetime.now().isoformat(),
            datetime.now().isoformat(),
            sample_generation.id
        ))

    sample_generation.status = 'completed'
    sample_generation.progress = 1.0
    sample_generation.started_at = datetime.now()
    sample_generation.completed_at = datetime.now()

    return sample_generation


@pytest.fixture
def sample_generation_with_files(sample_generation, test_db) -> Generation:
    """
    Create a sample generation with associated file records.

    Creates a generation with 2 sample image files attached.

    Args:
        sample_generation: Base generation fixture
        test_db: Test database fixture

    Returns:
        Generation: Generation instance with files
    """
    # Create sample file records
    files = []
    for i in range(2):
        file_id = generate_ulid()
        file_path = f"generations/2025-10-07/{sample_generation.id}/{i}.png"

        # Insert file record
        with test_db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO files (
                    id, file_path, file_type, user_id, mime_type,
                    file_size, pipe_name, is_final, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_id,
                file_path,
                'IMAGE',
                sample_generation.user_id,
                'image/png',
                1024000,  # 1MB
                'generator',
                True,
                datetime.now().isoformat()
            ))

            # Link file to generation
            cursor.execute("""
                INSERT INTO generation_files (id, generation_id, file_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                generate_ulid(),
                sample_generation.id,
                file_id,
                datetime.now().isoformat()
            ))

        file = File(
            id=file_id,
            file_path=file_path,
            file_type='IMAGE',
            user_id=sample_generation.user_id,
            mime_type='image/png',
            file_size=1024000,
            pipe_name='generator',
            is_final=True,
            created_at=datetime.now()
        )
        files.append(file)

    sample_generation.files = files
    return sample_generation


@pytest.fixture
def fake_form_data_with_image(fake_image_bytes) -> Dict[str, Any]:
    """
    Provide sample form data with an image attachment.

    Args:
        fake_image_bytes: Fake image bytes fixture

    Returns:
        dict: Form data with image data included
    """
    return {
        "prompt": "Transform this image",
        "negative_prompt": "blurry",
        "init_image": {
            "data": fake_image_bytes,
            "filename": "input.png",
            "content_type": "image/png"
        },
        "denoise_strength": 0.7,
        "steps": 30,
        "cfg_scale": 7.5,
        "seed": -1
    }
