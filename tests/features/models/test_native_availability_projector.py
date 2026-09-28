import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional
from unittest.mock import Mock

from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_LOCAL_DRIVER, NATIVE_REMOTE_DRIVER
from src.features.backends.model_listing import CONFIDENCE_REPORTED, CONFIDENCE_VERIFIED
from src.features.models.availability_records import ModelAvailability
from src.features.models.native_availability_reconciler import NativeAvailabilityProjector


@dataclass
class FakeConfig:
    driver: str


class FakeBackend:
    def __init__(self, backend_id, driver):
        self.backend_id = backend_id
        self.config = FakeConfig(driver=driver)


class FakeBackendRegistry:
    def __init__(self, backends):
        self._backends = backends

    def get_backends_for_engine(self, engine):
        assert engine == NATIVE_ENGINE
        return list(self._backends)


@dataclass
class FakeRoot:
    id: str
    raw_path: str
    state: str = "online"


class FakeResolver:
    def __init__(self, roots):
        self._roots = roots

    def roots(self):
        return list(self._roots)

    def online_root_ids(self):
        return {r.id for r in self._roots if r.state == "online"}


class FakeLocations:
    def __init__(self, winners: Dict[str, dict]):
        self._winners = winners
        self.calls: List[List[str]] = []

    def winners_by_model(self, root_ids):
        self.calls.append(list(root_ids))
        online = set(root_ids)
        return {mid: loc for mid, loc in self._winners.items() if loc["root_id"] in online}


class FakeAvailabilityRepo:
    def __init__(self):
        self.rows: Dict[tuple, ModelAvailability] = {}

    def get(self, model_id, backend_id):
        return self.rows.get((model_id, backend_id))

    def upsert(self, availability: ModelAvailability) -> ModelAvailability:
        self.rows[(availability.model_id, availability.backend_id)] = availability
        return availability

    def delete_for_backend(self, backend_id, keep_model_ids: Optional[set] = None) -> int:
        keep = keep_model_ids or set()
        to_delete = [key for key in self.rows if key[1] == backend_id and key[0] not in keep]
        for key in to_delete:
            del self.rows[key]
        return len(to_delete)


def _winner(root_id, *, model_type="lora", rel_path="x.safetensors", subdir="loras", sha256=None, size=100):
    return {
        "root_id": root_id, "model_type": model_type, "rel_path": rel_path, "subdir": subdir,
        "sha256": sha256, "size": size, "position": 0, "status": "present",
    }


def test_projects_only_local_native_backends():
    resolver = FakeResolver([FakeRoot(id="home", raw_path="models")])
    locations = FakeLocations({"m1": _winner("home")})
    availability = FakeAvailabilityRepo()
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    summary = asyncio.run(projector.reconcile(FakeBackendRegistry([
        FakeBackend("local-1", NATIVE_LOCAL_DRIVER), FakeBackend("remote-1", NATIVE_REMOTE_DRIVER),
    ])))

    assert summary.backend_ids == ["local-1"]
    assert ("m1", "local-1") in availability.rows
    assert ("m1", "remote-1") not in availability.rows


def test_ref_uses_the_logical_type_dir_plus_rel_path_format():
    resolver = FakeResolver([FakeRoot(id="home", raw_path="models")])
    locations = FakeLocations({"m1": _winner("home", rel_path="sdxl/x.safetensors", subdir="loras")})
    availability = FakeAvailabilityRepo()
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    asyncio.run(projector.reconcile(FakeBackendRegistry([FakeBackend("local-1", NATIVE_LOCAL_DRIVER)])))

    row = availability.rows[("m1", "local-1")]
    assert row.ref == "loras/sdxl/x.safetensors"


def test_confidence_is_verified_when_the_winner_has_a_digest():
    resolver = FakeResolver([FakeRoot(id="home", raw_path="models")])
    locations = FakeLocations({"m1": _winner("home", sha256="a" * 64)})
    availability = FakeAvailabilityRepo()
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    asyncio.run(projector.reconcile(FakeBackendRegistry([FakeBackend("local-1", NATIVE_LOCAL_DRIVER)])))

    assert availability.rows[("m1", "local-1")].confidence == CONFIDENCE_VERIFIED


def test_confidence_is_reported_when_the_winner_has_no_digest():
    resolver = FakeResolver([FakeRoot(id="home", raw_path="models")])
    locations = FakeLocations({"m1": _winner("home", sha256=None)})
    availability = FakeAvailabilityRepo()
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    asyncio.run(projector.reconcile(FakeBackendRegistry([FakeBackend("local-1", NATIVE_LOCAL_DRIVER)])))

    assert availability.rows[("m1", "local-1")].confidence == CONFIDENCE_REPORTED


def test_an_offline_roots_winners_are_never_projected():
    resolver = FakeResolver([
        FakeRoot(id="home", raw_path="models", state="online"),
        FakeRoot(id="usb", raw_path="/mnt/usb", state="offline"),
    ])
    locations = FakeLocations({"m1": _winner("home"), "m2": _winner("usb")})
    availability = FakeAvailabilityRepo()
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    asyncio.run(projector.reconcile(FakeBackendRegistry([FakeBackend("local-1", NATIVE_LOCAL_DRIVER)])))

    assert ("m1", "local-1") in availability.rows
    assert ("m2", "local-1") not in availability.rows
    assert locations.calls[-1] == ["home"]


def test_a_stale_availability_row_is_removed_when_the_model_no_longer_wins():
    resolver = FakeResolver([FakeRoot(id="home", raw_path="models")])
    locations = FakeLocations({})
    availability = FakeAvailabilityRepo()
    availability.rows[("stale", "local-1")] = ModelAvailability(id="a1", model_id="stale", backend_id="local-1", ref="x")
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    summary = asyncio.run(projector.reconcile(FakeBackendRegistry([FakeBackend("local-1", NATIVE_LOCAL_DRIVER)])))

    assert ("stale", "local-1") not in availability.rows
    assert summary.removed == 1


def test_no_backend_registry_is_a_no_op():
    projector = NativeAvailabilityProjector(resolver=FakeResolver([]))

    summary = asyncio.run(projector.reconcile(None))

    assert summary.backend_ids == []


def test_per_backend_failure_is_swallowed_and_reported():
    resolver = FakeResolver([FakeRoot(id="home", raw_path="models")])
    locations = FakeLocations({"m1": _winner("home")})

    class FailingAvailabilityRepo(FakeAvailabilityRepo):
        def upsert(self, availability: ModelAvailability) -> ModelAvailability:
            if availability.backend_id == "broken":
                raise RuntimeError("boom")
            return super().upsert(availability)

    availability = FailingAvailabilityRepo()
    projector = NativeAvailabilityProjector(resolver=resolver, locations_repository=locations, availability_repository=availability)

    summary = asyncio.run(projector.reconcile(FakeBackendRegistry([
        FakeBackend("ok", NATIVE_LOCAL_DRIVER), FakeBackend("broken", NATIVE_LOCAL_DRIVER),
    ])))

    assert summary.backend_ids == ["ok"]
    assert summary.failed_backend_ids == ["broken"]


def test_a_registry_failure_never_reaches_the_caller():
    registry = Mock()
    registry.get_backends_for_engine.side_effect = RuntimeError("registry down")
    projector = NativeAvailabilityProjector(resolver=FakeResolver([]))

    summary = asyncio.run(projector.reconcile(registry))

    assert summary.backend_ids == []
