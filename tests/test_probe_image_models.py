"""Classification tests for the image-model probe. The probe's verdict drives a
model-selection decision, so 'OK' must mean real image bytes came back — never a
filtered/empty response."""
import base64
from types import SimpleNamespace

import httpx
import pytest

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


# --- Cloudflare Workers AI -------------------------------------------------
# Same bar as the Gemini rows: OK must mean decoded image bytes. Workers AI
# answers 200 with success=false on a model error, so the status code alone is
# not a verdict.

def _cf_client(status=200, payload=None, text="", exc=None, content_type="application/json",
               content=b"", sent=None):
    class _Client:
        def post(self, _url, json=None, **__):
            if sent is not None:
                sent.append(json)
            if exc:
                raise exc
            resp = SimpleNamespace(
                status_code=status, text=text, content=content,
                headers={"content-type": content_type})
            resp.json = (lambda: payload) if payload is not None else _raise_value
            return resp

    def _raise_value():
        raise ValueError("not json")

    return _Client()


def _cf_ok(data=b"abcd"):
    return {"success": True, "result": {"image": base64.b64encode(data).decode()}}


def test_cloudflare_ok_only_with_decoded_bytes():
    out = probe._probe_cloudflare(_cf_client(payload=_cf_ok()), "acct", "m")
    assert out.startswith("OK") and "4 bytes" in out


def test_cloudflare_success_false_is_error():
    payload = {"success": False, "errors": [{"message": "no such model"}], "result": None}
    out = probe._probe_cloudflare(_cf_client(payload=payload), "acct", "m")
    assert out.startswith("ERROR") and "no such model" in out


def test_cloudflare_missing_image_field_is_no_image():
    out = probe._probe_cloudflare(_cf_client(payload={"success": True, "result": {}}), "acct", "m")
    assert out.startswith("NO_IMAGE")


def test_cloudflare_undecodable_base64_is_error():
    payload = {"success": True, "result": {"image": "!!!not-base64!!!"}}
    out = probe._probe_cloudflare(_cf_client(payload=payload), "acct", "m")
    assert out.startswith("ERROR") and "base64" in out


def test_cloudflare_non_200_is_error():
    out = probe._probe_cloudflare(_cf_client(status=403, text="forbidden"), "acct", "m")
    assert out.startswith("ERROR") and "403" in out


def test_cloudflare_non_json_is_error():
    out = probe._probe_cloudflare(_cf_client(status=200), "acct", "m")
    assert out.startswith("ERROR") and "non-JSON" in out


def test_cloudflare_transport_error_is_error():
    out = probe._probe_cloudflare(_cf_client(exc=httpx.ConnectError("boom")), "acct", "m")
    assert out.startswith("ERROR") and "ConnectError" in out


def test_cloudflare_empty_decoded_payload_is_no_image():
    payload = {"success": True, "result": {"image": ""}}
    assert probe._probe_cloudflare(_cf_client(payload=payload), "acct", "m").startswith("NO_IMAGE")


# Two wire contracts on the same endpoint: Schnell returns base64 in the
# Cloudflare envelope, the SDXL pair returns a raw image stream with no
# envelope. Keying off the model name would misreport a model that changes
# shape, so the probe sniffs the content type — these pin that.

def test_cloudflare_image_stream_is_ok_without_an_envelope():
    out = probe._probe_cloudflare(
        _cf_client(content_type="image/png", content=b"\x89PNG1234"), "acct", "m")
    assert out.startswith("OK") and "8 bytes" in out


def test_cloudflare_image_stream_tolerates_content_type_params():
    out = probe._probe_cloudflare(
        _cf_client(content_type="image/jpeg; charset=binary", content=b"\xff\xd8ab"), "acct", "m")
    assert out.startswith("OK")


def test_cloudflare_empty_image_stream_is_no_image():
    out = probe._probe_cloudflare(_cf_client(content_type="image/png", content=b""), "acct", "m")
    assert out.startswith("NO_IMAGE") and "stream" in out


def test_cloudflare_stream_extension_follows_content_type(tmp_path):
    probe._probe_cloudflare(
        _cf_client(content_type="image/jpeg", content=b"\xff\xd8ab"), "acct", "m",
        out_dir=str(tmp_path))
    assert [p.name for p in tmp_path.iterdir()] == ["m.jpeg"]


def test_cloudflare_sends_the_models_own_steps_parameter():
    sent = []
    probe._probe_cloudflare(
        _cf_client(payload=_cf_ok(), sent=sent), "acct", "m",
        steps_param="num_steps", steps=20)
    assert sent == [{"prompt": probe._PROMPT, "num_steps": 20}]


def test_candidate_table_declares_a_steps_param_and_value_per_model():
    # SDXL takes num_steps (default/max 20); Schnell takes steps (default 4).
    # A candidate added without its own entry would silently get Schnell's.
    table = {model: (param, value) for model, param, value in probe._CLOUDFLARE_CANDIDATES}
    assert table["@cf/black-forest-labs/flux-1-schnell"] == ("steps", 4)
    for model, (param, value) in table.items():
        if "stable-diffusion-xl" in model:
            assert (param, value) == ("num_steps", 20), model


# --- image saving ----------------------------------------------------------
# A failed write must not downgrade a working model's verdict.

def test_save_writes_file_and_reports_path(tmp_path):
    suffix = probe._save(str(tmp_path), "@cf/vendor/model-1", b"abcd")
    written = list(tmp_path.glob("*.png"))
    assert len(written) == 1 and written[0].read_bytes() == b"abcd"
    assert str(written[0]) in suffix


def test_save_is_a_noop_without_out_dir():
    assert probe._save(None, "m", b"abcd") == ""


def test_save_is_a_noop_on_empty_data(tmp_path):
    assert probe._save(str(tmp_path), "m", b"") == ""
    assert not list(tmp_path.iterdir())


def test_ok_verdict_survives_a_failed_save(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    out = probe._probe_cloudflare(
        _cf_client(payload=_cf_ok()), "acct", "m", out_dir=str(blocker / "sub"))
    assert out.startswith("OK") and "save failed" in out


# --- CLI contract ----------------------------------------------------------
# The existing image-probe workflow invokes the module with no arguments; that
# must keep meaning "probe Gemini".

def test_default_provider_is_gemini(monkeypatch):
    called = []
    monkeypatch.setattr(probe, "_run_gemini", lambda out: called.append(("gemini", out)))
    monkeypatch.setattr(probe, "_run_cloudflare", lambda out: called.append(("cloudflare", out)))
    assert probe.main([]) == 0
    assert called == [("gemini", None)]


def test_provider_both_runs_both(monkeypatch):
    called = []
    monkeypatch.setattr(probe, "_run_gemini", lambda out: called.append("gemini"))
    monkeypatch.setattr(probe, "_run_cloudflare", lambda out: called.append("cloudflare"))
    assert probe.main(["--provider", "both", "--out", "d"]) == 0
    assert called == ["gemini", "cloudflare"]


def test_cloudflare_creds_required(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        probe._cloudflare_creds()
