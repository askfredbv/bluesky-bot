"""Diagnostic: find which image-generation model this GEMINI_API_KEY can reach.

Read-only — posts nothing, changes nothing. History: the bot's IMAGE_MODEL
(gemini-3.1-flash-image) briefly stopped returning images around 2026-08-26
when Google removed it from the key's reachable models; it was reachable again
by 2026-08-31 (this probe, run 33406401910). The probe stays useful as the
standing diagnostic — it enumerates the key's image-capable models and actively
probes a candidate set (both the Gemini IMAGE-modality path the bot uses and the
Imagen generate_images path) so a working replacement is one dispatch away if the
model drops off again, and it verifies the request-level HttpOptions timeout the
bot relies on. Run via the image-probe workflow — the key is IP-restricted to the
GitHub runners.

``--out DIR`` writes the generated images so they can be compared by eye.
A Cloudflare Workers AI provider (2026-10-19) was removed on 2026-11-18,
together with its secrets; see git history for the code.
"""
import argparse
import os
from pathlib import Path
from typing import List, Optional, Tuple

from google import genai
from google.genai import types

try:
    # The bot's live image model, prompt and topic pool — probe the real ones,
    # not a guess. The bake-off below is only evidence if it sends what the bot sends.
    from src.config import (
        FALLBACK_VISUAL_PROMPT, IMAGE_MODEL, MENTOR_TOPICS, VISUAL_PROMPT_CONSTRAINTS,
    )
except Exception:  # pragma: no cover - defensive if run outside the repo root
    IMAGE_MODEL = "gemini-3.1-flash-image"
    FALLBACK_VISUAL_PROMPT = (
        "A clean, minimal editorial illustration representing the concept: '{topic}'."
    )
    VISUAL_PROMPT_CONSTRAINTS = " No text, no people."
    MENTOR_TOPICS = []

# Bake-off defaults: the model the bot runs today against the half-price Lite tier
# the 2026-10-19 probe run turned up. Same call path, same prompt, same resolution —
# the model is the only variable, which is the point.
_BAKEOFF_MODELS = [IMAGE_MODEL, "gemini-3.1-flash-lite-image"]
_BAKEOFF_TOPIC_COUNT = 5

_PROMPT = ("A clean, minimal editorial illustration of a lighthouse at dusk. "
           "No text, flat design, muted modern palette.")

# generate_content candidates (Gemini image output via response_modalities=IMAGE)
_GEMINI_CANDIDATES = [
    "gemini-3.1-flash-image",                     # the bot's live IMAGE_MODEL
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

#: Magic bytes -> extension. Sniffing is the only source of truth here, because
#: the other two lie. Measured on the 2026-10-19 artifacts: FLUX.1 Schnell's
#: base64 envelope decodes to JPEG, and SDXL Lightning's stream arrives under a
#: ``Content-Type: image/png`` header carrying JPEG bytes — so neither the model
#: documentation nor the server's own header can be trusted to name the format.
#: Gemini's inline_data is JPEG too. Only SDXL base actually returned PNG.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def _sniff_ext(data: bytes, fallback: str = "png") -> str:
    """The extension the BYTES say, not the one the header or the docs claim."""
    for magic, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return fallback


def _save(out_dir: Optional[str], name: str, data: bytes, ext: str = "png") -> str:
    """Write probe output for eyeballing. Returns a ' -> path' suffix, or ''.

    The extension comes from the payload's magic bytes; ``ext`` is only the
    fallback for a format we do not recognise. A file whose extension contradicts
    its contents gets rejected by previewers and image tools, which defeats the
    one purpose of writing it — being looked at.

    Never raises: a failed write must not turn a working model into an ERROR
    row, because the row is what the model-selection decision reads.
    """
    if not out_dir or not data:
        return ""
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in name)
    safe_ext = _sniff_ext(data, "".join(c for c in ext if c.isalnum()) or "png")
    try:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{safe}.{safe_ext}"
        target.write_bytes(data)
        return f"  -> {target}"
    except OSError as exc:
        return f"  (save failed: {type(exc).__name__}: {str(exc)[:120]})"


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


