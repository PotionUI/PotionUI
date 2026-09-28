from unittest.mock import Mock, patch

from src.features.preset_suite.resolver_factory import build_live_resolver


def test_build_live_resolver_derives_models_dir_from_the_root_resolvers_home(tmp_path):
    home = tmp_path / "models"
    fake_resolver = Mock(home_dir=Mock(return_value=home))

    with patch(
        "src.platform.filesystem.model_roots.ModelRootResolver",
        return_value=fake_resolver,
    ), patch("src.platform.settings.settings.Settings") as mock_settings_cls, \
       patch("src.features.models.repository.ModelRepository") as mock_repo_cls:
        mock_settings_cls.return_value = Mock()
        mock_repo_cls.return_value = Mock(get_all=Mock(return_value=[]))

        resolver = build_live_resolver()

    assert resolver.models_dir == home
