"""The models directory: where each type of model file lives on disk.

The model *objects* that pipes pass around are `src.pipelines.models`; this is
the filesystem side - the directory layout and the on-disk index over it.
"""

from typing import Optional

from src.platform.observability.logger import logger
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Set, Any
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


class ModelManager:
    """
    Owns the models directory layout: where each model type lives on disk.

    Downloading is NOT its job. Models are downloaded on demand through the
    core download queue (src/features/downloads), which authenticates via the
    provider registry (see docs/backends.md for the same registry pattern
    applied to engines).
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def _get_model_subdir(self, model_type: str) -> str:
        """Get the appropriate subdirectory for a model type"""
        type_mapping = {
            'checkpoint': 'checkpoints',
            'lora': 'loras',
            'embedding': 'embeddings',
            'upscaler': 'upscalers',
            'vae': 'vae',
            'controlnet': 'controlnet',
            'adetailer': 'adetailer',
            'clip': 'clip',
        }
        return type_mapping.get(model_type.lower(), 'checkpoint')

    def get_model_dir(self, model_type: str) -> Path:
        """Get the directory for a specific model type"""
        subdir = self._get_model_subdir(model_type)
        return self.base_path / subdir

    def create_model_dirs(self):
        """Create all necessary model directories"""
        for subdir in [
            'stable-diffusion',
            'loras',
            'embeddings',
            'upscalers',
            'vae',
            'controlnet',
            'adetailer'
        ]:
            (self.base_path / subdir).mkdir(parents=True, exist_ok=True)

@dataclass
class ModelIndex:
    file_path: Path
    sha256: str
    file_size: int
    model_type: str
    last_modified: float
    file_name: str
    is_symlink: bool
    real_path: Path

class ModelIndexer:

    TYPE_MAPPING = {
        'stable-diffusion': 'checkpoint',
        'loras': 'lora',
        'embeddings': 'embedding',
        'upscalers': 'upscaler',
        'vae': 'vae',
        'controlnet': 'controlnet',
        'adetailer': 'adetailer',
        'checkpoints': 'checkpoint',
        'clip': 'clip',  # Added CLIP model type
        'vfi': 'vfi',
    }

    def __init__(self, model_manager: ModelManager, cache_file: str = "model_cache.json", follow_symlinks: bool = True):
        self.model_manager = model_manager
        self.cache_file = Path(model_manager.base_path) / cache_file
        self.model_extensions: Set[str] = {'.safetensors', '.pt', '.ckpt', '.bin'}
        self.index: Dict[str, ModelIndex] = {}
        self.name_to_hash: Dict[str, str] = {}
        self.follow_symlinks = follow_symlinks
        self._load_cache()

    def _load_cache(self) -> None:
        """Load existing cache from disk if it exists."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                    self.index = {
                        sha: ModelIndex(
                            Path(data['file_path']),
                            sha,
                            data['file_size'],
                            data['model_type'],
                            data.get('last_modified', 0),
                            data.get('file_name', Path(data['file_path']).name),
                            data.get('is_symlink', False),
                            Path(data.get('real_path', data['file_path']))
                        )
                        for sha, data in cache_data.items()
                    }
                    self.name_to_hash = {
                        model.file_name: sha
                        for sha, model in self.index.items()
                    }
            except Exception as e:
                logger.error(f"Error loading cache file: {e}")
                self.index = {}
                self.name_to_hash = {}

    def _save_cache(self) -> None:
        """Save current cache to disk."""
        cache_data = {
            sha: {
                'file_path': str(model.file_path),
                'file_size': model.file_size,
                'model_type': model.model_type,
                'last_modified': model.last_modified,
                'file_name': model.file_name,
                'is_symlink': model.is_symlink,
                'real_path': str(model.real_path)
            }
            for sha, model in self.index.items()
        }

        try:
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving cache file: {e}")

    def _find_model_files(self) -> List[Path]:
        """
        Recursively find all model files in the base directory, including in symlinked directories.
        """
        model_files: List[Path] = []

        def process_directory(directory: Path, visited: Set[Path] = None):
            if visited is None:
                visited = set()

            try:
                # Resolve the directory path to handle symlinks
                real_dir = directory.resolve()

                # Skip if we've already visited this directory (prevents infinite loops)
                if real_dir in visited:
                    return

                visited.add(real_dir)

                # Iterate through directory contents
                for item in directory.iterdir():
                    try:
                        if item.is_symlink() and self.follow_symlinks:
                            real_item = item.resolve()
                            if real_item.is_dir():
                                process_directory(real_item, visited)
                            elif real_item.suffix.lower() in self.model_extensions:
                                model_files.append(item)  # Store symlink path
                        elif item.is_dir():
                            process_directory(item, visited)
                        elif item.suffix.lower() in self.model_extensions:
                            model_files.append(item)
                    except Exception as e:
                        logger.error(f"Error processing {item}: {e}")

            except Exception as e:
                logger.error(f"Error accessing directory {directory}: {e}")

        process_directory(self.model_manager.base_path)
        return model_files

    def _should_update_file(self, file_path: Path) -> bool:
        """
        Determine if a file needs to be re-indexed based on name and modification time.
        For symlinks, checks the target file's modification time.
        """
        real_path = file_path.resolve()
        file_name = file_path.name

        try:
            file_mtime = real_path.stat().st_mtime

            if file_name in self.name_to_hash:
                existing_model = self.index.get(self.name_to_hash[file_name])
                if existing_model and existing_model.last_modified >= file_mtime:
                    return False
            return True
        except Exception as e:
            logger.error(f"Error checking file status {file_path}: {e}")
            return True

    def _calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _get_model_type_from_path(self, file_path: Path) -> str:
        """
        Determine model type based on file location.
        Handles both absolute and relative paths.
        """
        try:
            # Convert both paths to absolute and resolve any symlinks
            abs_base_path = self.model_manager.base_path.resolve()
            abs_file_path = file_path.resolve()

            # Try to get the relative path from base to file
            try:
                relative_path = abs_file_path.relative_to(abs_base_path)
            except ValueError:
                # If file is not in base path, try to extract model type from the path components
                path_parts = abs_file_path.parts
                for part in path_parts:
                    part_lower = part.lower()
                    if part_lower in self.TYPE_MAPPING:
                        return self.TYPE_MAPPING[part_lower]
                return 'unknown'

            # Get the first directory name after the base path
            first_dir = str(relative_path.parts[0]).lower()
            return self.TYPE_MAPPING.get(first_dir, 'unknown')

        except Exception as e:
            logger.error(f"Error determining model type for {file_path}: {e}")
            return 'unknown'

    def _index_file(self, file_path: Path) -> Optional[ModelIndex]:
        """Index a single file and return its ModelIndex if needed."""
        try:
            is_symlink = file_path.is_symlink()
            real_path = file_path.resolve()

            if not real_path.exists():
                logger.error(f"File does not exist or broken symlink: {file_path}")
                return None

            if not self._should_update_file(real_path):
                return None

            sha256 = self._calculate_sha256(real_path)
            model_type = self._get_model_type_from_path(file_path)  # Use symlink path for type
            file_stats = real_path.stat()

            return ModelIndex(
                file_path=file_path,
                sha256=sha256,
                file_size=file_stats.st_size,
                model_type=model_type,
                last_modified=file_stats.st_mtime,
                file_name=file_path.name,
                is_symlink=is_symlink,
                real_path=real_path
            )
        except Exception as e:
            logger.error(f"Error indexing file {file_path}: {e}")
            return None

    def index_models(self, max_workers: int = 4) -> None:
        """
        Index all model files in the model directories using multiple threads.
        Only processes files that have been modified since last indexing.
        """
        model_files = self._find_model_files()

        # Process files in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            new_indices = list(executor.map(self._index_file, model_files))

        # Update index with new results, filtering out None values
        for model_index in filter(None, new_indices):
            self.index[model_index.sha256] = model_index
            self.name_to_hash[model_index.file_name] = model_index.sha256

            logger.debug(f"Indexing model: {model_index.file_path}")

        self._save_cache()

    def get_model_by_sha256(self, sha256: str) -> ModelIndex:
        """Retrieve model information by SHA256 hash."""
        return self.index.get(sha256)

    def get_models_by_type(self, model_type: str) -> List[ModelIndex]:
        """Retrieve all models of a specific type."""
        return [
            model for model in self.index.values()
            if model.model_type.lower() == model_type.lower()
        ]

    def verify_model_integrity(self, file_path: Path) -> bool:
        """
        Verify if a model file matches its cached checksum.
        Returns True if the file is valid, False otherwise.
        """
        if not file_path.exists():
            return False

        current_sha256 = self._calculate_sha256(file_path)
        for model in self.index.values():
            if model.file_path == file_path:
                return model.sha256 == current_sha256
        return False
