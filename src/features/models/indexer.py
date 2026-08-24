import os
import hashlib
import logging
from typing import Any, List, Dict, Optional, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE, SUPPORTED_MODEL_EXTENSIONS
from src.platform.settings.repository import SettingRepository

logger = logging.getLogger(__name__)

_warned_unmapped_type_dir: set = set()

class ModelScanner:
    """Scans the models directory and reconciles it with the model index.

    Distinct from `src.features.models.directory.ModelIndexer`, which maintains a
    JSON side-cache; this class hashes files and upserts rows in the database, and
    owns the single-file entry point (`index_single_model`) the download job and
    the automation `action.index_model` need.
    """

    SUPPORTED_EXTENSIONS = SUPPORTED_MODEL_EXTENSIONS

    # Model type mappings based on directory structure.
    MODEL_TYPE_MAPPING = DIRECTORY_TO_MODEL_TYPE

    # Model types whose files live one directory-per-model deep (HF layout:
    # `config.json` + sharded weights) instead of flat-file-per-model. A type
    # dir mapped to one of these is scanned for immediate subdirectories
    # instead of walked file-by-file - see `_find_hf_directories`. Without this
    # split, the generic `rglob` walk below would find each shard inside such a
    # directory and index it as its own model.
    DIRECTORY_MODEL_TYPES = {'llm'}

    def __init__(self, models_dir: Optional[str] = None):
        # Get model directory from settings or use default
        if models_dir is None:
            setting_repo = SettingRepository()
            model_dir_setting = setting_repo.get_setting_by_key('models_dir')
            models_dir = model_dir_setting.get_typed_value() if model_dir_setting else "models"

        self.models_dir = Path(models_dir)
        self.progress_callback = None

    def set_progress_callback(self, callback):
        """Set callback function for progress updates"""
        self.progress_callback = callback

    def _report_progress(self, current: int, total: int, message: str):
        """Report progress if callback is set"""
        if self.progress_callback:
            self.progress_callback(current, total, message)

    def scan_models_directory(self) -> List[Tuple[str, str, int]]:
        """
        Scan models directory and return list of (file_path, model_type, file_size) tuples
        """
        logger.info(f"Scanning models directory: {self.models_dir}")

        if not self.models_dir.exists():
            logger.warning(f"Models directory does not exist: {self.models_dir}")
            return []

        found_files = []

        for type_dir in self.models_dir.iterdir():
            if not type_dir.is_dir():
                continue

            model_type = self.MODEL_TYPE_MAPPING.get(type_dir.name, 'unknown')
            if model_type == 'unknown' and type_dir.name not in _warned_unmapped_type_dir:
                logger.warning(f"Directory '{type_dir.name}' not in MODEL_TYPE_MAPPING, using 'unknown' type")
                _warned_unmapped_type_dir.add(type_dir.name)
            logger.debug(f"Scanning {type_dir.name} directory for {model_type} models")

            if model_type in self.DIRECTORY_MODEL_TYPES:
                found_files.extend(self._find_hf_directories(type_dir, model_type))
                continue

            # Recursively scan subdirectories
            for file_path in type_dir.rglob("*"):
                if file_path.is_file() and file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    try:
                        file_size = file_path.stat().st_size
                        found_files.append((str(file_path), model_type, file_size))
                        logger.debug(f"Found model: {file_path} ({file_size} bytes)")
                    except OSError as e:
                        logger.warning(f"Could not get file info for {file_path}: {e}")

        logger.info(f"Found {len(found_files)} model files")
        return found_files

    def _find_hf_directories(self, type_dir: Path, model_type: str) -> List[Tuple[str, str, int]]:
        """Find HF-layout checkpoint directories directly under `type_dir`.

        A candidate is an immediate subdirectory holding a `config.json` plus at
        least one recognized shard file; its shards are not walked further or
        indexed individually. Size is the sum of its shard files.
        """
        found: List[Tuple[str, str, int]] = []
        for child in sorted(type_dir.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "config.json").is_file():
                continue
            try:
                shard_files = [
                    f for f in child.iterdir()
                    if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
                ]
                if not shard_files:
                    continue
                total_size = sum(f.stat().st_size for f in shard_files)
            except OSError as e:
                logger.warning(f"Could not read HF-layout directory {child}: {e}")
                continue
            found.append((str(child), model_type, total_size))
            logger.debug(f"Found HF-layout checkpoint: {child} ({total_size} bytes)")
        return found

    def calculate_directory_fingerprint(self, dir_path: str) -> Optional[str]:
        """Cheap stable fingerprint for an HF-layout checkpoint directory.

        Hashing every shard's full contents would make indexing a multi-GB
        checkpoint directory unusable, so this hashes `config.json`'s bytes plus
        the sorted (name, size) of each shard file instead - stable across
        re-indexing the same directory, and it changes whenever the shard set
        changes (a file added/removed/resized).
        """
        try:
            path = Path(dir_path)
            hasher = hashlib.sha256()
            config_path = path / "config.json"
            if config_path.is_file():
                hasher.update(config_path.read_bytes())
            shard_entries = sorted(
                (f.name, f.stat().st_size)
                for f in path.iterdir()
                if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS
            )
            for name, size in shard_entries:
                hasher.update(f"{name}:{size}".encode("utf-8"))
            return hasher.hexdigest()
        except OSError as e:
            logger.error(f"Error fingerprinting HF-layout directory {dir_path}: {e}")
            return None

    def calculate_sha256(self, file_path: str, chunk_size: int = 8192) -> Optional[str]:
        """
        Calculate SHA256 hash of a file
        """
        try:
            sha256_hash = hashlib.sha256()

            with open(file_path, "rb") as f:
                # Read in chunks to handle large files efficiently
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    sha256_hash.update(chunk)

            return sha256_hash.hexdigest()

        except Exception as e:
            logger.error(f"Error calculating SHA256 for {file_path}: {e}")
            return None

    @staticmethod
    def _file_identity(file_path: str) -> Optional[tuple]:
        """(size, mtime_ns) as of right now, or None if the file cannot be stat'd.

        Read BEFORE the file's bytes, never after: the cache key has to describe
        the state the digest was computed from. Stat'ing afterwards records the
        mtime of a file that may have been rewritten mid-hash, which stores a
        digest of the old bytes under the new file's key - a cache entry that
        looks fresh forever and is wrong forever.
        """
        try:
            stat = Path(file_path).stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError as e:
            logger.debug(f"Could not stat {file_path} for hash caching: {e}")
            return None

    @staticmethod
    def _seed_hash_cache(file_path: str, identity: Optional[tuple], sha256: str) -> None:
        """Best-effort: a cache-seeding failure must never fail the index pass itself.

        `identity` is the (size, mtime_ns) captured before hashing - see
        `_file_identity`. Nothing is cached without it, because a digest with no
        trustworthy key is worse than no cache entry at all.

        Broad `except Exception` deliberately: a database error (e.g. a test
        double with no `model_hash_cache` table) is "the cache didn't get seeded
        this time", not "indexing failed".
        """
        if identity is None:
            return
        try:
            from src.features.models.hash_cache_repository import model_hash_cache_repo
            size, mtime_ns = identity
            model_hash_cache_repo.put(file_path, size, mtime_ns, sha256)
        except Exception as e:
            logger.debug(f"Could not seed hash cache for {file_path}: {e}")

    def index_single_model(self, file_path: str, model_type: str, file_size: int) -> Optional[Model]:
        """
        Index a single model file with SHA256 deduplication.

        Handles the following scenarios:
        1. Model with same SHA256 exists at same path -> Skip (already indexed)
        2. Model with same SHA256 exists at different path -> Update path (model moved/renamed)
        3. Model exists at path with different SHA256 -> Update hash (file replaced)
        4. Model with the same (model_type, filename) exists -> Update it. A model is
           identified by its identity, not its path, so two files sharing a basename
           under one type are one model.
        5. New model -> Create new entry
        """
        try:
            filename = Path(file_path).name
            is_directory = Path(file_path).is_dir()

            # HF-layout checkpoint directories get a cheap fingerprint
            # instead of a content hash - see calculate_directory_fingerprint.
            if is_directory:
                logger.debug(f"Fingerprinting HF-layout directory {file_path}")
                identity = None
                sha256 = self.calculate_directory_fingerprint(file_path)
            else:
                logger.debug(f"Calculating SHA256 for {file_path}")
                identity = self._file_identity(file_path)
                sha256 = self.calculate_sha256(file_path)

            if not sha256:
                logger.error(f"Failed to hash/fingerprint {file_path}")
                return None

            # Seed the native scan's (path, size, mtime) hash cache with the digest
            # this pass just computed, so a subsequent `scan_native_models` reuses it
            # instead of re-reading the file - see hash_cache_repository.py. Directory
            # fingerprints are deliberately excluded: they are not content hashes
            # (101_add_model_is_directory.py) and must never be handed out as one.
            if not is_directory:
                self._seed_hash_cache(file_path, identity, sha256)

            # Check if a model with this SHA256 already exists
            existing_by_hash = model_repo.get_by_sha256(sha256, include_providers=False)

            # Check if a model exists at this file path (shouldn't happen with our new filtering)
            existing_by_path = model_repo.get_by_file_path(file_path, include_providers=False)

            # Scenario 1: Same SHA256, same path (shouldn't happen with new filtering)
            if existing_by_hash and existing_by_hash.file_path == file_path:
                # This shouldn't happen anymore since we filter existing paths
                logger.debug(f"Model already indexed (shouldn't happen): {filename}")
                existing_by_hash.indexed_at = datetime.now()
                existing_by_hash.is_available = True
                existing_by_hash.unavailable_at = None
                model_repo.update(existing_by_hash)
                return existing_by_hash

            # Scenario 2: Same SHA256, different path (model moved/renamed or duplicate)
            if existing_by_hash:
                logger.debug(f"Found duplicate SHA256 - updating existing model from {existing_by_hash.file_path} to {file_path}")

                # Update the existing model with new path/filename
                existing_by_hash.file_path = file_path
                existing_by_hash.filename = filename
                existing_by_hash.file_size = file_size
                existing_by_hash.model_type = model_type
                existing_by_hash.is_directory = is_directory
                existing_by_hash.indexed_at = datetime.now()
                existing_by_hash.is_available = True
                existing_by_hash.unavailable_at = None

                # If there's an old entry at the new path (shouldn't happen), remove it
                if existing_by_path and existing_by_path.id != existing_by_hash.id:
                    logger.warning(f"Removing duplicate entry for {file_path}")
                    model_repo.delete(existing_by_path.id)

                model_repo.update(existing_by_hash)
                return existing_by_hash

            # Scenario 3: Different SHA256 at same path (file replaced - shouldn't happen with new filtering)
            if existing_by_path:
                logger.debug(f"Model file changed (shouldn't happen), updating: {filename}")
                existing_by_path.sha256 = sha256
                existing_by_path.file_size = file_size
                existing_by_path.model_type = model_type
                existing_by_path.is_directory = is_directory
                existing_by_path.indexed_at = datetime.now()
                existing_by_path.is_available = True
                existing_by_path.unavailable_at = None
                model_repo.update(existing_by_path)
                return existing_by_path

            # Scenario 4: same identity, different file. A model is (model_type, filename),
            # so two files sharing a basename under one type are one model - e.g.
            # loras/styleA/x.safetensors and loras/styleB/x.safetensors. Creating a second
            # row would violate UNIQUE(model_type, filename) and abort the whole index run.
            existing_by_identity = model_repo.get_by_identity(
                model_type, filename, include_providers=False
            )
            if existing_by_identity:
                if existing_by_identity.sha256 and existing_by_identity.sha256 != sha256:
                    logger.warning(
                        f"Identity collision for {model_type}/{filename}: "
                        f"{existing_by_identity.file_path} and {file_path} share a name but "
                        f"differ in content. Keeping one model row and pointing it at the "
                        f"newer file."
                    )
                existing_by_identity.file_path = file_path
                existing_by_identity.file_size = file_size
                existing_by_identity.sha256 = sha256
                existing_by_identity.is_directory = is_directory
                existing_by_identity.indexed_at = datetime.now()
                existing_by_identity.is_available = True
                existing_by_identity.unavailable_at = None
                model_repo.update(existing_by_identity)
                return existing_by_identity

            # Scenario 5: Completely new model
            model_data = Model(
                filename=filename,
                file_path=file_path,
                file_size=file_size,
                sha256=sha256,
                model_type=model_type,
                is_directory=is_directory,
                indexed_at=datetime.now()
            )

            try:
                model = model_repo.create(model_data)
                logger.debug(f"Added new model: {filename}")
                return model
            except Exception as create_error:
                if "UNIQUE constraint failed" in str(create_error):
                    # This can happen if another thread indexed the same SHA256
                    logger.warning(f"Duplicate SHA256 detected during creation for {file_path}, likely concurrent indexing")
                    # Try to get the existing model
                    existing = model_repo.get_by_sha256(sha256, include_providers=False)
                    if existing:
                        # Update it to point to this file
                        existing.file_path = file_path
                        existing.filename = filename
                        existing.file_size = file_size
                        existing.model_type = model_type
                        existing.is_directory = is_directory
                        existing.indexed_at = datetime.now()
                        existing.is_available = True
                        existing.unavailable_at = None
                        model_repo.update(existing)
                        return existing
                    return None
                else:
                    raise create_error

        except Exception as e:
            logger.error(f"Error indexing model {file_path}: {e}")
            return None

    def _diff_against_index(
        self, all_model_files: List[Tuple[str, str, int]]
    ) -> Tuple[List[Tuple[str, str, int]], int]:
        """Split an already-scanned file list into (files not yet in the index,
        count already indexed). No hashing and no database writes - just a set
        diff against known file paths. Shared by `index_models` (which then hashes
        and upserts the new files) and `count_unindexed` (which stops right here).
        """
        # A model marked unavailable (see _cleanup_deleted_models) is deliberately
        # left OUT of this set even though its row still exists - so if a scan finds
        # a file at that path again (the models location switched back, say), it is
        # treated as "new" and run back through index_single_model, which revives
        # the row.
        existing_models = model_repo.get_all(include_providers=False, include_tags=False)
        existing_paths = {
            model.file_path for model in existing_models
            if getattr(model, 'is_available', True)
        }

        new_model_files = [
            (file_path, model_type, file_size)
            for file_path, model_type, file_size in all_model_files
            if file_path not in existing_paths
        ]

        skipped_count = len(all_model_files) - len(new_model_files)
        return new_model_files, skipped_count

    def count_unindexed(self) -> Dict[str, Any]:
        """Cheap disk-vs-index diff for admin UI badges: how many files on disk
        aren't indexed yet, broken down by type. No hashing, no writes - safe to
        call on every page load.
        """
        new_model_files, _ = self._diff_against_index(self.scan_models_directory())
        by_type: Dict[str, int] = {}
        for _, model_type, _ in new_model_files:
            by_type[model_type] = by_type.get(model_type, 0) + 1
        return {
            'total': len(new_model_files),
            'by_type': by_type,
        }

    def index_models(self, max_workers: int = 4) -> Dict[str, any]:
        """
        Index only new models that aren't already in the database
        """
        logger.info("Starting model indexing process")
        start_time = datetime.now()

        # Scan for model files
        self._report_progress(0, 0, "Scanning models directory...")
        all_model_files = self.scan_models_directory()

        if not all_model_files:
            logger.info("No model files found to index")
            return {
                'indexed': 0,
                'skipped': 0,
                'failed': 0,
                'total': 0,
                'duration': 0,
                'models': [],
                'new_files': 0
            }

        new_model_files, skipped_count = self._diff_against_index(all_model_files)

        if not new_model_files:
            logger.info(f"No new model files to index. Skipped {skipped_count} already indexed files.")
            # Still clean up deleted files
            self._cleanup_deleted_models()

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            return {
                'indexed': 0,
                'skipped': skipped_count,
                'failed': 0,
                'total': len(all_model_files),
                'duration': duration,
                'models': [],
                'new_files': 0
            }

        logger.info(f"Found {len(new_model_files)} new files to index (skipping {skipped_count} already indexed)")

        indexed_models = []
        failed_files = []

        # Index only new models in parallel
        logger.info(f"Indexing {len(new_model_files)} new models with {max_workers} workers")
        total_new = len(new_model_files)
        # Files-scanned count, known once we know which files are new - lets a
        # caller show "N of M" instead of the indeterminate "Scanning..." tick.
        self._report_progress(0, total_new, "Looking through your models folder...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit indexing tasks for new files only
            future_to_file = {
                executor.submit(self.index_single_model, file_path, model_type, file_size): (file_path, model_type, file_size)
                for file_path, model_type, file_size in new_model_files
            }

            # Process completed tasks
            for i, future in enumerate(as_completed(future_to_file), 1):
                file_path, model_type, file_size = future_to_file[future]
                try:
                    model = future.result()

                    # Log progress every 10 models or at the end
                    if i % 10 == 0 or i == total_new:
                        logger.debug(f"Indexing progress: {i}/{total_new} new models processed ({(i/total_new*100):.1f}%)")

                    if model:
                        indexed_models.append(model.to_dict(include_providers=False))
                        logger.debug(f"Successfully indexed: {Path(file_path).name}")
                    else:
                        failed_files.append(file_path)
                        logger.debug(f"Failed to index: {Path(file_path).name}")

                except Exception as e:
                    logger.error(f"Exception processing {file_path}: {e}")
                    failed_files.append(file_path)

                # One tick per completed file: hashing a large checkpoint is the
                # slow part, so this is the only granularity that reflects real
                # progress on a huge library. The caller throttles DB writes.
                self._report_progress(i, total_new, f"Checked {Path(file_path).name}")

        # Clean up deleted files
        self._cleanup_deleted_models()

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        result = {
            'indexed': len(indexed_models),
            'skipped': skipped_count,
            'failed': len(failed_files),
            'total': len(all_model_files),
            'duration': duration,
            'models': indexed_models,
            'new_files': len(new_model_files),
            'failed_files': failed_files
        }

        logger.info(f"Model indexing completed: {result['indexed']} new models indexed, {result['skipped']} skipped, {result['failed']} failed in {duration:.2f}s")

        return result

    def _cleanup_deleted_models(self):
        """Soft-mark models whose file is no longer found on disk.

        Marks rather than deletes: the models location can be switched away and
        back (see src.features.models.location), and a hard delete would throw
        away tags/ratings/user assignments for a file that is only temporarily
        unreachable. `index_models` revives a marked row the next time a scan
        finds its file again.
        """
        logger.info("Checking for models with missing files")

        all_models = model_repo.get_all(include_providers=False)
        marked_count = 0

        for model in all_models:
            if not getattr(model, 'is_available', True):
                continue  # already marked; avoid a redundant write + timestamp bump
            if not Path(model.file_path).exists():
                logger.debug(f"Marking model unavailable (file missing): {model.filename}")
                model_repo.mark_unavailable(model.id)
                marked_count += 1

        if marked_count > 0:
            logger.info(f"Marked {marked_count} models unavailable (file missing)")

    def get_indexing_status(self) -> Dict[str, any]:
        """Get current indexing status and statistics from database only"""
        # Get database statistics
        type_counts = model_repo.count_by_type()
        type_sizes = model_repo.get_total_size_by_type()

        # Calculate total size in different units
        total_size_bytes = sum(type_sizes.values())
        total_size_mb = total_size_bytes / (1024 * 1024) if total_size_bytes > 0 else 0
        total_size_gb = total_size_bytes / (1024 * 1024 * 1024) if total_size_bytes > 0 else 0

        return {
            'total_models_db': sum(type_counts.values()),
            'total_size_bytes': total_size_bytes,
            'total_size_mb': round(total_size_mb, 2),
            'total_size_gb': round(total_size_gb, 2),
            'by_type': {
                model_type: {
                    'count': type_counts.get(model_type, 0),
                    'size_bytes': type_sizes.get(model_type, 0),
                    'size_mb': round(type_sizes.get(model_type, 0) / (1024 * 1024), 2) if type_sizes.get(model_type, 0) > 0 else 0
                }
                for model_type in self.MODEL_TYPE_MAPPING.values()
            },
            'models_missing_hashes': len(model_repo.get_models_missing_hashes()),
            'models_without_provider_info': len(model_repo.get_models_without_provider_info())
        }

# Global scanner instance (lazy initialized to avoid database access before migrations)
_model_scanner: Optional[ModelScanner] = None

def get_model_scanner() -> ModelScanner:
    """Get the global model scanner instance (lazy initialization)"""
    global _model_scanner
    if _model_scanner is None:
        _model_scanner = ModelScanner()
    return _model_scanner

# Module-level handle that lazily builds the scanner on first access, so importing
# this module never touches the settings database before migrations have run.
class _ModelScannerProxy:
    """Proxy that lazily initializes the model scanner on first access"""
    def __getattr__(self, name):
        return getattr(get_model_scanner(), name)

model_scanner = _ModelScannerProxy()
