import functools
import io
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.cover_store import PresetCoverStore
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.mappers import read_overlays
from src.features.presets.name_resolver import PresetNameResolver
from src.features.presets.repository import DatabasePresetRepository
from src.features.user_groups.repository import UserGroupRepository
from src.platform.security.user import AccountType, User
from tests.fixtures.seed import seed_user


def write_preset(root: Path, preset_id: str, name: str, modes=("txt2img", "img2img", "edit")) -> Path:
    preset_dir = root / name / "std"
    preset_dir.mkdir(parents=True)
    mode_list = "\n".join(f"  - {mode}" for mode in modes)
    (preset_dir / "preset.yml").write_text(
        f'schema: 1\nid: "{preset_id}"\nname: "{name}"\nversion: "1.0.0"\ncategory: "image"\n'
        f'engine: "native"\nmedia:\n  cover: public/cover.png\nmodes:\n{mode_list}\n'
    )
    (preset_dir / "public").mkdir()
    (preset_dir / "public" / "cover.png").write_bytes(png_bytes())
    (preset_dir / "public" / "alt.png").write_bytes(png_bytes())
    for mode in modes:
        (preset_dir / "modes" / mode).mkdir(parents=True)
        (preset_dir / "modes" / mode / "pipeline.yml").write_text("pipeline: []\n")
    return preset_dir


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def user(user_id: str, account_type: AccountType) -> User:
    return User(username=user_id, email=f"{user_id}@example.test", password_hash="x", account_type=account_type, id=user_id)


@dataclass
class MirrorWorld:
    root: Path
    storage: Path
    loader: PresetTemplateLoader
    repo: DatabasePresetRepository
    groups: UserGroupRepository
    collaborators: PresetCollaborators
    covers: PresetCoverStore
    admin: User
    regular: User

    def install(self, preset_id: str):
        return self.repo.install_preset(preset_id)

    def names(self) -> PresetNameResolver:
        return PresetNameResolver(self.loader)


def build_world(mock_db, tmp_path: Path) -> MirrorWorld:
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, "krea2", "Krea-2")
    write_preset(root, "flux", "Flux")
    storage = tmp_path / "storage"
    repo = DatabasePresetRepository()
    loader = PresetTemplateLoader([str(root)], overlay_source=functools.partial(read_overlays, repo))
    settings = Mock()
    settings.get_file_storage_directory.return_value = str(storage)
    covers = PresetCoverStore(settings, repo)
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={"blocked": False}), [])
    groups = UserGroupRepository()
    with patch("src.features.presets.collaborators.PresetFormSerializer"):
        collaborators = PresetCollaborators(
            preset_loader=loader,
            preset_processor=Mock(),
            template_processor=Mock(),
            file_repo=FilePresetRepository(loader),
            db_repo=repo,
            user_repo=Mock(),
            group_repo=groups,
            pipeline_builder=Mock(),
            pipe_catalog=Mock(),
            plugins=plugins,
            settings=settings,
            cover_store=covers,
        )
    seed_user(mock_db, "admin", account_type="ADMIN")
    seed_user(mock_db, "ann")
    return MirrorWorld(
        root=root,
        storage=storage,
        loader=loader,
        repo=repo,
        groups=groups,
        collaborators=collaborators,
        covers=covers,
        admin=user("admin", AccountType.ADMIN),
        regular=user("ann", AccountType.USER),
    )
