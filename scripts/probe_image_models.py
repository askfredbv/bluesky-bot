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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
