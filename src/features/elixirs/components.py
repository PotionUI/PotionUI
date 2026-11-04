import functools
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from src.features.elixirs.catalog import Catalog, installed_catalog
from src.features.elixirs.contracts import Contracts, current_contracts
from src.features.elixirs.coordinator import Coordinator
from src.features.elixirs.follow import SessionFollows
from src.features.elixirs.generation_port import GenerationPort
from src.features.elixirs.limits import Limits, stored_limits
from src.features.elixirs.media import Resolver
from src.features.elixirs.repository import ElixirRepository
from src.features.elixirs.run_repository import RunStore
from src.features.elixirs.runtime import ElixirRuntime
from src.features.elixirs.session_reader import SessionReader


@dataclass
class ElixirsComponents:
    repository: ElixirRepository
    coordinator: Coordinator
    sessions: SessionReader
    runtime: ElixirRuntime
    resolver: Resolver
    catalog: Callable[[Iterable[str]], Catalog]
    contracts: Callable[[], Contracts]
    limits: Callable[[], Limits]


def build_elixirs(
    *,
    users: Any,
    orchestrator: Any,
    broadcaster: Any,
    generations: Any,
    generation_files: Any,
    history: Any,
    file_service: Any,
    media_store: Any,
    prompt_writer: Any,
    presets: Any,
    settings: Any,
    setting_repository: Any,
    session_repository: Any,
    session_version_repository: Any,
    phrasebook_values: Any = None,
    prompt_batch: Optional[Callable[[Optional[str]], bool]] = None,
) -> ElixirsComponents:
    repository = ElixirRepository()
    limits = stored_limits(setting_repository)
    port = GenerationPort(
        users=users,
        orchestrator=orchestrator,
        broadcaster=broadcaster,
        generations=generations,
        generation_files=generation_files,
        history=history,
        file_service=file_service,
        media_store=media_store,
        prompt_writer=prompt_writer,
        storage_root=settings.get_file_storage_directory,
        phrasebook_values=phrasebook_values,
    )
    coordinator = Coordinator(store=RunStore(), port=port, limits_provider=limits)
    sessions = SessionReader(session_repository, session_version_repository)
    follows = SessionFollows(repository, sessions.get_session, users.get_by_id)
    return ElixirsComponents(
        repository=repository,
        coordinator=coordinator,
        sessions=sessions,
        runtime=ElixirRuntime(coordinator, follows),
        resolver=port.resolve,
        catalog=functools.partial(installed_catalog, presets, prompt_batch=prompt_batch),
        contracts=current_contracts,
        limits=limits,
    )
