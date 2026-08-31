"""Tests for utils.is_usable_image — the guard that stops invalid/oversize
generated media from shipping (compress_image returns original bytes on failure)."""
import io

from PIL import Image

from src.utils import is_usable_image


def _png(size=(4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_valid_png_is_usable():
    assert is_usable_image(_png()) is True


def test_garbage_bytes_are_not_usable():
    assert is_usable_image(b"not-an-image-at-all") is False


def test_empty_bytes_are_not_usable():
    assert is_usable_image(b"") is False


def test_oversized_image_is_not_usable():
    data = _png()
    assert is_usable_image(data, max_bytes=len(data) - 1) is False
    assert is_usable_image(data, max_bytes=len(data)) is True
