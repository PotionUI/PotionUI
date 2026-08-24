"""What a model looks like to a generating user versus to an administrator.

Where the bytes live, how many there are, their hash and when we last looked are
operational facts. Someone picking a model to generate with needs none of them.
"""

from src.features.models.records import Model, ModelInfo


ADMIN_ONLY = {'file_path', 'file_size', 'sha256', 'indexed_at', 'updated_at', 'is_directory'}


def make_model(**overrides):
    defaults = dict(
        id='m1',
        filename='detail.safetensors',
        file_path='models/loras/detail.safetensors',
        file_size=228458116,
        sha256='deadbeef',
        model_type='lora',
    )
    defaults.update(overrides)
    return Model(**defaults)


class TestDisplayName:
    def test_falls_back_to_the_filename_without_its_extension(self):
        """A freshly indexed library has no custom names and no provider metadata.
        Without this fallback every model would be unlabelled."""
        assert make_model().display_name == 'detail'

    def test_prefers_a_name_a_human_chose(self):
        model = make_model(custom_name='My Detail LoRA')
        assert model.display_name == 'My Detail LoRA'

    def test_prefers_a_provider_name_over_the_filename(self):
        model = make_model(providers=[ModelInfo(name='Detail Enhancer')])
        assert model.display_name == 'Detail Enhancer'

    def test_custom_name_outranks_provider_name(self):
        model = make_model(custom_name='Mine', providers=[ModelInfo(name='Theirs')])
        assert model.display_name == 'Mine'

    def test_provider_without_a_name_is_skipped(self):
        model = make_model(providers=[ModelInfo(name=None), ModelInfo(name='Second')])
        assert model.display_name == 'Second'

    def test_extensionless_filename_survives(self):
        assert make_model(filename='detail').display_name == 'detail'

    def test_no_filename_at_all_falls_back_to_the_id(self):
        assert make_model(filename=None).display_name == 'm1'


class TestUserFacingSerialization:
    def test_user_never_sees_path_size_hash_or_index_time(self):
        payload = make_model().to_dict(admin=False)

        for field in ADMIN_ONLY:
            assert field not in payload, f"{field} leaked to a non-admin"

    def test_admin_sees_all_of_them(self):
        payload = make_model().to_dict(admin=True)

        for field in ADMIN_ONLY:
            assert field in payload

    def test_user_gets_a_display_name(self):
        payload = make_model(custom_name='Nice Name').to_dict(admin=False)

        assert payload['name'] == 'Nice Name'

    def test_filename_stays_for_both_because_it_resolves_legacy_values(self):
        """Saved sessions reference models by path or filename. The picker matches on it.
        It is a key, not something to render."""
        assert make_model().to_dict(admin=False)['filename'] == 'detail.safetensors'

    def test_user_keeps_what_they_generate_with(self):
        model = make_model(description='Adds fine detail', model_metadata={'triggers': ['detail']})
        payload = model.to_dict(admin=False)

        assert payload['description'] == 'Adds fine detail'
        assert payload['model_metadata'] == {'triggers': ['detail']}
        assert payload['model_type'] == 'lora'
        assert payload['is_favorite'] is False

    def test_admin_is_the_default_so_internal_callers_are_unchanged(self):
        """to_dict() is called all over; defaulting to the fuller payload keeps
        existing behaviour and makes the restriction an explicit choice."""
        payload = make_model().to_dict()

        assert payload['sha256'] == 'deadbeef'

    def test_preview_media_is_visible_to_generating_users(self):
        """Pickers show the preview to non-admins, so it stays in the user payload."""
        preview = {'url': '/api/media/uploads/p.png', 'type': 'image'}
        payload = make_model(preview_media=preview).to_dict(admin=False)

        assert payload['preview_media'] == preview

    def test_preview_media_defaults_to_none(self):
        assert make_model().to_dict(admin=False)['preview_media'] is None


class TestFromRow:
    def _row(self, **overrides):
        base = {
            'id': 'm1', 'filename': 'a.safetensors', 'file_path': 'models/a.safetensors',
            'file_size': 1, 'sha256': 'x', 'model_type': 'lora',
            'created_at': None, 'updated_at': None, 'indexed_at': None,
            'description': None, 'prompting_guidance': None,
            'preview_media': None,
        }
        base.update(overrides)

        class Row(dict):
            def keys(self):
                return list(super().keys())

        return Row(base)

    def test_preview_media_json_is_decoded(self):
        import json
        preview = {'url': '/api/media/uploads/p.mp4', 'type': 'video', 'name': 'p.mp4'}
        model = Model.from_row(self._row(preview_media=json.dumps(preview)))
        assert model.preview_media == preview

    def test_preview_media_null_stays_none(self):
        assert Model.from_row(self._row(preview_media=None)).preview_media is None

    def test_is_directory_defaults_false_on_a_pre_migration_row(self):
        """A row from before migration 101 has no is_directory column at all."""
        assert Model.from_row(self._row()).is_directory is False

    def test_is_directory_true_is_read(self):
        assert Model.from_row(self._row(is_directory=1)).is_directory is True

    def test_is_directory_false_is_read(self):
        assert Model.from_row(self._row(is_directory=0)).is_directory is False
