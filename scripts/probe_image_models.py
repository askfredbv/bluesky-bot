"""Diagnostic: find which image-generation model this GEMINI_API_KEY can reach.

Read-only — posts nothing, changes nothing. The bot's IMAGE_MODEL
(gemini-3.1-flash-image) stopped returning images around 2026-08-26 because
Google removed it from the key's reachable models. This enumerates the key's
image-capable models and actively probes a candidate set (both the Gemini
IMAGE-modality path the bot uses and the Imagen generate_images path) so we can
pick a working replacement. Run via the image-probe workflow — the key is
IP-restricted to the GitHub runners.
"""
import os
from typing import List

from google import genai
from google.genai import types

try:
    # The bot's live image model — probe the real one, not a guess.
    from src.config import IMAGE_MODEL
except Exception:  # pragma: no cover - defensive if run outside the repo root
    IMAGE_MODEL = "gemini-3.1-flash-image"

_PROMPT = ("A clean, minimal editorial illustration of a lighthouse at dusk. "
           "No text, flat design, muted modern palette.")

# generate_content candidates (Gemini image output via response_modalities=IMAGE)
_GEMINI_CANDIDATES = [
    "gemini-3.1-flash-image",                     # the bot's current (now-dead) model
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image-generation",
    "gemini-3-flash-preview",
    "gemini-omni-flash-preview",
    "gemini-omni-1.1-flash",
]
# generate_images candidates (Imagen family)
_IMAGEN_CANDIDATES = [
    "imagen-4.0-generate-001",
    "imagen-4.0-fast-generate-001",
    "imagen-4.0-generate-preview-06-06",
    "imagen-3.0-generate-002",
]


def _client() -> genai.Client:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY not set")
    return genai.Client(api_key=key)


def _list_image_like(client: genai.Client) -> List[str]:
    names: List[str] = []
    try:
        for m in client.models.list():
            short = (getattr(m, "name", "") or "").split("/")[-1]
            if "image" in short.lower() or "imagen" in short.lower():
                names.append(short)
    except Exception as exc:
        print(f"  (model list failed: {type(exc).__name__}: {exc})")
    return sorted(set(names))


def _probe_gemini(client: genai.Client, model: str) -> str:
    try:
        result = client.models.generate_content(
            model=model, contents=_PROMPT,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1")))
        for candidate in result.candidates or []:
            content = candidate.content
            for part in (content.parts if content else None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    return f"OK        image={len(inline.data)} bytes"
        return "NO_IMAGE  (text-only response)"
    except Exception as exc:
        return f"ERROR     {type(exc).__name__}: {str(exc)[:140]}"


def _probe_imagen(client: genai.Client, model: str) -> str:
    try:
        result = client.models.generate_images(
            model=model, prompt=_PROMPT,
            config=types.GenerateImagesConfig(number_of_images=1))
        images = getattr(result, "generated_images", None) or []
        if images:
            data = getattr(getattr(images[0], "image", None), "image_bytes", None)
            if data:
                return f"OK        image={len(data)} bytes"
            return "NO_IMAGE  (entry had no image_bytes — filtered?)"
        return "NO_IMAGE  (empty result)"
    except Exception as exc:
        return f"ERROR     {type(exc).__name__}: {str(exc)[:140]}"


def _probe_gemini_with_http_timeout(model: str) -> None:
    """Verify a request-level SDK timeout on the bot's image path (issue: shutdown hang).

    Confirms three things the src/agents.py hardening depends on, all against
    the model the bot actually uses:
      1. A generous per-request timeout (config=HttpOptions(timeout=30000ms))
         still returns image bytes — the timeout config does not break the call.
      2. A generous client-level timeout (genai.Client(http_options=...)) also
         returns image bytes.
      3. The unit is MILLISECONDS: a tiny timeout (1 ms) must raise fast rather
         than wait ~30 s. If 1 were seconds, the call would usually succeed.
    Read-only.
    """
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()

    print(f"\n=== request-level HttpOptions timeout on {model} (the bot's path) ===")

    # 1. Per-request config timeout, generous (30 s expressed as ms).
    try:
        client = genai.Client(api_key=key)
        result = client.models.generate_content(
            model=model, contents=_PROMPT,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1"),
                http_options=types.HttpOptions(timeout=30000)))
        got = _first_image_len(result)
        print(f"  per-request config timeout=30000ms   "
              f"{'OK  image=' + str(got) + ' bytes' if got else 'NO_IMAGE'}")
    except Exception as exc:
        print(f"  per-request config timeout=30000ms   ERROR {type(exc).__name__}: {str(exc)[:140]}")

    # 2. Client-level http_options timeout, generous.
    try:
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30000))
        result = client.models.generate_content(
            model=model, contents=_PROMPT,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1")))
        got = _first_image_len(result)
        print(f"  client-level  timeout=30000ms        "
              f"{'OK  image=' + str(got) + ' bytes' if got else 'NO_IMAGE'}")
    except Exception as exc:
        print(f"  client-level  timeout=30000ms        ERROR {type(exc).__name__}: {str(exc)[:140]}")

    # 3. Unit proof: 1 ms must fail fast. If ms, this errors immediately; if the
    #    unit were seconds, a 1 s budget would usually let the call through.
    try:
        client = genai.Client(api_key=key)
        client.models.generate_content(
            model=model, contents=_PROMPT,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1"),
                http_options=types.HttpOptions(timeout=1)))
        print("  per-request timeout=1ms              UNEXPECTED_OK "
              "(did NOT time out — unit may not be ms!)")
    except Exception as exc:
        print(f"  per-request timeout=1ms              EXPECTED_TIMEOUT "
              f"{type(exc).__name__}: {str(exc)[:100]}")


def _first_image_len(result: object) -> int:
    for candidate in getattr(result, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) if content else None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return len(inline.data)
    return 0


def main() -> int:
    client = _client()

    print("=== image/imagen models the key enumerates ===")
    found = _list_image_like(client)
    print("  " + (", ".join(found) if found else "(none reachable)"))

    gemini = list(dict.fromkeys(
        _GEMINI_CANDIDATES + [f for f in found if "imagen" not in f]))
    imagen = list(dict.fromkeys(
        _IMAGEN_CANDIDATES + [f for f in found if "imagen" in f]))

    print("\n=== generate_content (Gemini IMAGE modality — the bot's path) ===")
    for model in gemini:
        print(f"  {model:<46} {_probe_gemini(client, model)}")

    print("\n=== generate_images (Imagen path) ===")
    for model in imagen:
        print(f"  {model:<46} {_probe_imagen(client, model)}")

    print("\nPick any row marked OK for IMAGE_MODEL (and switch the call path if "
          "it is an Imagen model).")

    _probe_gemini_with_http_timeout(IMAGE_MODEL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
