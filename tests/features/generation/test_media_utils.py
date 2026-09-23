import base64
import io

from PIL import Image

from src.features.generation.media_utils import create_base64_image


def _decode(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def test_rgba_image_encodes_as_png_with_alpha_preserved():
    image = Image.new("RGBA", (4, 4), (10, 20, 30, 128))

    b64 = create_base64_image(image, max_dimension=768)

    decoded = _decode(b64)
    assert decoded.format == "PNG"
    assert decoded.mode == "RGBA"
    assert decoded.getpixel((0, 0)) == (10, 20, 30, 128)


def test_rgb_image_still_encodes_as_jpeg():
    image = Image.new("RGB", (4, 4), (10, 20, 30))

    b64 = create_base64_image(image, max_dimension=768)

    decoded = _decode(b64)
    assert decoded.format == "JPEG"
