"""Assembling an ExecutionPackageV1 from a built pipeline."""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.features.generation.generation import deep_update
from src.features.generation.package_assembly import assemble_execution_package
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.remote_execution.policy import RemoteExecutionPolicy
from src.platform.worker_protocol import (
    ContentDigest,
    ExecutionPackageV1,
    ModelBundleManifestV1,
    read_envelope,
    to_wire,
)

ISSUED_AT = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


class FakeCatalog:
    def __init__(self, classes):
        self._classes = classes

    def get_pipe(self, name):
        return self._classes.get(name)


class LoaderPipe:
    @classmethod
    def get_default_config(cls):
        return {'model': 'models/checkpoints/default.safetensors', 'clip_skip': 2}


class GeneratorPipe:
    @classmethod
    def get_default_config(cls):
        return {'steps': 20, 'sampler': {'name': 'euler', 'scheduler': 'normal'}}


@pytest.fixture
def catalog():
    return FakeCatalog({'model_loader/sdxl': LoaderPipe, 'generator/sdxl': GeneratorPipe})


@pytest.fixture
def model_bundle():
    """Supplied by the model layer, which owns digests - assembly never
    computes one."""
    return ModelBundleManifestV1(
        bundle_id='bundle-1',
        bundle_digest=ContentDigest(algorithm='sha256', hex='ab' * 32),
        entries=(),
    )


def build(pipes, *, generation_id='gen-1', preset_id='preset-1', engine='native'):
    return BuiltPipeline(
        generation_id=generation_id,
        preset_id=preset_id,
        preset_template=SimpleNamespace(engine=engine, id=preset_id),
        pipes=pipes,
    )


def pipe(name, config, *, pipe_id=None, enabled=True, inputs=None):
    return {
        'id': pipe_id,
        'name': name,
        'enabled': enabled,
        'config': config,
        'input': inputs or [],
        'cache': [],
    }


SAMPLE_PIPES = [
    pipe('model_loader/sdxl', {'model': 'models/checkpoints/chosen.safetensors'},
         pipe_id='loader'),
    pipe('generator/sdxl', {'steps': 30},
         pipe_id='gen',
         inputs=[{'name': 'model', 'provider': 'loader', 'output_var': 'model',
                  'enabled': True}]),
]


