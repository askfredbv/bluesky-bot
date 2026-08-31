"""Classification tests for the image-model probe. The probe's verdict drives a
model-selection decision, so 'OK' must mean real image bytes came back — never a
filtered/empty response."""
from types import SimpleNamespace

from scripts import probe_image_models as probe


def _client(result=None, exc=None):
    class _Models:
        def generate_content(self, **_):
            if exc:
                raise exc
            return result

        def generate_images(self, **_):
            if exc:
                raise exc
            return result

    return SimpleNamespace(models=_Models())


def _gemini_result(data):
    part = SimpleNamespace(inline_data=SimpleNamespace(data=data) if data is not None else None)
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]))
    return SimpleNamespace(candidates=[cand])


def test_gemini_ok_only_with_bytes():
    assert probe._probe_gemini(_client(_gemini_result(b"abcd")), "m").startswith("OK")


def test_gemini_text_only_is_no_image():
    assert probe._probe_gemini(_client(_gemini_result(None)), "m").startswith("NO_IMAGE")


def test_gemini_exception_is_error():
    out = probe._probe_gemini(_client(exc=RuntimeError("boom")), "m")
    assert out.startswith("ERROR") and "RuntimeError" in out


def test_imagen_ok_only_with_bytes():
    img = SimpleNamespace(image=SimpleNamespace(image_bytes=b"abcd"))
    res = SimpleNamespace(generated_images=[img])
    assert probe._probe_imagen(_client(res), "m").startswith("OK")


def test_imagen_entry_without_bytes_is_no_image():
    img = SimpleNamespace(image=SimpleNamespace(image_bytes=None))
    res = SimpleNamespace(generated_images=[img])
    assert probe._probe_imagen(_client(res), "m").startswith("NO_IMAGE")


def test_imagen_empty_result_is_no_image():
    res = SimpleNamespace(generated_images=[])
    assert probe._probe_imagen(_client(res), "m").startswith("NO_IMAGE")
