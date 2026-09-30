import os
import hashlib
import logging
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path
from dataclasses import dataclass, replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.features.models.type_repository import ModelTypeRepository, verdict_is_current
from src.features.models.type_resolution import Copy, resolve_type
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import (
    LogicalLocation,
    ModelRoot,
    ModelRootResolver,
    TypeDir,
    root_path_key,
)
from src.platform.filesystem.model_types import (
    DIRECTORY_TO_MODEL_TYPE,
    HEADER_EXTENSIONS,
    MODEL_TYPES,
    SUPPORTED_MODEL_EXTENSIONS,
    UNDEFINED_MODEL_TYPE,
)
from src.platform.runtime.model_headers import HeaderStatus, classify_header, model_classifier_registry, read_header
from src.platform.runtime.model_headers.reader import HeaderTap

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
    scan_headers: bool = False

    @property
    def classifiable(self) -> bool:
        return self.scan_headers and not self.is_directory and _has_header_extension(self.abs_path)


def _has_header_extension(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in HEADER_EXTENSIONS


@dataclass(frozen=True)
class DuplicateOf:
    model_id: str
    model_type: str
    root: str
    path: str

    def to_dict(self) -> Dict[str, str]:
        return {'model_id': self.model_id, 'model_type': self.model_type, 'root': self.root, 'path': self.path}


@dataclass(frozen=True)
class IndexOutcome:
    model: Optional[Model]
    touched: Set[str]
    duplicate_of: Optional[DuplicateOf] = None
    type_conflicts: Tuple[Dict[str, Any], ...] = ()
    header_error: Optional[str] = None


class HeaderUnreadable(Exception):
    pass


class ModelScanner:
    SUPPORTED_EXTENSIONS = SUPPORTED_MODEL_EXTENSIONS

    MODEL_TYPE_MAPPING = DIRECTORY_TO_MODEL_TYPE

    DIRECTORY_MODEL_TYPES = {'llm'}

    def __init__(
        self,
        resolver: ModelRootResolver,
        locations_repository: Optional[ModelLocationsRepository] = None,
        types_repository: Optional[ModelTypeRepository] = None,
    ):
        self.resolver = resolver
        self.locations = locations_repository or ModelLocationsRepository()
        self.types = types_repository or ModelTypeRepository()
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
                    found.append(FoundFile(
                        type_dir.root_id, model_type, rel_path, abs_path, size, mtime_ns, is_dir, type_dir.scan_headers
                    ))
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
        self,
        file_path: str,
        chunk_size: int = 4 * 1024 * 1024,
        cancel_check: Optional[Any] = None,
        tap: Optional[HeaderTap] = None,
    ) -> Optional[str]:
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    if cancel_check is not None and cancel_check():
                        raise ScanCancelled(file_path)
                    sha256_hash.update(chunk)
                    if tap is not None:
                        tap.feed(chunk)
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
        self,
        abs_path: str,
        size: int,
        mtime_ns: int,
        cancel_check: Optional[Any] = None,
        tap: Optional[HeaderTap] = None,
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

        if tap is not None:
            digest = self.calculate_sha256(abs_path, cancel_check=cancel_check, tap=tap)
        else:
            digest = self.calculate_sha256(abs_path, cancel_check=cancel_check)
        if digest:
            try:
                model_hash_cache_repo.put(abs_path, size, mtime_ns, digest)
            except Exception as e:
                logger.debug(f"Could not seed hash cache for {abs_path}: {e}")
        return digest

    def index_file(self, found: FoundFile, cancel_check: Optional[Any] = None) -> IndexOutcome:
        try:
            filename = Path(found.abs_path).name
            tap = None
            if found.is_directory:
                sha256 = self.calculate_directory_fingerprint(found.abs_path)
            else:
                tap = HeaderTap() if found.classifiable else None
                sha256 = self._digest_for(
                    found.abs_path, found.size, found.mtime_ns, cancel_check=cancel_check, tap=tap
                )

            if not sha256:
                return IndexOutcome(None, set())

            header_error = None
            if found.classifiable:
                try:
                    self._ensure_verdict(found.abs_path, sha256, tap.prefix if tap is not None else None)
                except HeaderUnreadable as e:
                    header_error = str(e)

            with self._write_lock:
                outcome = self._commit_indexed_file(found, filename, sha256)
                self._recompute_availability_flags(outcome.touched)
                outcome = self._retype(outcome)

            try:
                reclassified = outcome.model is not None and self._reconcile_disagreeing_copies(
                    found, sha256, outcome.model.id
                )
            except HeaderUnreadable as e:
                reclassified = False
                header_error = str(e)
            if reclassified:
                with self._write_lock:
                    outcome = self._retype(outcome, force_ids={outcome.model.id})
            if header_error is not None:
                outcome = replace(outcome, header_error=header_error)
            return outcome
        except ScanCancelled:
            raise
        except Exception as e:
            logger.error(f"Error indexing {found.abs_path}: {e}")
            return IndexOutcome(None, set())

    def _retype(self, outcome: IndexOutcome, force_ids: Optional[Set[str]] = None) -> IndexOutcome:
        changed, conflicts = self._recompute_types(set(outcome.touched) | (force_ids or set()))
        outcome = replace(outcome, type_conflicts=(*outcome.type_conflicts, *conflicts))
        if outcome.model is None or outcome.model.id not in changed:
            return outcome
        fresh = model_repo.get_by_id(outcome.model.id, include_providers=False, include_tags=False)
        return replace(outcome, model=fresh or outcome.model)

    def found_file_for(
        self, loc: LogicalLocation, path: Path, size: int, mtime_ns: int, is_directory: bool
    ) -> FoundFile:
        scan_headers = any(
            td.root_id == loc.root_id and td.scan_headers
            for td in self.resolver.type_dirs(loc.model_type, online_only=False)
        )
        return FoundFile(loc.root_id, loc.model_type, loc.rel_path, str(path), size, mtime_ns, is_directory, scan_headers)

    def index_single_model(
        self, file_path: str, model_type: str, file_size: Optional[int] = None, cancel_check: Optional[Any] = None
    ) -> IndexOutcome:
        path = Path(file_path)
        loc = self.resolver.to_logical(path)
        if loc is None:
            logger.warning(f"'{file_path}' is not under any known model root; cannot index it")
            return IndexOutcome(None, set())
        try:
            is_dir = path.is_dir()
            stat = path.stat()
        except OSError as e:
            logger.warning(f"Cannot stat '{file_path}': {e}")
            return IndexOutcome(None, set())
        size = file_size if file_size is not None else stat.st_size
        found = self.found_file_for(loc, path, size, stat.st_mtime_ns, is_dir)
        return self.index_file(found, cancel_check=cancel_check)

    def _ensure_verdict(self, abs_path: str, sha256: str, prefix: Optional[bytes]) -> bool:
        fingerprint = model_classifier_registry.fingerprint()
        existing = self.types.get_verdicts([sha256]).get(sha256)
        if existing is not None and verdict_is_current(existing, fingerprint):
            return False

        result = read_header(abs_path, prefix=prefix or None)
        if result.status is HeaderStatus.IO_ERROR:
            raise HeaderUnreadable(f"Could not read the file header: {result.error}")

        classified_at = now_iso()
        if result.status is HeaderStatus.INVALID:
            self.types.upsert_verdict(
                sha256=sha256, format='unknown', status='invalid', model_type=None, family=None, variant=None,
                components=[], transformer_extractable=False, classifier=None,
                registry_fingerprint=fingerprint, reason=result.error, classified_at=classified_at,
            )
            return True

        verdict = classify_header(result.view)
        components = [
            name for name, present in (
                ('denoiser', verdict.components.denoiser),
                ('vae', verdict.components.vae),
                ('text_encoder', verdict.components.text_encoder),
            ) if present
        ]
        self.types.upsert_verdict(
            sha256=sha256, format=result.view.format, status='decided' if verdict.decided else 'undecided',
            model_type=verdict.model_type, family=verdict.family, variant=verdict.variant,
            components=components, transformer_extractable=verdict.transformer_extractable,
            classifier=verdict.classifier, registry_fingerprint=verdict.fingerprint or fingerprint,
            reason=None, classified_at=classified_at,
        )
        return True

    def _reconcile_disagreeing_copies(self, found: FoundFile, sha256: str, model_id: str) -> bool:
        if found.classifiable or found.is_directory or not _has_header_extension(found.abs_path):
            return False
        copies = self.types.present_copies([model_id])
        unscanned_types = {
            c['model_type'] for c in copies if not (c['scan_headers'] and _has_header_extension(c['rel_path']))
        }
        if len(unscanned_types) < 2:
            return False
        return self._ensure_verdict(found.abs_path, sha256, None)

    def _recompute_types(self, model_ids: Set[str]) -> Tuple[Set[str], List[Dict[str, Any]]]:
        models = [m for m in self.types.models_by_ids(model_ids) if m['sha256'] and not m['is_directory']]
        if not models:
            return set(), []
        ids = [m['id'] for m in models]
        shas = [m['sha256'] for m in models]
        assertions = self.types.get_assertions(shas)
        verdicts = self.types.get_verdicts(shas)
        copies_by_model: Dict[str, List[Copy]] = {}
        for row in self.types.present_copies(ids):
            copies_by_model.setdefault(row['model_id'], []).append(
                Copy(row['model_type'], bool(row['scan_headers']) and _has_header_extension(row['rel_path']))
            )

        changes = []
        for model in models:
            resolution = resolve_type(
                assertions.get(model['sha256']), copies_by_model.get(model['id'], []), verdicts.get(model['sha256'])
            )
            if resolution is None:
                continue
            if (resolution.model_type, resolution.source) != (model['model_type'], model['type_source']):
                changes.append((model, resolution))
        if not changes:
            return set(), []

        occupied = self.types.identities_for_filenames(m['filename'] for m, _ in changes)
        updates: List[Tuple[str, str, str]] = []
        conflicts: List[Dict[str, Any]] = []
        for model, resolution in changes:
            if resolution.model_type != model['model_type']:
                key = (resolution.model_type, model['filename'])
                holder = occupied.get(key)
                if holder is not None and holder != model['id']:
                    conflicts.append(self._type_conflict(model, resolution.model_type))
                    continue
                occupied[key] = model['id']
            updates.append((model['id'], resolution.model_type, resolution.source))
        self.types.apply_types(updates)
        return {model_id for model_id, _, _ in updates}, conflicts

    @staticmethod
    def _type_conflict(model: Dict[str, Any], target_type: str) -> Dict[str, Any]:
        return {
            'id': None,
            'model_id': model['id'],
            'root_id': None,
            'model_type': target_type,
            'rel_path': model['filename'],
            'rel_key': model['filename'],
            'size': None,
            'mtime_ns': None,
            'sha256': model['sha256'],
            'status': 'type_conflict',
            'seen_at': now_iso(),
            'root_label': '',
            'message': f"type change blocked: '{model['filename']}' already exists as {target_type}",
        }

    def recompute_types(self, model_ids: Set[str]) -> List[Dict[str, Any]]:
        with self._write_lock:
            _, conflicts = self._recompute_types(set(model_ids))
        return conflicts

    def _initial_resolution(self, found: FoundFile, sha256: str):
        resolution = resolve_type(
            self.types.get_assertions([sha256]).get(sha256),
            [Copy(found.model_type, found.classifiable)],
            self.types.get_verdicts([sha256]).get(sha256),
        )
        return resolution

    def _commit_indexed_file(
        self, found: FoundFile, filename: str, sha256: str
    ) -> IndexOutcome:
        root = self._root_by_id(found.root_id)
        if root is None:
            return IndexOutcome(None, set())
        rel_key = self._rel_key(root, found.rel_path)
        seen_at = now_iso()
        touched: Set[str] = set()

        existing_location = self.locations.get(found.root_id, found.model_type, rel_key)
        location_model = None
        if existing_location is not None:
            location_model = model_repo.get_by_id(
                existing_location['model_id'], include_providers=False, include_tags=False
            )

        if location_model is not None and location_model.filename == filename:
            location_model.sha256 = sha256
            location_model.file_size = found.size
            location_model.is_directory = found.is_directory
            location_model.indexed_at = datetime.now()
            location_model.is_available = True
            location_model.unavailable_at = None
            model_repo.update(location_model)
            self._write_location(found, rel_key, model_id=location_model.id, sha256=sha256, status='present', seen_at=seen_at)
            touched.add(location_model.id)
            return IndexOutcome(location_model, touched)

        existing_by_hash = model_repo.get_by_sha256(sha256, include_providers=False)
        if existing_by_hash is not None:
            identity_type = existing_by_hash.model_type
            resolution = None
        else:
            resolution = self._initial_resolution(found, sha256)
            identity_type = resolution.model_type
        existing_by_identity = model_repo.get_by_identity(identity_type, filename, include_providers=False)

        if existing_by_identity is not None and existing_by_identity.sha256 and existing_by_identity.sha256 != sha256:
            self._write_location(found, rel_key, model_id=existing_by_identity.id, sha256=sha256, status='conflict', seen_at=seen_at)
            touched.add(existing_by_identity.id)
            return IndexOutcome(existing_by_identity, touched)

        if existing_by_hash is not None:
            if existing_by_hash.filename == filename:
                self._write_location(found, rel_key, model_id=existing_by_hash.id, sha256=sha256, status='present', seen_at=seen_at)
                self._revive(existing_by_hash)
                touched.add(existing_by_hash.id)
                return IndexOutcome(existing_by_hash, touched)

            old_locations = self.locations.list_for_model(existing_by_hash.id)
            surviving = self._first_present_location(old_locations)
            if surviving is None:
                existing_by_hash.filename = filename
                existing_by_hash.file_size = found.size
                existing_by_hash.is_directory = found.is_directory
                existing_by_hash.indexed_at = datetime.now()
                existing_by_hash.is_available = True
                existing_by_hash.unavailable_at = None
                model_repo.update(existing_by_hash)
                self._write_location(found, rel_key, model_id=existing_by_hash.id, sha256=sha256, status='present', seen_at=seen_at)
                touched.add(existing_by_hash.id)
                return IndexOutcome(existing_by_hash, touched)

            logger.warning(
                f"[MODEL_SCAN] '{found.abs_path}' duplicates the content of "
                f"'{existing_by_hash.filename}' ({existing_by_hash.model_type}); not indexed"
            )
            return IndexOutcome(None, touched, self._duplicate_of(existing_by_hash, surviving))

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
            return IndexOutcome(existing_by_identity, touched)

        model_data = Model(
            filename=filename, file_size=found.size, sha256=sha256,
            model_type=resolution.model_type, type_source=resolution.source,
            is_directory=found.is_directory, indexed_at=datetime.now(),
        )
        try:
            model = model_repo.create(model_data)
        except Exception as create_error:
            if "UNIQUE constraint failed" not in str(create_error):
                raise
            existing = model_repo.get_by_sha256(sha256, include_providers=False)
            if existing is None:
                return IndexOutcome(None, touched)
            model = existing

        self._write_location(found, rel_key, model_id=model.id, sha256=sha256, status='present', seen_at=seen_at)
        touched.add(model.id)
        return IndexOutcome(model, touched)

    def _classify_only(self, sha256: str, found: FoundFile, cancel_check: Optional[Any] = None) -> None:
        if cancel_check is not None and cancel_check():
            raise ScanCancelled(found.abs_path)
        self._ensure_verdict(found.abs_path, sha256, None)

    def _first_present_location(self, locations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for loc in locations:
            root = self._root_by_id(loc['root_id'])
            if root is None or not self.resolver.is_online_for(loc['root_id'], loc['model_type']):
                return loc
            try:
                abs_path = self.resolver.physical(
                    LogicalLocation(loc['root_id'], loc['model_type'], loc['rel_path'])
                )
            except Exception:
                return loc
            if abs_path.exists():
                return loc
        return None

    def _duplicate_of(self, model: Model, location: Dict[str, Any]) -> DuplicateOf:
        root = self._root_by_id(location['root_id'])
        label = root.label if root is not None else location['root_id']
        logical = LogicalLocation(location['root_id'], location['model_type'], location['rel_path'])
        path = None
        if self.resolver.is_online_for(location['root_id'], location['model_type']):
            try:
                path = self.resolver.physical(logical).as_posix()
            except Exception:
                path = None
        if path is None:
            path = f"{label}/{logical.logical_ref}"
        return DuplicateOf(model.id, model.model_type, label, path)

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

    def _classification_candidates(
        self, found: List[FoundFile], roots_by_id: Dict[str, ModelRoot]
    ) -> Dict[str, List[Tuple[FoundFile, Dict[str, Any]]]]:
        by_binding: Dict[Tuple[str, str], List[FoundFile]] = {}
        for f in found:
            if f.classifiable:
                by_binding.setdefault((f.root_id, f.model_type), []).append(f)

        by_sha: Dict[str, List[Tuple[FoundFile, Dict[str, Any]]]] = {}
        for (root_id, model_type), files in by_binding.items():
            root = roots_by_id.get(root_id)
            if root is None:
                continue
            existing = {row['rel_key']: row for row in self.locations.list_for_root_type(root_id, model_type)}
            for f in files:
                row = existing.get(self._rel_key(root, f.rel_path))
                if self._row_matches(row, f) and row['sha256']:
                    by_sha.setdefault(row['sha256'], []).append((f, row))

        settled = self.types.settled_shas(by_sha.keys(), model_classifier_registry.fingerprint())
        return {sha: entries for sha, entries in by_sha.items() if sha not in settled}

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

        for entries in self._classification_candidates(found, roots_by_id).values():
            for f, _ in entries:
                key = (f.root_id, f.model_type)
                counts[key] = counts.get(key, 0) + 1
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
        type_conflicts: List[Dict[str, Any]] = []
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

        classify_jobs = list(self._classification_candidates(found, roots_by_id).items())
        total_classify = len(classify_jobs)
        classified_model_ids: Set[str] = set()

        total_new = len(new_or_changed)
        indexed_models: List[Dict[str, Any]] = []
        failed_files: List[Dict[str, str]] = []
        skipped_duplicates: List[Dict[str, Any]] = []
        failed_by_root: Dict[str, int] = {}
        cancelled = False

        total_jobs = total_classify + total_new
        if total_jobs:
            self._report_progress(0, total_jobs, "Reading file headers..." if total_classify else "Looking through your models folder...")

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_job: Dict[Any, Tuple[str, Any]] = {}
                for sha, entries in classify_jobs:
                    future_to_job[executor.submit(self._classify_only, sha, entries[0][0], cancel_check)] = ('classify', entries)
                for f in new_or_changed:
                    future_to_job[executor.submit(self.index_file, f, cancel_check)] = ('hash', f)
                for i, future in enumerate(as_completed(future_to_job), 1):
                    kind, payload = future_to_job[future]
                    if kind == 'classify':
                        try:
                            future.result()
                            classified_model_ids.update(row['model_id'] for _, row in payload)
                        except ScanCancelled:
                            cancelled = True
                        except Exception as e:
                            path_failed = payload[0][0]
                            logger.error(f"Exception reading the header of {path_failed.abs_path}: {e}")
                            failed_files.append({'path': path_failed.abs_path, 'error': str(e)})
                            failed_by_root[path_failed.root_id] = failed_by_root.get(path_failed.root_id, 0) + 1
                        self._report_progress(i, total_jobs, "Reading file headers...")
                    else:
                        f = payload
                        try:
                            outcome = future.result()
                            type_conflicts.extend(outcome.type_conflicts)
                            if outcome.header_error is not None:
                                failed_files.append({'path': f.abs_path, 'error': outcome.header_error})
                                failed_by_root[f.root_id] = failed_by_root.get(f.root_id, 0) + 1
                            if outcome.model:
                                indexed_models.append(outcome.model.to_dict(include_providers=False))
                            elif outcome.duplicate_of is not None:
                                root = roots_by_id.get(f.root_id)
                                skipped_duplicates.append({
                                    'path': Path(f.abs_path).as_posix(),
                                    'root': root.label if root is not None else f.root_id,
                                    'same_as': outcome.duplicate_of.to_dict(),
                                })
                            else:
                                failed_files.append({'path': f.abs_path, 'error': 'Failed to hash or index this file'})
                                failed_by_root[f.root_id] = failed_by_root.get(f.root_id, 0) + 1
                        except ScanCancelled:
                            cancelled = True
                        except Exception as e:
                            logger.error(f"Exception processing {f.abs_path}: {e}")
                            failed_files.append({'path': f.abs_path, 'error': str(e)})
                            failed_by_root[f.root_id] = failed_by_root.get(f.root_id, 0) + 1
                        self._report_progress(i, total_jobs, f"Checked {Path(f.abs_path).name}")

                    if cancelled or (cancel_check is not None and cancel_check()):
                        cancelled = True
                        for pending_future in future_to_job:
                            pending_future.cancel()
                        logger.info(f"Model indexing cancelled after {i}/{total_jobs} files")
                        break

        self._recompute_availability_flags(touched_by_diff)
        with self._write_lock:
            _, retype_conflicts = self._recompute_types(touched_by_diff | classified_model_ids)
        type_conflicts.extend(retype_conflicts)
        type_conflicts = list({(c['model_id'], c['model_type']): c for c in type_conflicts}.values())

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
            'skipped_duplicates': skipped_duplicates,
            'found_on_disk': found_on_disk,
            'cancelled': cancelled,
            'found_by_root': found_by_root,
            'failed_by_root': failed_by_root,
            'classified': len(classified_model_ids),
            'type_conflicts': type_conflicts,
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
                for model_type in (*self.MODEL_TYPE_MAPPING.values(), UNDEFINED_MODEL_TYPE)
            },
            'models_missing_hashes': len(model_repo.get_models_missing_hashes()),
            'models_without_provider_info': len(model_repo.get_models_without_provider_info())
        }
