from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
import json
import base64
from copy import deepcopy

from src.platform.database.rows import row_get

@dataclass
class File:
    """Represents a generated file in the system"""
    file_path: str  # Relative path without base storage directory
    file_type: str  # 'IMAGE' | 'VIDEO' | 'AUDIO' | 'MESH'
    user_id: str
    mime_type: Optional[str] = None  # e.g., 'image/png', 'video/mp4'
    file_size: Optional[int] = None
    pipe_name: Optional[str] = None
    is_final: bool = False
    # Produced from another final file of this generation (e.g. an inline
    # enhance pass). Presentation hint only - never affects stored order.
    is_derived: bool = False
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    thumbnail_small: Optional[str] = None
    thumbnail_medium: Optional[str] = None
    thumbnail_large: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # duration_seconds: video and audio (added by migration 086 for video;
    # AudioGenerationOutputHandler populates it for audio - see audio_handler.py).
    # fps remains video-only. Both are None for images, and for files created
    # before migration 086 existed.
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None

    @classmethod
    def from_row(cls, row) -> 'File':
        """Create File instance from database row"""
        return cls(
            id=row['id'],
            file_path=row['file_path'],
            file_type=row['file_type'],
            user_id=row['user_id'],
            mime_type=row_get(row, 'mime_type'),
            file_size=row['file_size'],
            pipe_name=row['pipe_name'],
            is_final=bool(row['is_final']),
            is_derived=bool(row_get(row, 'is_derived') or 0),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            thumbnail_small=row_get(row, 'thumbnail_small'),
            thumbnail_medium=row_get(row, 'thumbnail_medium'),
            thumbnail_large=row_get(row, 'thumbnail_large'),
            width=row_get(row, 'width'),
            height=row_get(row, 'height'),
            duration_seconds=row_get(row, 'duration_seconds'),
            fps=row_get(row, 'fps')
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'file_path': self.file_path,
            'file_type': self.file_type,
            'user_id': self.user_id,
            'mime_type': self.mime_type,
            'file_size': self.file_size,
            'pipe_name': self.pipe_name,
            'is_final': self.is_final,
            'is_derived': self.is_derived,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'thumbnail_small': self.thumbnail_small,
            'thumbnail_medium': self.thumbnail_medium,
            'thumbnail_large': self.thumbnail_large,
            'width': self.width,
            'height': self.height,
            'duration_seconds': self.duration_seconds,
            'fps': self.fps
        }

