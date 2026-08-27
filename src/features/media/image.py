import os
from datetime import datetime

from PIL import Image

from src.platform.settings.settings import Settings
from src.platform.templating import TemplateProcessor
from src.features.presets.templates import PresetTemplate


class ImageWriter:

    def __init__(
            self,
            template_processor: TemplateProcessor,
            settings: Settings,
    ):
        self.template_processor = template_processor
        self.settings = settings

    def get_image_path_for_save(
            self,
            image: Image.Image,
            preset: PresetTemplate,
    ) -> str:
        image_name_tpl = self.settings.get_setting("image_name_tpl")
        image_ctx = {
            "preset": {
                "name": preset.name,
            },
            "datetime": datetime.now().strftime("%Y-%m-%d-%H-%M-%S"),
            "image": {
                # Output is always PNG; templates can reference the extension via `image.ext`.
                "ext": "png",
            }
        }
        gallery_path_tpl = self.settings.get_setting("gallery_path_tpl")
        gallery_ctx = {
            "preset": {
                "name": preset.name,
            },
            "datetime": {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H-%M-%S"),
            },
        }

        image_file_name = self.template_processor.process_template(image_name_tpl, image_ctx)
        gallery_path = self.template_processor.process_template(gallery_path_tpl, gallery_ctx)

        if "/" in gallery_path:
            os.makedirs(gallery_path, exist_ok=True)

        return f"{gallery_path}/{image_file_name}"


    def save(self, image: Image.Image, preset: PresetTemplate) -> str:
        image_path = self.get_image_path_for_save(image, preset)
        image.save(image_path)

        return image_path
