from typing import List, Dict, Any

from src.pipelines.outputs import (
    GalleryGenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
    AudioGenerationOutput,
)
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    PipeOutputSpec,
    PipeInputSpec,
    PipeInput,
    PipeOutput,
    IOType,
    PipeConfigSpec,
)


class GalleryPipe(BasePipe):
    name = "gallery"
    description = "Pipe that will output an gallery"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {}

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec(
                name="derived",
                param_type=bool,
                default=False,
                description="Mark emitted media as derived from another final output of this generation (e.g. an enhance pass). Presentation hint only - persisted file order is unchanged.",
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, False, "List of images to output", True),
            PipeInputSpec("video", IOType.VIDEO, False, "List of videos to output", True),
            PipeInputSpec("audio", IOType.AUDIO, False, "List of audio files to output", True),
            PipeInputSpec("seed", IOType.SEED, False, "Seed used for generation", True),
        ]


    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "List of images that were inputted (it just passes them forward)", True),
            PipeOutputSpec("video", IOType.VIDEO, "List of videos that were inputted (it just passes them forward)", True),
            PipeOutputSpec("audio", IOType.AUDIO, "List of audio files that were inputted (it just passes them forward)", True),
        ]

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = pipe_input.input.get("image", [])
        videos = pipe_input.input.get("video", [])
        audios = pipe_input.input.get("audio", [])

        derived = self.config.get("derived", False)
        if isinstance(derived, str):
            derived = derived.lower() in ("true", "1", "yes", "on")
        derived = bool(derived)

        image_outputs = []
        video_outputs = []
        audio_outputs = []

        # Handle images
        if images:
            for index, image in enumerate(images):
                image_outputs.append(ImageGenerationOutput(
                    image=image,
                    temporary=False,
                    derived=derived,
                ))

        # Handle videos
        if videos:
            for index, video_path in enumerate(videos):
                video_outputs.append(VideoGenerationOutput(
                    video_path=video_path,
                    temporary=False,
                    derived=derived,
                ))

        # Handle audio - AudioGenerationOutput has no `derived` field (unlike
        # image/video), so it is omitted here rather than passed and ignored.
        if audios:
            for index, audio_path in enumerate(audios):
                audio_outputs.append(AudioGenerationOutput(
                    audio_path=audio_path,
                    temporary=False,
                ))

        # Send gallery output with properly separated media
        if image_outputs or video_outputs or audio_outputs:
            generation_outputs(GalleryGenerationOutput(
                images=image_outputs,
                videos=video_outputs,
                audios=audio_outputs,
            ))

        return PipeOutput(output={"image": images, "video": videos, "audio": audios})
