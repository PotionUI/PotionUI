"""Regression coverage for the generation persistence audit: a Video
Director document (form_data.video_director) and the live editor snapshot
(prompt_state.videoDirector) must both survive create() -> get_by_id() so
history detail and "reuse settings" can reproduce a Director run faithfully.
"""
import unittest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.platform.util.ids import generate_ulid


class TestVideoDirectorRoundtrip(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()

    def test_form_data_video_director_survives_create_and_read(self):
        director_doc = {
            "schema_version": 1,
            "mode": "director",
            "settings": {"fps": 24, "duration": 5.0, "seed": 4242},
            "segments": [
                {"id": "seg-1", "prompt": "a cat", "sub_type": "t2v"},
                {"id": "seg-2", "prompt": "a dog", "sub_type": "chain"},
            ],
            "media": [{"id": "m1", "role": "keyframe", "at": 0.5, "media": {"path": "/x"}}],
            "audio": [],
            "ic_lora": [],
        }

        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data={"video_director": director_doc, "seed": 4242},
            user_id=self.user_id,
            status="pending",
        )
        self.repo.create(generation)

        fetched = self.repo.get_by_id(generation.id)

        self.assertEqual(fetched.form_data["video_director"], director_doc)
        self.assertEqual(fetched.form_data["video_director"]["segments"], director_doc["segments"])

    def test_prompt_state_video_director_survives_create_and_read(self):
        prompt_state = {
            "prompt": "",
            "negativePrompt": "",
            "videoDirector": {
                "segments": [
                    {"id": "seg-1", "prompt": "a cat"},
                    {"id": "seg-2", "prompt": "a dog"},
                ],
                "keyframes": [{"id": "kf-1", "at": 0.5}],
            },
        }

        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data={},
            user_id=self.user_id,
            status="pending",
            prompt_state=prompt_state,
        )
        self.repo.create(generation)

        fetched = self.repo.get_by_id(generation.id)

        self.assertEqual(fetched.prompt_state["videoDirector"], prompt_state["videoDirector"])

    def test_history_detail_dict_carries_both_forms(self):
        """`Generation.to_dict()` is what the history-detail endpoint (and the
        history list) sends to the frontend -- confirm neither field is
        dropped on the way out."""
        director_doc = {"schema_version": 1, "mode": "director", "segments": [{"id": "seg-1", "prompt": "x"}]}
        prompt_state = {"videoDirector": {"segments": [{"id": "seg-1", "prompt": "x"}]}}

        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data={"video_director": director_doc},
            user_id=self.user_id,
            status="pending",
            prompt_state=prompt_state,
        )
        self.repo.create(generation)

        fetched = self.repo.get_by_id(generation.id)
        data = fetched.to_dict()

        self.assertEqual(data["form_data"]["video_director"], director_doc)
        self.assertEqual(data["prompt_state"]["videoDirector"], prompt_state["videoDirector"])


if __name__ == "__main__":
    unittest.main()