@dataclass
class GenerationFile:
    """Junction table record linking generations to files"""
    generation_id: str
    file_id: str
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> 'GenerationFile':
        """Create GenerationFile instance from database row"""
        return cls(
            id=row['id'],
            generation_id=row['generation_id'],
            file_id=row['file_id'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'generation_id': self.generation_id,
            'file_id': self.file_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

@dataclass
class Generation:
    id: str
    preset_id: Optional[str]
    form_data: Dict[str, Any]
    user_id: str
    status: str = 'pending'
    preset_version: Optional[str] = None
    backend_id: Optional[str] = None
    # Client-minted id of the tab that queued this generation. Opaque routing
    # label, unique only within a user. See migration 077.
    tab_id: Optional[str] = None
    progress: float = 0.0
    mode: str = 'txt2img'
    prompt_state: Optional[Dict[str, Any]] = None
    # The resolved preset form variant this submission bound against (bind_form's
    # `BoundForm.form_name`; see `docs/presets.md` "Variants"). NULL for rows
    # created before migration 093 - reads as "unknown variant", never "default".
    form_name: Optional[str] = None
    # Library prompt this submission came from (Prompt Library "used in
    # generations" provenance). No FK: a deleted prompt must not break
    # generation history, so a dangling id simply resolves to nothing.
    source_prompt_id: Optional[str] = None
    rating: int = 0
    is_favorite: bool = False
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Wall-clock duration recorded at completion. Stored rather than derived because
    # updated_at is bumped by later writes (rating, favouriting). See migration 075.
    duration_ms: Optional[int] = None
    # The short failure summary set on the FAILED transition (status_tracker.py);
    # None for any generation that never failed. The full traceback/detail body
    # is never persisted - it only ever reaches the frontend live, over the
    # generation_error websocket message.
    error_message: Optional[str] = None
    files: List[File] = field(default_factory=list)
    tags: List['Tag'] = field(default_factory=list)  # Forward reference for Tag
    
    @classmethod
    def from_row(cls, row) -> 'Generation':
        """Create Generation instance from database row"""
        return cls(
            id=row['id'],
            preset_id=row['preset_id'],
            preset_version=row['preset_version'],
            form_data=json.loads(row['form_data']),
            user_id=row['user_id'],
            status=row['status'],
            backend_id=row_get(row, 'backend_id'),
            tab_id=row_get(row, 'tab_id'),
            progress=row['progress'] or 0.0,
            mode=row['mode'],
            prompt_state=json.loads(row['prompt_state']) if row['prompt_state'] else None,
            form_name=row_get(row, 'form_name'),
            source_prompt_id=row_get(row, 'source_prompt_id'),
            rating=row_get(row, 'rating', 0),
            is_favorite=bool(row_get(row, 'is_favorite', 0)),
            duration_ms=row_get(row, 'duration_ms'),
            error_message=row_get(row, 'error_message'),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            started_at=datetime.fromisoformat(row_get(row, 'started_at')) if row_get(row, 'started_at') else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )
    
    def to_dict(self, include_files: bool = False, include_tags: bool = False) -> dict:
        """Convert to dictionary for API responses"""
        result = {
            'id': self.id,
            'preset_id': self.preset_id,
            'preset_version': self.preset_version,
            'form_data': self.form_data,
            'user_id': self.user_id,
            'status': self.status,
            'backend_id': self.backend_id,
            'tab_id': self.tab_id,
            'progress': self.progress,
            'mode': self.mode,
            'prompt_state': self.prompt_state,
            'form_name': self.form_name,
            'source_prompt_id': self.source_prompt_id,
            # The submitted seed, exactly as bound. A -1 (randomize) submission
            # has its concrete roll resolved later in-memory and never written
            # back, so -1 here means "was randomized", not "unknown".
            'seed': self.form_data.get('seed') if isinstance(self.form_data, dict) else None,
            'rating': self.rating,
            'is_favorite': self.is_favorite,
            'duration_ms': self.duration_ms,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

        if include_files:
            result['files'] = [f.to_dict() for f in self.files]

        if include_tags:
            result['tags'] = [tag.model_dump() if hasattr(tag, 'model_dump') else tag.to_dict() for tag in self.tags]

        return result
    
    def serialize_form_data(self) -> str:
        """Serialize form data for database storage, handling image bytes"""
        # Make a deep copy to avoid modifying the original
        form_data_copy = deepcopy(self.form_data)
        
        # Convert any image bytes to base64 strings
        self._convert_bytes_to_base64(form_data_copy)
        
        return json.dumps(form_data_copy)

    def serialize_prompt_state(self) -> Optional[str]:
        """Serialize prompt state for database storage"""
        return json.dumps(self.prompt_state) if self.prompt_state is not None else None

    def _convert_bytes_to_base64(self, data: Any) -> None:
        """Recursively convert bytes to base64 strings in a data structure"""
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict) and 'data' in value and isinstance(value['data'], bytes):
                    # This looks like image data from the image field
                    try:
                        # Convert bytes to base64 string
                        value['data'] = base64.b64encode(value['data']).decode('utf-8')
                    except Exception:
                        # If conversion fails, remove the data to avoid serialization error
                        value['data'] = None
                else:
                    # Recursively process nested structures
                    self._convert_bytes_to_base64(value)
        elif isinstance(data, list):
            for item in data:
                self._convert_bytes_to_base64(item)
    
    def is_active(self) -> bool:
        """Check if generation is currently active"""
        return self.status in ['pending', 'running']
    
    def is_completed(self) -> bool:
        """Check if generation is completed (success or failure)"""
        return self.status in ['completed', 'failed', 'cancelled']

@dataclass
class GenerationParameter:
    """Represents a parameter associated with a generation"""
    generation_id: str
    parameter_name: str
    parameter_value: str  # JSON-encoded value
    parameter_index: int = 0
    id: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'GenerationParameter':
        """Create GenerationParameter instance from database row"""
        return cls(
            id=row['id'],
            generation_id=row['generation_id'],
            parameter_name=row['parameter_name'],
            parameter_value=row['parameter_value'],
            parameter_index=row['parameter_index'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        # Parse the JSON value for API response
        import json
        try:
            value = json.loads(self.parameter_value)
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON, use raw value
            value = self.parameter_value

        return {
            'id': self.id,
            'generation_id': self.generation_id,
            'parameter_name': self.parameter_name,
            'parameter_value': value,
            'parameter_index': self.parameter_index,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

@dataclass
class GenerationModel:
    """Junction table record linking generations to models used"""
    generation_id: str
    model_id: str
    id: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'GenerationModel':
        """Create GenerationModel instance from database row"""
        return cls(
            id=row['id'],
            generation_id=row['generation_id'],
            model_id=row['model_id'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'generation_id': self.generation_id,
            'model_id': self.model_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }