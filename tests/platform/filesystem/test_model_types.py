"""`DIRECTORY_TO_MODEL_TYPE` is the canonical model depot layout every scanner,
indexer and downloader derives its own view from - these are the invariants
that let a derived view (an inverse, a subset, a filtered copy) stay correct
without re-checking its own literal values.
"""

from src.platform.filesystem.model_types import (
    DIRECTORY_TO_MODEL_TYPE,
    MODEL_DIRECTORY_NAMES,
    MODEL_TYPE_TO_DIRECTORY,
    MODEL_TYPES,
    SUPPORTED_MODEL_EXTENSIONS,
)


def test_the_inverse_map_round_trips_every_entry():
    for directory, model_type in DIRECTORY_TO_MODEL_TYPE.items():
        assert MODEL_TYPE_TO_DIRECTORY[model_type] == directory


def test_no_directory_is_mapped_to_more_than_one_type():
    assert len(set(DIRECTORY_TO_MODEL_TYPE.values())) == len(DIRECTORY_TO_MODEL_TYPE)


def test_no_type_is_mapped_to_more_than_one_directory():
    assert len(set(MODEL_TYPE_TO_DIRECTORY.values())) == len(MODEL_TYPE_TO_DIRECTORY)


def test_derived_collections_match_the_canonical_map():
    assert set(MODEL_DIRECTORY_NAMES) == set(DIRECTORY_TO_MODEL_TYPE.keys())
    assert set(MODEL_TYPES) == set(DIRECTORY_TO_MODEL_TYPE.values())


def test_llm_is_present_for_directory_per_model_callers_to_special_case():
    assert DIRECTORY_TO_MODEL_TYPE['llm'] == 'llm'


def test_conditioning_encoders_have_exactly_one_home():
    """TRELLIS.2's DINOv3 image conditioner sits in `text_encoders/` alongside
    the text encoders. Comfy-Org ships it under `clip_vision/`; defining that as
    a second directory would split every picker that lists an encoder."""
    assert DIRECTORY_TO_MODEL_TYPE['text_encoders'] == 'text_encoder'
    assert 'clip_vision' not in DIRECTORY_TO_MODEL_TYPE


def test_every_depot_scanner_shares_the_same_supported_extensions():
    from src.features.backends.native_model_scan import SUPPORTED_EXTENSIONS as native_scan_extensions
    from src.features.models.indexer import ModelScanner

    assert ModelScanner.SUPPORTED_EXTENSIONS is SUPPORTED_MODEL_EXTENSIONS
    assert native_scan_extensions is SUPPORTED_MODEL_EXTENSIONS