def _probe_gemini(client: genai.Client, model: str, out_dir: Optional[str] = None) -> str:
    try:
        result = client.models.generate_content(
            model=model, contents=_PROMPT,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1")))
        data = _first_image_bytes(result)
        if data:
            return f"OK        image={len(data)} bytes{_save(out_dir, model, data)}"
        return "NO_IMAGE  (text-only response)"
    except Exception as exc:
        return f"ERROR     {type(exc).__name__}: {str(exc)[:140]}"


def _probe_imagen(client: genai.Client, model: str, out_dir: Optional[str] = None) -> str:
    try:
        result = client.models.generate_images(
            model=model, prompt=_PROMPT,
            config=types.GenerateImagesConfig(number_of_images=1))
        images = getattr(result, "generated_images", None) or []
        if images:
            data = getattr(getattr(images[0], "image", None), "image_bytes", None)
            if data:
                return f"OK        image={len(data)} bytes{_save(out_dir, model, data)}"
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


def _first_image_bytes(result: object) -> bytes:
    """The first inline image payload in a generate_content result, or b''.

    One extraction for all three Gemini callers (reachability probe, bake-off,
    timeout check): they must agree on what counts as an image, or their verdicts
    are not comparable.
    """
    for candidate in getattr(result, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) if content else None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data:
                return bytes(data)
    return b""


def _first_image_len(result: object) -> int:
    return len(_first_image_bytes(result))


def _run_gemini(out_dir: Optional[str]) -> None:
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
        print(f"  {model:<46} {_probe_gemini(client, model, out_dir)}")

    print("\n=== generate_images (Imagen path) ===")
    for model in imagen:
        print(f"  {model:<46} {_probe_imagen(client, model, out_dir)}")

    print("\nPick any row marked OK for IMAGE_MODEL (and switch the call path if "
          "it is an Imagen model).")

    _probe_gemini_with_http_timeout(IMAGE_MODEL)


def _bakeoff_mode_label(crafted: bool, summary: str) -> str:
    """Name the mode honestly in the run header, since the header is what a reader
    of the log takes the evidence to be."""
    if not crafted:
        return "static fallback prompt"
    if summary:
        return "crafted — production assembly, with thread context"
    return "crafted — production assembly, topic only (no thread context)"


def _topic_slug(topic: str) -> str:
    """A short, stable, filesystem-safe stem for a Mentor topic."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in topic).split()][:5]
    return "-".join(w.lower() for w in words) or "topic"


def _bakeoff_prompts(
    count: int, crafted: bool = False, summary: str = ""
) -> List[Tuple[str, str]]:
    """``(topic, prompt)`` for the first ``count`` Mentor topics.

    First N rather than a random sample, so two runs are comparable: the point is
    to vary the MODEL, and a different topic set each run reintroduces exactly the
    confound the bake-off exists to remove.

    ``crafted=False`` sends the static fallback: deterministic, so the model is the
    only variable. ``crafted=True`` goes through ``agents.build_visual_prompt`` —
    the same assembly production uses, model-written prompt plus the appended
    constraints — which is non-deterministic and therefore useless for comparing
    models. Use it to check a constraint change, not to pick a model.

    **It is the production ASSEMBLY, not a production run.** Live Mentor and
    Strategist posts hand the crafter an 800-character summary of the finished
    thread; here ``summary`` is empty unless supplied, so the crafter writes from
    the topic alone and invents a different scene than it would in production.
    That does not weaken a constraint check — the constraints are appended after
    the crafter either way — but it does mean the pictures are not the pictures a
    real run would produce. Pass ``--summary`` with representative thread text to
    close that gap. (Codex P2, 2026-10-19.)
    """
    topics = MENTOR_TOPICS[:count]
    if not crafted:
        return [(t, FALLBACK_VISUAL_PROMPT.format(topic=t) + VISUAL_PROMPT_CONSTRAINTS)
                for t in topics]

    import asyncio

    from src.agents import build_visual_prompt

    key = (os.environ.get("GEMINI_API_KEY") or "").strip()

    async def _all() -> List[Tuple[str, str]]:
        return [(t, await build_visual_prompt(key, t, summary)) for t in topics]

    return asyncio.run(_all())


def _probe_gemini_prompt(
    client: genai.Client, model: str, prompt: str, stem: str, out_dir: Optional[str]
) -> str:
    """One bake-off cell: a given model against a given prompt. Mirrors
    ``_probe_gemini`` but takes the prompt instead of the module-level one."""
    try:
        result = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="1:1")))
        data = _first_image_bytes(result)
        if data:
            return f"OK        image={len(data)} bytes{_save(out_dir, stem, data)}"
        return "NO_IMAGE  (text-only response)"
    except Exception as exc:
        return f"ERROR     {type(exc).__name__}: {str(exc)[:140]}"


def _run_bakeoff(
    out_dir: Optional[str], models: List[str], count: int,
    crafted: bool = False, summary: str = "",
) -> None:
    """Same prompt, same call path, one image per (topic, model).

    Files are named ``<topic>__<model>.png`` — topic first on purpose, so a plain
    alphabetical listing puts each topic's variants next to each other and the
    comparison is a scroll rather than a lookup.

    This is the step that makes the model choice evidence rather than taste: the
    2026-10-19 probe compared one lighthouse, and one image cannot carry a
    decision that changes every picture in the feed.
    """
    prompts = _bakeoff_prompts(count, crafted, summary)
    if not prompts:
        print("Bake-off: no Mentor topics available; nothing to compare.")
        return

    client = _client()
    print(f"\n=== bake-off: {len(prompts)} Mentor topics x {len(models)} models "
          f"({len(prompts) * len(models)} images, "
          f"{_bakeoff_mode_label(crafted, summary)}) ===")
    for topic, prompt in prompts:
        print(f"\n  topic: {topic}")
        # The prompt is the thing under test when --crafted is on, so show it.
        if crafted:
            print(f"    prompt: {prompt}")
        for model in models:
            stem = f"{_topic_slug(topic)}__{model}"
            print(f"    {model:<40} {_probe_gemini_prompt(client, model, prompt, stem, out_dir)}")
    print("\nCompare the files pairwise; a byte count says nothing about whether the "
          "picture suits the post.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only image-model diagnostic. Probes which models return "
                    "real image bytes; --out writes them so they can be compared by eye.")
    parser.add_argument(
        "--out", default=None, metavar="DIR",
        help="write the generated images here so they can be compared by eye")
    parser.add_argument(
        "--bakeoff", action="store_true",
        help="compare image MODELS on the bot's real fallback prompt over real "
             "Mentor topics, instead of probing which models are reachable")
    parser.add_argument(
        "--bakeoff-models", default=",".join(_BAKEOFF_MODELS), metavar="A,B",
        help=f"comma-separated models for --bakeoff (default: {','.join(_BAKEOFF_MODELS)})")
    parser.add_argument(
        "--topics", type=int, default=_BAKEOFF_TOPIC_COUNT, metavar="N",
        help=f"how many Mentor topics to run in --bakeoff (default: {_BAKEOFF_TOPIC_COUNT})")
    parser.add_argument(
        "--crafted", action="store_true",
        help="--bakeoff: build the prompt the way production does (model-written + "
             "appended constraints) instead of the static fallback. Non-deterministic, "
             "so use it to check a prompt change, not to compare models")
    parser.add_argument(
        "--summary", default="", metavar="TEXT",
        help="--crafted: representative thread text for the crafter. Production passes "
             "800 chars of the finished thread; without this the crafter sees only the "
             "topic and writes a different scene than a real run would")
    args = parser.parse_args(argv)

    if args.bakeoff:
        models = [m.strip() for m in args.bakeoff_models.split(",") if m.strip()]
        if not models:
            parser.error("--bakeoff-models resolved to no models")
        _run_bakeoff(args.out, models, max(1, args.topics), args.crafted, args.summary)
        if args.out:
            print(f"\nImages written to {args.out}")
        return 0

    _run_gemini(args.out)
    if args.out:
        print(f"\nImages written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
