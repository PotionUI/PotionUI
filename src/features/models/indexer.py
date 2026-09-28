import os
import hashlib
import logging
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import (
    LogicalLocation,
    ModelRoot,
    ModelRootResolver,
    TypeDir,
    root_path_key,
)
from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE, MODEL_TYPES, SUPPORTED_MODEL_EXTENSIONS

logger = logging.getLogger(__name__)


class ScanCancelled(Exception):
    pass


@dataclass(frozen=True)
class FoundFile:
    root_id: str
    model_type: str
    rel_path: str
    abs_path: str
    size: int
    mtime_ns: int
    is_directory: bool = False


class ModelScanner:
    SUPPORTED_EXTENSIONS = SUPPORTED_MODEL_EXTENSIONS

    MODEL_TYPE_MAPPING = DIRECTORY_TO_MODEL_TYPE

    DIRECTORY_MODEL_TYPES = {'llm'}

    def __init__(self, resolver: ModelRootResolver, locations_repository: Optional[ModelLocationsRepository] = None):
        self.resolver = resolver
        self.locations = locations_repository or ModelLocationsRepository()
        self.progress_callback = None
        self._write_lock = threading.Lock()

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def _report_progress(self, current: int, total: int, message: str):
        if self.progress_callback:
            self.progress_callback(current, total, message)

    def _root_by_id(self, root_id: str) -> Optional[ModelRoot]:
        return next((r for r in self.resolver.roots() if r.id == root_id), None)

    def _rel_key(self, root: ModelRoot, rel_path: str) -> str:
        key = unicodedata.normalize('NFC', rel_path)
        if root.case_insensitive:
            key = key.casefold()
        return key

    def _scanned_bindings(self) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        for model_type in MODEL_TYPES:
            for type_dir in self.resolver.type_dirs(model_type, online_only=True):
                pairs.append((type_dir.root_id, model_type))
        return pairs

    def scan_roots(self) -> List[FoundFile]:
        found: List[FoundFile] = []
        claimed: Set[str] = set()
        for model_type in MODEL_TYPES:
            for type_dir in self.resolver.type_dirs(model_type, online_only=True):
                if model_type in self.DIRECTORY_MODEL_TYPES:
                    entries = self._find_hf_dir_entries(type_dir)
                else:
                    entries = self._walk_type_dir_entries(type_dir)
                for rel_path, abs_path, size, mtime_ns, is_dir in entries:
                    try:
                        real = os.path.realpath(abs_path)
                    except OSError:
                        real = abs_path
                    real_key = root_path_key(real)
                    if real_key in claimed:
                        continue
                    claimed.add(real_key)
                    found.append(FoundFile(type_dir.root_id, model_type, rel_path, abs_path, size, mtime_ns, is_dir))
        return found

    def _walk_type_dir_entries(self, type_dir: TypeDir) -> List[Tuple[str, str, int, int, bool]]:
        base = type_dir.path
        out: List[Tuple[str, str, int, int, bool]] = []
        if not base.is_dir():
            return out

        visited_real_dirs: Set[str] = set()
        for root, dirnames, filenames in os.walk(base, followlinks=True):
            real_root = os.path.realpath(root)
            real_root_key = root_path_key(real_root)
            if real_root_key in visited_real_dirs:
                dirnames[:] = []
                continue
            visited_real_dirs.add(real_root_key)

            for filename in filenames:
                file_path = Path(root) / filename
                if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                    continue
                try:
                    stat = file_path.stat()
                    rel = file_path.relative_to(base).as_posix()
                    out.append((rel, str(file_path), stat.st_size, stat.st_mtime_ns, False))
                except OSError as e:
                    logger.warning(f"Could not stat {file_path}: {e}")
        return out

    def _find_hf_dir_entries(self, type_dir: TypeDir) -> List[Tuple[str, str, int, int, bool]]:
        base = type_dir.path
        out: List[Tuple[str, str, int, int, bool]] = []
        if not base.is_dir():
            return out

        for child in sorted(base.iterdir()):
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
                mtime_ns = child.stat().st_mtime_ns
                rel = child.relative_to(base).as_posix()
                out.append((rel, str(child), total_size, mtime_ns, True))
            except OSError as e:
                logger.warning(f"Could not read HF-layout directory {child}: {e}")
        return out

    def calculate_directory_fingerprint(self, dir_path: str) -> Optional[str]:
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

    def calculate_sha256(
        self, file_path: str, chunk_size: int = 4 * 1024 * 1024, cancel_check: Optional[Any] = None
    ) -> Optional[str]:
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    if cancel_check is not None and cancel_check():
                        raise ScanCancelled(file_path)
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except ScanCancelled:
            raise
        except Exception as e:
            logger.error(f"Error calculating SHA256 for {file_path}: {e}")
            return None

    def _legacy_hash_cache_keys(self, abs_path: str) -> List[str]:
        keys: List[str] = []
        try:
            real = os.path.realpath(abs_path)
        except OSError:
            real = None
        if real is not None and real != abs_path:
            keys.append(real)

        posix = abs_path.replace(os.sep, "/")
        try:
            home = str(self.resolver.home_dir()).replace(os.sep, "/").rstrip("/")
        except Exception:
            home = None
        if home is not None and posix.startswith(home + "/"):
            keys.append("models/" + posix[len(home) + 1:])
        return keys

    def _digest_for(
        self, abs_path: str, size: int, mtime_ns: int, cancel_check: Optional[Any] = None
    ) -> Optional[str]:
        from src.features.models.hash_cache_repository import model_hash_cache_repo

        cached = model_hash_cache_repo.get(abs_path)
        if cached is None:
            for legacy_key in self._legacy_hash_cache_keys(abs_path):
                cached = model_hash_cache_repo.get(legacy_key)
                if cached is not None:
                    break
        if cached and cached.size == size and cached.mtime_ns == mtime_ns:
            return cached.sha256

        digest = self.calculate_sha256(abs_path, cancel_check=cancel_check)
        if digest:
            try:
                model_hash_cache_repo.put(abs_path, size, mtime_ns, digest)
            except Exception as e:
                logger.debug(f"Could not seed hash cache for {abs_path}: {e}")
        return digest

    def index_file(self, found: FoundFile, cancel_check: Optional[Any] = None) -> Optional[Model]:
        try:
            filename = Path(found.abs_path).name
            if found.is_directory:
                sha256 = self.calculate_directory_fingerprint(found.abs_path)
            else:
                sha256 = self._digest_for(found.abs_path, found.size, found.mtime_ns, cancel_check=cancel_check)

            if not sha256:
                return None

            with self._write_lock:
                model, touched = self._commit_indexed_file(found, filename, sha256)
                self._recompute_availability_flags(touched)
                return model
        except ScanCancelled:
            raise
        except Exception as e:
            logger.error(f"Error indexing {found.abs_path}: {e}")
            return None

    def index_single_model(
        self, file_path: str, model_type: str, file_size: Optional[int] = None, cancel_check: Optional[Any] = None
    ) -> Optional[Model]:
        path = Path(file_path)
        loc = self.resolver.to_logical(path)
        if loc is None:
            logger.warning(f"'{file_path}' is not under any known model root; cannot index it")
            return None
        try:
            is_dir = path.is_dir()
            stat = path.stat()
        except OSError as e:
            logger.warning(f"Cannot stat '{file_path}': {e}")
            return None
        size = file_size if file_size is not None else stat.st_size
        found = FoundFile(loc.root_id, loc.model_type, loc.rel_path, str(path), size, stat.st_mtime_ns, is_dir)
        return self.index_file(found, cancel_check=cancel_check)

    def _commit_indexed_file(
        self, found: FoundFile, filename: str, sha256: str
    ) -> Tuple[Optional[Model], Set[str]]:
        root = self._root_by_id(found.root_id)
        if root is None:
            return None, set()
        rel_key = self._rel_key(root, found.rel_path)
        seen_at = now_iso()
        touched: Set[str] = set()

        existing_location = self.locations.get(found.root_id, found.model_type, rel_key)
        existing_by_identity = model_repo.get_by_identity(found.model_type, filename, include_providers=False)

        if (
            existing_location is not None and existing_by_identity is not None
            and existing_location['model_id'] == existing_by_identity.id
        ):
            existing_by_identity.sha256 = sha256
            existing_by_identity.file_size = found.size
            existing_by_identity.is_directory = found.is_directory
            existing_by_identity.indexed_at = datetime.now()
            existing_by_identity.is_available = True
            existing_by_identity.unavailable_at = None
            model_repo.update(existing_by_identity)
            self._write_location(found, rel_key, model_id=existing_by_identity.id, sha256=sha256, status='present', seen_at=seen_at)
            touched.add(existing_by_identity.id)
            return existing_by_identity, touched

        if existing_by_identity is not None and existing_by_identity.sha256 and existing_by_identity.sha256 != sha256:
            self._write_location(found, rel_key, model_id=existing_by_identity.id, sha256=sha256, status='conflict', seen_at=seen_at)
            touched.add(existing_by_identity.id)
            return existing_by_identity, touched

        existing_by_hash = model_repo.get_by_sha256(sha256, include_providers=False)
        if existing_by_hash is not None:
            if existing_by_hash.model_type == found.model_type and existing_by_hash.filename == filename:
                self._write_location(found, rel_key, model_id=existing_by_hash.id, sha256=sha256, status='present', seen_at=seen_at)
                self._revive(existing_by_hash)
                touched.add(existing_by_hash.id)
                return existing_by_hash, touched

            old_locations = self.locations.list_for_model(existing_by_hash.id)
            if not self._any_location_still_present(old_locations):
                existing_by_hash.filename = filename
                existing_by_hash.model_type = found.model_type
                existing_by_hash.file_size = found.size
                existing_by_hash.is_directory = found.is_directory
                existing_by_hash.indexed_at = datetime.now()
                existing_by_hash.is_available = True
                existing_by_hash.unavailable_at = None
                model_repo.update(existing_by_hash)
                self._write_location(found, rel_key, model_id=existing_by_hash.id, sha256=sha256, status='present', seen_at=seen_at)
                touched.add(existing_by_hash.id)
                return existing_by_hash, touched

            logger.warning(
                f"[MODEL_SCAN] '{found.abs_path}' duplicates the content of "
                f"'{existing_by_hash.filename}' ({existing_by_hash.model_type}); not indexed"
            )
            return None, touched

        if existing_by_identity is not None:
            if not existing_by_identity.sha256:
                existing_by_identity.sha256 = sha256
                existing_by_identity.file_size = found.size
                existing_by_identity.is_directory = found.is_directory
                existing_by_identity.indexed_at = datetime.now()
                existing_by_identity.is_available = True
                existing_by_identity.unavailable_at = None
                model_repo.update(existing_by_identity)
            self._write_location(found, rel_key, model_id=existing_by_identity.id, sha256=sha256, status='present', seen_at=seen_at)
            touched.add(existing_by_identity.id)
            return existing_by_identity, touched

        model_data = Model(
            filename=filename, file_size=found.size, sha256=sha256,
            model_type=found.model_type, is_directory=found.is_directory, indexed_at=datetime.now(),
        )
        try:
            model = model_repo.create(model_data)
        except Exception as create_error:
            if "UNIQUE constraint failed" not in str(create_error):
                raise
            existing = model_repo.get_by_sha256(sha256, include_providers=False)
            if existing is None:
                return None, touched
            model = existing

        self._write_location(found, rel_key, model_id=model.id, sha256=sha256, status='present', seen_at=seen_at)
        touched.add(model.id)
        return model, touched

    def _any_location_still_present(self, locations: List[Dict[str, Any]]) -> bool:
        for loc in locations:
            root = self._root_by_id(loc['root_id'])
            if root is None or not self.resolver.is_online_for(loc['root_id'], loc['model_type']):
                return True
            try:
                abs_path = self.resolver.physical(
                    LogicalLocation(loc['root_id'], loc['model_type'], loc['rel_path'])
                )
            except Exception:
                return True
            if abs_path.exists():
                return True
        return False

    def _write_location(
        self, found: FoundFile, rel_key: str, *, model_id: str, sha256: str, status: str, seen_at: str
    ) -> None:
        self.locations.upsert(
            model_id=model_id, root_id=found.root_id, model_type=found.model_type,
            rel_path=found.rel_path, rel_key=rel_key, size=found.size, mtime_ns=found.mtime_ns,
            sha256=sha256, status=status, seen_at=seen_at,
        )

    def _revive(self, model: Model) -> None:
        if not model.is_available:
            model.is_available = True
            model.unavailable_at = None
            model_repo.update(model)

    def _recompute_availability_flags(self, model_ids: Set[str]) -> None:
        if not model_ids:
            return
        online_root_ids = list(self.resolver.online_root_ids())
        winners = self.locations.winners_by_model(online_root_ids)

        for model_id in model_ids:
            model = model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
            if model is None:
                continue
            winner = winners.get(model_id)
            if winner is not None:
                if not model.is_available:
                    model.is_available = True
                    model.unavailable_at = None
                    model_repo.update(model)
            elif model.is_available:
                model_repo.mark_unavailable(model.id)

    @staticmethod
    def _row_is_unverified_but_adoptable(row: Dict[str, Any], f: FoundFile) -> bool:
        return (
            row is not None and row['status'] == 'present' and row['size'] == f.size
            and row['mtime_ns'] is None and bool(row['sha256'])
        )

    @classmethod
    def _row_matches(cls, row: Optional[Dict[str, Any]], f: FoundFile) -> bool:
        if row is None or row['status'] != 'present' or row['size'] != f.size:
            return False
        return row['mtime_ns'] == f.mtime_ns or cls._row_is_unverified_but_adoptable(row, f)

    def count_unindexed_by_binding(self) -> Dict[Tuple[str, str], int]:
        found = self.scan_roots()
        roots_by_id = {r.id: r for r in self.resolver.roots()}
        by_binding: Dict[Tuple[str, str], List[FoundFile]] = {}
        for f in found:
            by_binding.setdefault((f.root_id, f.model_type), []).append(f)

        counts: Dict[Tuple[str, str], int] = {}
        for key, files in by_binding.items():
            root_id, model_type = key
            root = roots_by_id.get(root_id)
            if root is None:
                continue
            existing = {row['rel_key']: row for row in self.locations.list_for_root_type(root_id, model_type)}
            n = 0
            for f in files:
                rel_key = self._rel_key(root, f.rel_path)
                row = existing.get(rel_key)
                if not self._row_matches(row, f):
                    n += 1
            if n:
                counts[key] = n
        return counts

    def count_unindexed(self) -> Dict[str, Any]:
        by_binding = self.count_unindexed_by_binding()
        by_type: Dict[str, int] = {}
        total = 0
        for (_, model_type), n in by_binding.items():
            by_type[model_type] = by_type.get(model_type, 0) + n
            total += n
        return {'total': total, 'by_type': by_type}

    def index_models(self, max_workers: int = 4, cancel_check: Optional[Any] = None) -> Dict[str, Any]:
        logger.info("Starting model indexing process")
        start_time = datetime.now()

        self._report_progress(0, 0, "Scanning models directory...")
        found = self.scan_roots()
        found_on_disk = len(found)

        found_by_root: Dict[str, int] = {}
        for f in found:
            found_by_root[f.root_id] = found_by_root.get(f.root_id, 0) + 1

        roots_by_id = {r.id: r for r in self.resolver.roots()}
        by_binding: Dict[Tuple[str, str], List[FoundFile]] = {}
        for f in found:
            by_binding.setdefault((f.root_id, f.model_type), []).append(f)

        seen_at = now_iso()
        new_or_changed: List[FoundFile] = []
        skipped_count = 0
        touched_by_diff: Set[str] = set()

        for (root_id, model_type), files in by_binding.items():
            root = roots_by_id.get(root_id)
            if root is None:
                continue
            existing = {row['rel_key']: row for row in self.locations.list_for_root_type(root_id, model_type)}
            present_rel_keys: List[str] = []
            for f in files:
                rel_key = self._rel_key(root, f.rel_path)
                present_rel_keys.append(rel_key)
                row = existing.get(rel_key)
                if not self._row_matches(row, f):
                    new_or_changed.append(f)
                elif row['mtime_ns'] != f.mtime_ns:
                    self.locations.adopt_mtime(root_id, model_type, rel_key, f.mtime_ns, seen_at)
                    touched_by_diff.add(row['model_id'])
                    skipped_count += 1
                else:
                    self.locations.touch_present(root_id, model_type, rel_key, seen_at)
                    touched_by_diff.add(row['model_id'])
                    skipped_count += 1
            for rel_key, row in existing.items():
                if rel_key not in present_rel_keys and row['status'] != 'missing':
                    touched_by_diff.add(row['model_id'])
            self.locations.mark_missing_for_root_type(root_id, model_type, present_rel_keys, seen_at)

        for (root_id, model_type) in self._scanned_bindings():
            if (root_id, model_type) in by_binding:
                continue
            existing = {row['rel_key']: row for row in self.locations.list_for_root_type(root_id, model_type)}
            for row in existing.values():
                if row['status'] != 'missing':
                    touched_by_diff.add(row['model_id'])
            self.locations.mark_missing_for_root_type(root_id, model_type, [], seen_at)

        total_new = len(new_or_changed)
        indexed_models: List[Dict[str, Any]] = []
        failed_files: List[Dict[str, str]] = []
        failed_by_root: Dict[str, int] = {}
        cancelled = False

        if total_new:
            self._report_progress(0, total_new, "Looking through your models folder...")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {
                    executor.submit(self.index_file, f, cancel_check): f
                    for f in new_or_changed
                }
                for i, future in enumerate(as_completed(future_to_file), 1):
                    f = future_to_file[future]
                    try:
                        model = future.result()
                        if model:
                            indexed_models.append(model.to_dict(include_providers=False))
                        else:
                            failed_files.append({'path': f.abs_path, 'error': 'Failed to hash or index this file'})
                            failed_by_root[f.root_id] = failed_by_root.get(f.root_id, 0) + 1
                    except ScanCancelled:
                        cancelled = True
                    except Exception as e:
                        logger.error(f"Exception processing {f.abs_path}: {e}")
                        failed_files.append({'path': f.abs_path, 'error': str(e)})
                        failed_by_root[f.root_id] = failed_by_root.get(f.root_id, 0) + 1

                    self._report_progress(i, total_new, f"Checked {Path(f.abs_path).name}")

                    if cancelled or (cancel_check is not None and cancel_check()):
                        cancelled = True
                        for pending_future in future_to_file:
                            pending_future.cancel()
                        logger.info(f"Model indexing cancelled after {i}/{total_new} files")
                        break

        self._recompute_availability_flags(touched_by_diff)

        duration = (datetime.now() - start_time).total_seconds()
        result = {
            'indexed': len(indexed_models),
            'skipped': skipped_count,
            'failed': len(failed_files),
            'total': found_on_disk,
            'duration': duration,
            'models': indexed_models,
            'new_files': total_new,
            'failed_files': failed_files,
            'found_on_disk': found_on_disk,
            'cancelled': cancelled,
            'found_by_root': found_by_root,
            'failed_by_root': failed_by_root,
        }
        logger.info(
            f"Model indexing completed: {result['indexed']} new models indexed, "
            f"{result['skipped']} skipped, {result['failed']} failed in {duration:.2f}s"
        )
        return result

    def get_indexing_status(self) -> Dict[str, Any]:
        type_counts = model_repo.count_by_type()
        type_sizes = model_repo.get_total_size_by_type()

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