class TestPackageShape:
    def test_assembles_a_valid_package(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)),
            pipe_catalog=catalog,
            model_bundle=model_bundle,
            issued_at=ISSUED_AT,
        )

        assert isinstance(package, ExecutionPackageV1)
        assert package.execution_id == 'gen-1'
        assert package.idempotency_key == 'gen-1'
        assert package.engine == 'native'
        assert package.metadata['preset_id'] == 'preset-1'
        assert [p.pipe_type for p in package.processed_pipes.pipes] == [
            'model_loader/sdxl', 'generator/sdxl'
        ]

    def test_engine_comes_from_the_preset_template(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES), engine='comfyui'),
            pipe_catalog=catalog,
            model_bundle=model_bundle,
            issued_at=ISSUED_AT,
        )

        assert package.engine == 'comfyui'

    def test_disabled_pipes_travel_with_the_flag(self, catalog, model_bundle):
        pipes = [pipe('generator/sdxl', {}, pipe_id='gen', enabled=False)]

        package = assemble_execution_package(
            build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT,
        )

        assert package.processed_pipes.pipes[0].enabled is False

    def test_expiry_is_carried(self, catalog, model_bundle):
        expires = ISSUED_AT + timedelta(minutes=30)

        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT, expires_at=expires,
        )

        assert package.expires_at == expires

    def test_expiry_defaults_to_the_policy_ttl_after_issued_at(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        assert package.expires_at == ISSUED_AT + timedelta(
            seconds=RemoteExecutionPolicy().package_ttl_seconds
        )

    def test_a_custom_policy_changes_the_default_expiry(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
            policy=RemoteExecutionPolicy(package_ttl_seconds=60),
        )

        assert package.expires_at == ISSUED_AT + timedelta(seconds=60)

    def test_limits_default_to_the_policy_when_none_are_given(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        assert package.limits == RemoteExecutionPolicy().default_limits()

    def test_round_trips_through_the_envelope(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        decoded = read_envelope(to_wire(package))

        assert decoded == package


class TestEffectiveConfigInThePackage:
    def test_config_is_the_merged_effective_config(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        loader, generator = package.processed_pipes.pipes
        assert loader.config == {
            'model': 'models/checkpoints/chosen.safetensors',
            'clip_skip': 2,
        }
        assert generator.config == {
            'steps': 30,
            'sampler': {'name': 'euler', 'scheduler': 'normal'},
        }

    def test_equals_what_the_worker_would_have_merged_at_execution_time(
        self, catalog, model_bundle
    ):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        for shipped, source in zip(package.processed_pipes.pipes, SAMPLE_PIPES):
            pipe_class = catalog.get_pipe(source['name'])
            executor_config = deep_update(
                pipe_class.get_default_config() or {}, copy.deepcopy(source['config'])
            )
            assert shipped.config == executor_config

            # And the worker's own merge over what it received adds nothing.
            assert deep_update(
                pipe_class.get_default_config() or {}, copy.deepcopy(shipped.config)
            ) == shipped.config

    def test_a_non_json_config_value_is_rejected_at_the_boundary(
        self, catalog, model_bundle
    ):
        pipes = [pipe('generator/sdxl', {'image': object()}, pipe_id='gen')]

        with pytest.raises(ValidationError):
            assemble_execution_package(
                build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
                issued_at=ISSUED_AT,
            )


class TestPipeIdentity:
    def test_pipes_without_an_id_get_a_unique_one(self, catalog, model_bundle):
        pipes = [
            pipe('generator/sdxl', {}),
            pipe('generator/sdxl', {}),
        ]

        package = assemble_execution_package(
            build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT,
        )

        ids = [p.pipe_id for p in package.processed_pipes.pipes]
        assert ids == ['generator/sdxl#0', 'generator/sdxl#1']

    def test_preset_declared_ids_are_kept(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        assert [p.pipe_id for p in package.processed_pipes.pipes] == ['loader', 'gen']


class TestInputWiring:
    def test_inputs_are_keyed_by_parameter(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        assert package.processed_pipes.pipes[1].inputs == {
            'model': [{'provider': 'loader', 'output_var': 'model', 'enabled': True}]
        }

    def test_a_parameter_fed_by_several_providers_keeps_all_of_them_in_order(
        self, catalog, model_bundle
    ):
        pipes = [pipe('generator/sdxl', {}, pipe_id='gen', inputs=[
            {'name': 'image', 'provider': 'first', 'output_var': 'image', 'enabled': True},
            {'name': 'image', 'provider': 'second', 'output_var': 'image', 'enabled': False},
        ])]

        package = assemble_execution_package(
            build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT,
        )

        assert package.processed_pipes.pipes[0].inputs['image'] == [
            {'provider': 'first', 'output_var': 'image', 'enabled': True},
            {'provider': 'second', 'output_var': 'image', 'enabled': False},
        ]


class TestInputAssetWiring:
    def test_without_a_storage_dir_no_collection_runs(self, catalog, model_bundle):
        package = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog,
            model_bundle=model_bundle, issued_at=ISSUED_AT,
        )

        assert package.input_assets is None

    def test_a_real_file_under_storage_dir_is_tokenized(self, catalog, model_bundle, tmp_path):
        planted = tmp_path / "uploads" / "reference.png"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"reference bytes")

        pipes = [pipe('generator/sdxl', {'image': str(planted)}, pipe_id='gen')]

        package = assemble_execution_package(
            build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT, storage_dir=tmp_path,
        )

        assert package.input_assets is not None
        assert len(package.input_assets.assets) == 1
        assert package.processed_pipes.pipes[0].config['image'] == (
            f"asset://{package.input_assets.assets[0].logical_id}"
        )
        assert str(tmp_path) not in to_wire(package)

    def test_a_package_without_any_real_file_gets_no_manifest(self, catalog, model_bundle, tmp_path):
        pipes = [pipe('generator/sdxl', {'image': 'not/a/real/file.png'}, pipe_id='gen')]

        package = assemble_execution_package(
            build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT, storage_dir=tmp_path,
        )

        assert package.input_assets is None
        assert package.processed_pipes.pipes[0].config['image'] == 'not/a/real/file.png'

    def test_the_request_digest_changes_when_collection_tokenizes_a_path(
        self, catalog, model_bundle, tmp_path
    ):
        """Collection runs before digesting, so the token - not the host
        path - is what the digest covers."""
        planted = tmp_path / "uploads" / "reference.png"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"reference bytes")
        pipes = [pipe('generator/sdxl', {'image': str(planted)}, pipe_id='gen')]

        without = assemble_execution_package(
            build(copy.deepcopy(pipes)), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT,
        )
        with_collection = assemble_execution_package(
            build(copy.deepcopy(pipes)), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT, storage_dir=tmp_path,
        )

        assert without.request_digest != with_collection.request_digest

    def test_round_trips_through_the_envelope_with_input_assets(
        self, catalog, model_bundle, tmp_path
    ):
        planted = tmp_path / "uploads" / "reference.png"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"reference bytes")
        pipes = [pipe('generator/sdxl', {'image': str(planted)}, pipe_id='gen')]

        package = assemble_execution_package(
            build(pipes), pipe_catalog=catalog, model_bundle=model_bundle,
            issued_at=ISSUED_AT, storage_dir=tmp_path,
        )

        assert read_envelope(to_wire(package)) == package


class TestRequestDigest:
    def test_is_stable_for_the_same_package(self, catalog, model_bundle):
        kwargs = dict(pipe_catalog=catalog, model_bundle=model_bundle, issued_at=ISSUED_AT)

        first = assemble_execution_package(build(copy.deepcopy(SAMPLE_PIPES)), **kwargs)
        second = assemble_execution_package(build(copy.deepcopy(SAMPLE_PIPES)), **kwargs)

        assert first.request_digest == second.request_digest

    def test_changes_when_the_body_changes(self, catalog, model_bundle):
        kwargs = dict(pipe_catalog=catalog, model_bundle=model_bundle, issued_at=ISSUED_AT)
        other = copy.deepcopy(SAMPLE_PIPES)
        other[1]['config']['steps'] = 31

        first = assemble_execution_package(build(copy.deepcopy(SAMPLE_PIPES)), **kwargs)
        second = assemble_execution_package(build(other), **kwargs)

        assert first.request_digest != second.request_digest

    def test_covers_the_merged_defaults_not_only_the_shipped_config(
        self, catalog, model_bundle
    ):
        """A default that changes underneath must change the digest, or a
        worker could not tell one effective pipeline from another."""

        class OtherDefaults(GeneratorPipe):
            @classmethod
            def get_default_config(cls):
                return {'steps': 20, 'sampler': {'name': 'dpmpp_2m', 'scheduler': 'normal'}}

        kwargs = dict(model_bundle=model_bundle, issued_at=ISSUED_AT)
        first = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)), pipe_catalog=catalog, **kwargs
        )
        second = assemble_execution_package(
            build(copy.deepcopy(SAMPLE_PIPES)),
            pipe_catalog=FakeCatalog(
                {'model_loader/sdxl': LoaderPipe, 'generator/sdxl': OtherDefaults}
            ),
            **kwargs,
        )

        assert first.request_digest != second.request_digest
