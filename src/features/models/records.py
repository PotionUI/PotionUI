from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
import json

@dataclass
class ModelInfo:
    """Generic model metadata from various providers"""
    id: Optional[str] = None  # ULID for this info record
    model_id: Optional[str] = None  # ID of the model this info belongs to
    provider: str = ''  # Provider name (e.g., 'civitai-provider', 'huggingface', etc.)
    provider_model_id: Optional[str] = None  # ID on the provider's platform
    provider_version_id: Optional[str] = None  # Version ID on the provider's platform
    name: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    nsfw: bool = False
    download_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> 'ModelInfo':
        """Create ModelInfo instance from database row"""
        return cls(
            id=row['id'],
            model_id=row['model_id'],
            provider=row['provider'],
            provider_model_id=row['provider_model_id'],
            provider_version_id=row['provider_version_id'],
            name=row['name'],
            description=row['description'],
            tags=json.loads(row['tags']) if row['tags'] else [],
            nsfw=bool(row['nsfw']),
            download_url=row['download_url'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )
    
    def to_dict(self) -> dict:
        """
        Convert to dictionary for API responses

        NOTE: Provider info is METADATA ONLY - provider information like name, tags, nsfw status
        Media files (images/videos) should come from model.files, NOT from provider data
        """
        return {
            'id': self.id,
            'provider': self.provider,
            'provider_model_id': self.provider_model_id,
            'provider_version_id': self.provider_version_id,
            'name': self.name,
            'description': self.description,  # Provider description - user notes in model.description
            'tags': self.tags,  # Provider tags
            'nsfw': self.nsfw,
            'download_url': self.download_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class Model:
    """Represents a model file in the models directory"""
    id: Optional[str] = None  # ULID
    filename: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    model_type: Optional[str] = None  # checkpoint, lora, embedding, upscaler, vae, controlnet, adetailer, text_encoder
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    indexed_at: Optional[datetime] = None
    description: Optional[str] = None  # Markdown description with tips, trigger words, etc.
    prompting_guidance: Optional[str] = None  # Admin-authored: how the chat LLM should prompt this model
    preview_media: Optional[Dict[str, Any]] = None  # Admin-set preview ({url, type, name?, relative_path?}); takes precedence over provider preview files
    model_metadata: Dict[str, Any] = field(default_factory=dict)  # Per-model-type extensible fields (e.g. a LoRA's default strength), keyed by ModelMetadataFieldRegistry field name
    providers: List[ModelInfo] = field(default_factory=list)  # Info from multiple providers
    files: List[Dict[str, Any]] = field(default_factory=list)  # Associated files with URLs
    tags: List = field(default_factory=list)  # List of Tag objects
    custom_name: Optional[str] = None  # Per-user display name override (model library)
    is_favorite: bool = False  # Per-user favorite flag (model library)
    # True for an HF-layout checkpoint directory - `file_path` points at the
    # directory, `file_size` is the summed shard size, and `sha256` is a cheap
    # fingerprint (config.json + sorted shard names/sizes), not a content hash.
    is_directory: bool = False
    # False when the indexer's last scan found no file at `file_path` (e.g. the
    # models location was switched away from this file). Tags/ratings/assignments
    # survive; a later scan that finds the file again sets this back to True.
    is_available: bool = True
    unavailable_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'Model':
        """Create Model instance from database row"""
        row_keys = row.keys()
        return cls(
            id=row['id'],
            filename=row['filename'],
            file_path=row['file_path'],
            file_size=row['file_size'],
            sha256=row['sha256'],
            model_type=row['model_type'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
            indexed_at=datetime.fromisoformat(row['indexed_at']) if row['indexed_at'] else None,
            # Handle backward compatibility - use user_notes as description if description doesn't exist
            description=row['description'] if 'description' in row_keys else (row['user_notes'] if 'user_notes' in row_keys else None),
            prompting_guidance=row['prompting_guidance'] if 'prompting_guidance' in row_keys else None,
            preview_media=json.loads(row['preview_media']) if ('preview_media' in row_keys and row['preview_media']) else None,
            model_metadata=json.loads(row['model_metadata']) if ('model_metadata' in row_keys and row['model_metadata']) else {},
            # Only present when the query joined user_model_meta (library_user_id set)
            custom_name=row['custom_name'] if 'custom_name' in row_keys else None,
            is_favorite=bool(row['is_favorite']) if 'is_favorite' in row_keys else False,
            is_directory=bool(row['is_directory']) if 'is_directory' in row_keys else False,
            is_available=bool(row['is_available']) if 'is_available' in row_keys else True,
            unavailable_at=(
                datetime.fromisoformat(row['unavailable_at'])
                if ('unavailable_at' in row_keys and row['unavailable_at']) else None
            ),
        )

    @property
    def display_name(self) -> str:
        """What to call this model in the UI.

        Prefers a name a human chose, then one a marketplace supplied, and finally the
        filename without its extension. That fallback carries the weight in practice: a
        freshly indexed library has no custom names and no provider metadata, so without
        it every model would be unlabelled.
        """
        if self.custom_name:
            return self.custom_name

        for info in self.providers or []:
            name = getattr(info, 'name', None)
            if name:
                return name

        if self.filename:
            stem, _, _ = self.filename.rpartition('.')
            return stem or self.filename

        return self.id or ''

    def to_dict(
        self,
        include_providers: bool = True,
        include_tags: bool = True,
        admin: bool = True,
    ) -> dict:
        """Convert to dictionary for API responses.

        `admin=False` omits the operational fields - where the bytes live, how many of
        them there are, their hash, and when we last looked. Someone generating images
        cares what a model does, not where it sits on disk.

        `filename` stays in the payload even for non-admins: saved sessions reference
        models by path or filename, and the picker resolves those legacy values against
        it. It is a matching key, not something to render. Show `name` instead.
        """
        result = {
            'id': self.id,
            'filename': self.filename,
            'name': self.display_name,
            'model_type': self.model_type,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'description': self.description,
            'custom_name': self.custom_name,
            'is_favorite': self.is_favorite,
            # Preview media is shown to generating users in pickers, so it stays in the
            # non-admin payload too (unlike the operational fields below).
            'preview_media': self.preview_media,
            # Same reasoning as triggers/preview_media: a LoRA's default strength is
            # consumed wherever the model is added to a generation, not just in admin.
            'model_metadata': self.model_metadata,
        }

        if admin:
            result.update({
                'file_path': self.file_path,
                'file_size': self.file_size,
                'sha256': self.sha256,
                'updated_at': self.updated_at.isoformat() if self.updated_at else None,
                'indexed_at': self.indexed_at.isoformat() if self.indexed_at else None,
                'prompting_guidance': self.prompting_guidance,
                'is_directory': self.is_directory,
                'is_available': self.is_available,
                'unavailable_at': self.unavailable_at.isoformat() if self.unavailable_at else None,
            })

        if include_providers:
            # Include providers array
            result['providers'] = [info.to_dict() for info in self.providers]

        # Include associated files
        result['files'] = self.files

        # Include tags if requested
        if include_tags and self.tags:
            result['tags'] = [tag.to_dict() if hasattr(tag, 'to_dict') else tag for tag in self.tags]

        return result
    
    def get_file_size_mb(self) -> Optional[float]:
        """Get file size in MB"""
        if self.file_size:
            return round(self.file_size / (1024 * 1024), 2)
        return None
    
    def get_model_type_display(self) -> str:
        """Get display-friendly model type"""
        return self.model_type.upper() if self.model_type else "UNKNOWN"
    
    def get_file_size_gb(self) -> Optional[float]:
        """Get file size in GB"""
        if self.file_size:
            return round(self.file_size / (1024 * 1024 * 1024), 2)
        return None
    
    def is_large_model(self) -> bool:
        """Check if this is a large model (> 1GB)"""
        if self.file_size:
            return self.file_size > 1024 * 1024 * 1024
        return False
    
    def get_model_type_display(self) -> str:
        """Get display name for model type"""
        type_map = {
            'checkpoint': 'Checkpoint',
            'lora': 'LoRA',
            'embedding': 'Embedding',
            'upscaler': 'Upscaler',
            'vae': 'VAE',
            'controlnet': 'ControlNet',
            'adetailer': 'ADetailer',
            'text_encoder': 'Text Encoder'
        }
        return type_map.get(self.model_type, self.model_type.title() if self.model_type else 'Unknown')

@dataclass
class UserModel:
    user_id: str
    model_id: str
    id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'UserModel':
        """Create UserModel instance from database row"""
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            model_id=row['model_id'],
            assigned_at=datetime.fromisoformat(row['assigned_at']) if row['assigned_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'model_id': self.model_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

@dataclass
class ModelFile:
    """Junction table record linking models to files"""
    model_id: str
    file_id: str
    file_type: str = 'image'  # 'image', 'thumbnail', 'preview'
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> 'ModelFile':
        """Create ModelFile instance from database row"""
        return cls(
            id=row['id'],
            model_id=row['model_id'],
            file_id=row['file_id'],
            file_type=row['file_type'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'model_id': self.model_id,
            'file_id': self.file_id,
            'file_type': self.file_type,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }