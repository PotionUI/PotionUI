#!/usr/bin/env python3
"""Reusable throwaway-instance lifecycle for HTTP-driven backend harnesses.

Everything here boots, waits on, authenticates against, and tears down an
ephemeral PotionUI backend subprocess - never the real instance (ports
8005/8006/3001, or its storage/db). Two kinds of caller use this module:

  - `tests/e2e/harness/onboarding_e2e.py`, which needs full control over the
    boot sequence (recipe mutation, a dummy download server, a bespoke
    run-driving loop) and imports the pieces it needs directly.
  - `tests/e2e/journeys/`, which just needs an authenticated instance
    and uses the higher-level `ThrowawayApp` context manager below.

Nothing in here talks to a specific feature's endpoints (setup runs, recipes,
...) - that stays in the callers.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover - requests ships in requirements.txt
    print(
        "The 'requests' package is required to run this script "
        "(pip install -r requirements.txt), or run it with the project venv's "
        "PYTHONPATH - see CLAUDE.md's Testing section.",
        file=sys.stderr,
    )
    raise

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_PORT = 8055
# Ports a real running PotionUI instance or dev frontend might be using. This
# module must never bind, proxy to, or otherwise touch these - see
# reject_forbidden_port below.
FORBIDDEN_PORTS = {8005, 8006, 3001}

HEALTH_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 2.0

# Default depot: the checked-in convention for a small fixture models
# directory that journeys COPY files into as they need them.
# An empty tree here must still be a bootable state - journeys skip with a
# reason rather than fail when a specific file they need isn't present.
DEFAULT_TEST_MODELS_DIR = REPO_ROOT / "models" / "tests"


class StageError(Exception):
    """Raised on failure. `stage` names exactly where things went wrong, so a
    failed run's error message points straight at the culprit instead of a
    bare traceback."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"[{stage}] {message}")


def log(message: str) -> None:
    print(f"[e2e] {message}", flush=True)


def stage_log(stage: str, message: str) -> None:
    print(f"[e2e] [{stage}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Small platform helpers
# ---------------------------------------------------------------------------


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def reject_forbidden_port(port: int) -> None:
    """Refuse to touch a real running instance's ports, no matter what a
    caller passes - this is a hard rule, not a default."""
    if port in FORBIDDEN_PORTS:
        raise StageError(
            "args",
            f"Refusing to use port {port}: reserved for a real PotionUI instance "
            f"(backend 8005/8006, frontend dev server 3001). Pick a different port.",
        )


def pick_free_port(start: int = DEFAULT_PORT) -> int:
    """Scan upward from `start` for a free, non-forbidden port. Used when a
    caller doesn't care which port it gets (e.g. several journey runs
    happening concurrently in the same tree)."""
    port = start
    while port in FORBIDDEN_PORTS or not port_is_free(port):
        port += 1
        if port > start + 1000:
            raise StageError("args", f"No free port found scanning from {start}")
    return port


def find_venv_site_packages(repo_root: Path) -> Optional[Path]:
    """The project's `venv/lib/pythonX.Y/site-packages`, or None if no venv
    has been created yet. Mirrors the convention CLAUDE.md documents for
    running pytest in this environment (`PYTHONPATH=./venv/lib/python3.12/
    site-packages:.`) rather than assuming `venv/bin/python` is itself a
    working interpreter - it is a symlink into a pyenv install that may not
    exist on this machine even when the venv's installed packages are fine."""
    lib_dir = repo_root / "venv" / "lib"
    if not lib_dir.is_dir():
        return None
    candidates = sorted(lib_dir.glob("python3.*/site-packages"))
    return candidates[0] if candidates else None


def resolve_backend_launch(repo_root: Path) -> Tuple[str, Path]:
    """Pick (interpreter, site_packages) for launching the backend
    subprocess: an interpreter on PATH matching the venv's own Python minor
    version (compiled extensions like torch are ABI-specific to it), falling
    back to whatever `python3`/`python` resolves to. Raises StageError with a
    clear repair hint if no venv exists at all."""
    site_packages = find_venv_site_packages(repo_root)
    if site_packages is None:
        raise StageError(
            "subprocess-boot",
            f"No virtualenv found at {repo_root / 'venv'}. Create one first: "
            "python3.12 -m venv venv && venv/bin/pip install -r requirements.txt "
            "-c constraints.txt (or run `./potionui start` once).",
        )
    py_minor_name = site_packages.parent.name  # e.g. "python3.12"
    for candidate in (py_minor_name, "python3", "python"):
        found = shutil.which(candidate)
        if found:
            return found, site_packages
    raise StageError(
        "subprocess-boot",
        f"No Python interpreter found on PATH matching {py_minor_name} (or python3/python) "
        "to launch the backend subprocess with.",
    )


# ---------------------------------------------------------------------------
# The models depot: a read-only mirror of a source models directory
# ---------------------------------------------------------------------------


def mirror_models_dir_readonly(source: Path, dest: Path) -> None:
    """Recreate `source`'s directory tree under `dest`, symlinking every file
    in place rather than copying it.

    This is how an ephemeral instance gets a "read-only" view of a real
    model depot without a container bind mount: the backend never opens a
    depot file for writing (models are read, never modified in place), and a
    fresh download landing in this run writes an ordinary new file into
    `dest` - the symlinked entries, and the real depot they point at, are
    never touched either way.
    """
    if not source.is_dir():
        raise StageError(
            "models-dir",
            f"Models source directory does not exist or is not a directory: {source}",
        )
    dest.mkdir(parents=True, exist_ok=True)
    file_count = 0
    # followlinks=True: a real depot's per-type directories (checkpoints/,
    # loras/, ...) are commonly symlinks onto another disk - os.walk's
    # default (False) would silently never descend into them, mirroring an
    # empty tree with no error.
    for root, _dirs, files in os.walk(source, followlinks=True):
        rel = Path(root).relative_to(source)
        target_root = dest / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            src_file = (Path(root) / name).resolve()
            dst_file = target_root / name
            if dst_file.exists() or dst_file.is_symlink():
                continue
            try:
                os.symlink(src_file, dst_file)
                file_count += 1
            except OSError as exc:
                raise StageError(
                    "models-dir",
                    f"Could not mirror {src_file} into the ephemeral models directory: {exc}",
                )
    stage_log("models-dir", f"Mirrored {file_count} file(s) from {source} into {dest} (read-only, symlinked)")


def mirror_recipes_dir_readonly(source: Path, dest: Path) -> None:
    """Symlink every `*.yml` recipe from `source` (a `content/recipes/marketplace`-
    shaped directory) into `dest / "marketplace"`, read-only - the same
    convention as `mirror_models_dir_readonly`, for callers that don't need to
    mutate the recipe catalog (unlike onboarding_e2e.py, which builds its own
    mutated copy instead of calling this). `dest` is the catalog *base* dir
    (what `POTIONUI_RECIPES_DIR` points at), so `RecipeCatalog` finds these
    under its own `marketplace/` scan."""
    dest_marketplace = dest / "marketplace"
    dest_marketplace.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        stage_log("recipes", f"No recipes directory at {source} - ephemeral instance gets an empty catalog")
        return
    count = 0
    for path in sorted(source.glob("*.yml")):
        dst_file = dest_marketplace / path.name
        if dst_file.exists() or dst_file.is_symlink():
            continue
        os.symlink(path.resolve(), dst_file)
        count += 1
    stage_log(
        "recipes", f"Mirrored {count} recipe file(s) from {source} into {dest_marketplace} (read-only, symlinked)"
    )


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class SetupClient:
    """Thin `requests.Session` wrapper scoped to the ephemeral instance's
    base URL, with a bearer token attached once login succeeds."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.token: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.session.get(f"{self.base_url}{path}", headers=self._headers(), **kwargs)

    def post(self, path: str, timeout: float = 30.0, **kwargs) -> requests.Response:
        return self.session.post(f"{self.base_url}{path}", headers=self._headers(), timeout=timeout, **kwargs)

    def put(self, path: str, timeout: float = 30.0, **kwargs) -> requests.Response:
        return self.session.put(f"{self.base_url}{path}", headers=self._headers(), timeout=timeout, **kwargs)

    def delete(self, path: str, timeout: float = 30.0, **kwargs) -> requests.Response:
        return self.session.delete(f"{self.base_url}{path}", headers=self._headers(), timeout=timeout, **kwargs)


def raise_for_status(stage: str, resp: requests.Response, context: str) -> Dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        detail = body.get("detail") or body.get("message") or resp.text[:500]
        raise StageError(stage, f"{context} failed ({resp.status_code}): {detail}")
    return body


# ---------------------------------------------------------------------------
# Auth stages: claim the instance, log in, mint a second (non-admin) user
# ---------------------------------------------------------------------------


def stage_wait_for_health(client: SetupClient, timeout: float = HEALTH_TIMEOUT_SECONDS) -> None:
    import time

    stage = "health"
    deadline = time.monotonic() + timeout
    last_error: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            resp = client.session.get(f"{client.base_url}/health", timeout=5.0)
            if resp.status_code == 200:
                stage_log(stage, f"Backend is up at {client.base_url}")
                return
            last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise StageError(stage, f"Backend never became healthy within {timeout:.0f}s (last error: {last_error})")


def claim_owner(client: SetupClient, *, username: str, email: str, password: str) -> str:
    """Register the first account on a fresh instance, which atomically
    becomes the owner (ADMIN). Sets `client.token`. Returns the account_type
    the server reported, so callers can assert it came back ADMIN."""
    stage = "claim-owner"
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    body = raise_for_status(stage, resp, "Owner registration")
    data = body.get("data") or {}
    token = data.get("access_token")
    if not token:
        raise StageError(stage, f"Registration succeeded but returned no access token: {body}")
    client.token = token
    account_type = (data.get("user") or {}).get("account_type")
    stage_log(stage, f"Claimed the instance as '{username}' (account_type={account_type})")
    return account_type


def login(client: SetupClient, *, username: str, password: str) -> None:
    """Log in with existing credentials, independent of any registration-time
    token. Sets `client.token`."""
    stage = "login"
    resp = client.post("/api/auth/login", data={"username": username, "password": password})
    body = raise_for_status(stage, resp, "Login")
    token = body.get("access_token")
    if not token:
        raise StageError(stage, f"Login succeeded but returned no access token: {body}")
    client.token = token
    stage_log(stage, f"Logged in as '{username}'")


@dataclass
class SecondUser:
    """A non-admin account minted on an already-claimed instance, with its
    own authenticated client - for exercising authz journeys (does this
    endpoint correctly reject a non-admin?) without touching the owner's
    session."""

    username: str
    email: str
    password: str
    client: SetupClient


def create_second_user(
    admin_client: SetupClient,
    base_url: str,
    *,
    username: Optional[str] = None,
    email: Optional[str] = None,
    password: Optional[str] = None,
    account_type: str = "USER",
) -> SecondUser:
    """Create an additional account via the admin-only `POST /api/users/`
    endpoint (registration itself is closed by default once an instance is
    claimed - see `Auth.register`), then log in as that account so the
    returned client is independently authenticated."""
    stage = "create-second-user"
    suffix = uuid.uuid4().hex[:8]
    username = username or f"e2e-user-{suffix}"
    email = email or f"{username}@example.com"
    password = password or secrets.token_urlsafe(18)

    resp = admin_client.post(
        "/api/users/",
        json={"username": username, "email": email, "password": password, "account_type": account_type},
    )
    raise_for_status(stage, resp, "Create second user")
    stage_log(stage, f"Created '{username}' (account_type={account_type})")

    user_client = SetupClient(base_url)
    login(user_client, username=username, password=password)
    return SecondUser(username=username, email=email, password=password, client=user_client)


# ---------------------------------------------------------------------------
# Backend subprocess lifecycle
# ---------------------------------------------------------------------------


@dataclass
class EphemeralInstance:
    instance_dir: Path
    storage_dir: Path
    db_path: Path
    models_dir: Path
    recipes_dir: Path
    port: int
    process: Optional[subprocess.Popen] = None
    log_path: Optional[Path] = None


def build_backend_env(
    instance: EphemeralInstance,
    site_packages: Path,
    repo_root: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """The env-var seams the app actually reads at startup - see
    src/platform/database/database.py (POTIONUI_DB_PATH),
    src/platform/filesystem/file_store.py (POTIONUI_STORAGE_PATH),
    and src/bootstrap/app.py's apply_startup_env_overrides /
    src/bootstrap/container.py's RecipeCatalog construction for
    POTIONUI_MODELS_DIR / POTIONUI_RECIPES_DIR (see .env.example)."""
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(site_packages), str(repo_root), existing_pythonpath) if p
    )
    env["POTIONUI_DB_PATH"] = str(instance.db_path)
    env["POTIONUI_STORAGE_PATH"] = str(instance.storage_dir)
    env["POTIONUI_MODELS_DIR"] = str(instance.models_dir)
    env["POTIONUI_RECIPES_DIR"] = str(instance.recipes_dir)
    # Never let a real .env auth secret leak into this
    # throwaway instance's tokens, and never let its ALLOWED_ORIGINS narrow
    # this instance's loopback-only traffic. python-dotenv's load_dotenv()
    # never overrides an already-set env var, so these two placeholders (if
    # unset) simply prevent .env from setting them instead.
    env.setdefault("POTIONUI_AUTH_SECRET_KEY", secrets.token_urlsafe(48))
    env["DEBUG"] = env.get("DEBUG", "false")
    if extra_env:
        env.update(extra_env)
    return env


def spawn_backend(
    instance: EphemeralInstance,
    repo_root: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    interpreter, site_packages = resolve_backend_launch(repo_root)
    env = build_backend_env(instance, site_packages, repo_root, extra_env=extra_env)
    log_path = instance.instance_dir / "backend.log"
    instance.log_path = log_path
    cmd = [
        interpreter,
        "-m",
        "uvicorn",
        "api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(instance.port),
        "--log-level",
        "info",
    ]
    stage_log("subprocess-boot", f"Launching backend: {' '.join(cmd)} (cwd={repo_root})")
    stage_log("subprocess-boot", f"Backend log: {log_path}")
    log_file = open(log_path, "wb", buffering=0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    log_file.close()  # child holds its own dup'd fd
    instance.process = proc
    return proc


def teardown_backend(instance: EphemeralInstance, *, keep: bool) -> None:
    if instance.process is not None and instance.process.poll() is None:
        stage_log("teardown", f"Stopping backend subprocess (pid={instance.process.pid})")
        try:
            os.killpg(os.getpgid(instance.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(Exception):
                instance.process.terminate()
        try:
            instance.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            stage_log("teardown", "Backend didn't exit after SIGTERM within 15s - sending SIGKILL")
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(instance.process.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                instance.process.wait(timeout=10)

    if keep:
        stage_log("teardown", f"keep=True: leaving {instance.instance_dir} on disk")
        return
    shutil.rmtree(instance.instance_dir, ignore_errors=True)
    stage_log("teardown", f"Removed {instance.instance_dir}")


# ---------------------------------------------------------------------------
# ThrowawayApp: the high-level context-manager API
# ---------------------------------------------------------------------------


class ThrowawayApp:
    """Boots a throwaway PotionUI backend for the duration of a `with` block:
    fresh temp SQLite DB, temp storage dir, a read-only mirror of a models
    depot (default: `models/tests`), a plain read-only mirror of
    `content/recipes/marketplace/`, claims the instance as owner, and tears
    everything down on exit.

    For journeys that don't need to mutate the recipe catalog or drive a
    setup run themselves - see `tests/e2e/harness/onboarding_e2e.py` for a
    harness that instead calls the lower-level pieces above directly.

    Usage:
        with ThrowawayApp() as app:
            resp = app.client.get("/api/chat/pre-actions")
            second = app.create_second_user()
            resp2 = second.client.get("/api/models")
    """

    def __init__(
        self,
        *,
        models_dir: Optional[Path] = None,
        recipes_dir: Optional[Path] = None,
        port: Optional[int] = None,
        keep: bool = False,
        username: str = "e2e-owner",
        email: Optional[str] = None,
        password: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
        repo_root: Path = REPO_ROOT,
    ):
        self.models_source = Path(models_dir).expanduser().resolve() if models_dir else DEFAULT_TEST_MODELS_DIR
        self.recipes_source = (
            Path(recipes_dir).expanduser().resolve()
            if recipes_dir
            else (repo_root / "content" / "recipes" / "marketplace")
        )
        self._requested_port = port
        self.keep = keep
        self.username = username
        self.email = email or f"{username}@example.com"
        self.password = password or secrets.token_urlsafe(18)
        self.extra_env = extra_env or {}
        self.repo_root = repo_root

        self.instance: Optional[EphemeralInstance] = None
        self.client: Optional[SetupClient] = None
        self.owner_account_type: Optional[str] = None

    def __enter__(self) -> "ThrowawayApp":
        if self._requested_port is None:
            port = pick_free_port(DEFAULT_PORT)
        else:
            reject_forbidden_port(self._requested_port)
            if not port_is_free(self._requested_port):
                raise StageError("args", f"Port {self._requested_port} is already in use.")
            port = self._requested_port

        run_id = uuid.uuid4().hex[:8]
        instance_dir = Path(tempfile.mkdtemp(prefix=f"potionui-e2e-{run_id}-"))
        instance = EphemeralInstance(
            instance_dir=instance_dir,
            storage_dir=instance_dir / "storage",
            db_path=instance_dir / "storage" / "db.sqlite",
            models_dir=instance_dir / "models",
            recipes_dir=instance_dir / "recipes",
            port=port,
        )
        instance.storage_dir.mkdir(parents=True, exist_ok=True)
        self.instance = instance

        try:
            if self.models_source.is_dir():
                mirror_models_dir_readonly(self.models_source, instance.models_dir)
            else:
                instance.models_dir.mkdir(parents=True, exist_ok=True)
                stage_log("models-dir", f"No source models dir at {self.models_source} - booting with an empty depot")

            mirror_recipes_dir_readonly(self.recipes_source, instance.recipes_dir)

            spawn_backend(instance, self.repo_root, extra_env=self.extra_env)

            self.client = SetupClient(self.base_url)
            stage_wait_for_health(self.client)

            self.owner_account_type = claim_owner(
                self.client, username=self.username, email=self.email, password=self.password
            )
            if self.owner_account_type != "ADMIN":
                raise StageError(
                    "claim-owner",
                    f"Expected the first registration on a fresh instance to become ADMIN, "
                    f"got {self.owner_account_type!r}.",
                )
        except Exception:
            teardown_backend(instance, keep=self.keep)
            raise

        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.instance is not None:
            teardown_backend(self.instance, keep=self.keep)
        return False

    @property
    def base_url(self) -> str:
        assert self.instance is not None
        return f"http://127.0.0.1:{self.instance.port}"

    def create_second_user(
        self,
        *,
        username: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        account_type: str = "USER",
    ) -> SecondUser:
        assert self.client is not None
        return create_second_user(
            self.client,
            self.base_url,
            username=username,
            email=email,
            password=password,
            account_type=account_type,
        )


# ---------------------------------------------------------------------------
# Journey result type, shared by tests/e2e/journeys/*
# ---------------------------------------------------------------------------


@dataclass
class JourneyResult:
    """What a `feature_journeys` module's `run(app) -> JourneyResult` returns.
    `name` is filled in by the runner, not the journey itself."""

    status: str  # "pass" | "fail" | "skip"
    evidence: list = field(default_factory=list)
    name: str = ""

    @classmethod
    def ok(cls, *evidence: str) -> "JourneyResult":
        return cls(status="pass", evidence=list(evidence))

    @classmethod
    def fail(cls, *evidence: str) -> "JourneyResult":
        return cls(status="fail", evidence=list(evidence))

    @classmethod
    def skip(cls, reason: str) -> "JourneyResult":
        return cls(status="skip", evidence=[reason])
