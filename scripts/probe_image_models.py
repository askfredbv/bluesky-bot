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

Second provider, added 2026-10-19 (``--provider cloudflare``): Cloudflare Workers
AI running FLUX.1 Schnell. Rationale in ``docs/REVIEW_blubot_2026-10-19.md`` §C4 —
it is the only candidate that takes images off Google entirely, which is the
single point of failure ``PLAN_provider_agnostic.md`` names for a Google-free
configuration. Cost is *not* the argument (~$2-3/month against $0 at our volume,
which this project has already said does not decide anything). The open question
is aesthetic, and a byte count cannot answer it — hence ``--out DIR``, which
writes the actual images so they can be compared side by side. Still read-only:
it generates into a directory and posts nothing.
"""
import argparse
import base64
import binascii
import os
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
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

# Cloudflare Workers AI text-to-image candidates. Schnell is the one the review
# costs out; the others are here so one dispatch shows whether a better-looking
# model on the same account is reachable.
#
# (model, steps_param, steps). Both fields vary by model family and neither can
# be sniffed, so they are declared per candidate:
#   - Schnell takes `steps` (default 4, max 8). Billing is per step, so on that
#     model this is also the cost knob — see the review's pricing table.
#   - The SDXL pair takes `num_steps` (default and max 20). Sending `steps` to
#     them is silently ignored, and sending Schnell's 4 would misrepresent them
#     in a comparison that is about how the output LOOKS.
_CLOUDFLARE_CANDIDATES = [
    ("@cf/black-forest-labs/flux-1-schnell", "steps", 4),
    ("@cf/stabilityai/stable-diffusion-xl-base-1.0", "num_steps", 20),
    ("@cf/bytedance/stable-diffusion-xl-lightning", "num_steps", 20),
]
_CLOUDFLARE_TIMEOUT_SECONDS = 60.0


def _save(out_dir: Optional[str], name: str, data: bytes, ext: str = "png") -> str:
    """Write probe output for eyeballing. Returns a ' -> path' suffix, or ''.

    Never raises: a failed write must not turn a working model into an ERROR
    row, because the row is what the model-selection decision reads.
    """
    if not out_dir or not data:
        return ""
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in name)
    safe_ext = "".join(c for c in ext if c.isalnum()) or "png"
    try:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{safe}.{safe_ext}"
        target.write_bytes(data)
        return f"  -> {target}"
    except OSError as exc:
        return f"  (save failed: {type(exc).__name__}: {str(exc)[:120]})"


def _cloudflare_creds() -> Tuple[str, str]:
    account = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not account or not token:
        raise SystemExit(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must both be set "
            "for --provider cloudflare")
    return account, token


def _probe_cloudflare(
    client: httpx.Client,
    account_id: str,
    model: str,
    out_dir: Optional[str] = None,
    steps_param: str = "steps",
    steps: int = 4,
) -> str:
    """Probe one Workers AI text-to-image model. Same verdict vocabulary as the
    Gemini probes: OK only when real image bytes came back.

    **Two wire contracts, dispatched on the response content type.** Schnell
    answers with the standard Cloudflare envelope
    ``{"result": {"image": "<base64>"}, "success": bool, "errors": [...]}``;
    the SDXL models answer with a raw ``image/*`` stream and no envelope at all.
    Sniffing the content type rather than keying off the model name means a model
    that changes its shape, or a new candidate added to the list, is still read
    correctly instead of being reported as ERROR with no artifact.

    Ways a 200 is still not an image, none of which a status-code check catches:
    an ``image/*`` stream with an empty body, ``success: false`` with an errors
    array, a result with no ``image`` field, and a base64 payload that will not
    decode.
    """
    try:
        resp = client.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
            json={"prompt": _PROMPT, steps_param: steps},
            timeout=_CLOUDFLARE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return f"ERROR     {type(exc).__name__}: {str(exc)[:140]}"

    if resp.status_code != 200:
        return f"ERROR     HTTP {resp.status_code}: {resp.text[:140]}"

    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type.startswith("image/"):
        data = resp.content
        if not data:
            return "NO_IMAGE  (empty image stream)"
        ext = content_type.split("/", 1)[1] or "png"
        return f"OK        image={len(data)} bytes{_save(out_dir, model, data, ext)}"

    try:
        payload = resp.json()
    except ValueError as exc:
        return f"ERROR     non-JSON response: {str(exc)[:140]}"

    if not isinstance(payload, dict):
        return "ERROR     unexpected response shape (not an object)"
    if not payload.get("success", False):
        return f"ERROR     success=false: {str(payload.get('errors'))[:140]}"

    result = payload.get("result")
    encoded = result.get("image") if isinstance(result, dict) else None
    if not encoded or not isinstance(encoded, str):
        return "NO_IMAGE  (no image field in result)"

    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        return f"ERROR     base64 decode failed: {str(exc)[:140]}"
    if not data:
        return "NO_IMAGE  (image field decoded to zero bytes)"

    return f"OK        image={len(data)} bytes{_save(out_dir, model, data)}"


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


def _topic_slug(topic: str) -> str:
    """A short, stable, filesystem-safe stem for a Mentor topic."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in topic).split()][:5]
    return "-".join(w.lower() for w in words) or "topic"


def _bakeoff_prompts(count: int, crafted: bool = False) -> List[Tuple[str, str]]:
    """``(topic, prompt)`` for the first ``count`` Mentor topics.

    First N rather than a random sample, so two runs are comparable: the point is
    to vary the MODEL, and a different topic set each run reintroduces exactly the
    confound the bake-off exists to remove.

    ``crafted=False`` sends the static fallback: deterministic, so the model is the
    only variable. ``crafted=True`` runs the real production path through
    ``agents.build_visual_prompt`` — model-written prompt plus the appended
    constraints — which is non-deterministic and therefore useless for comparing
    models, but it is the ONLY way to see what the live pipeline actually sends.
    Use it to check a constraint change, not to pick a model.
    """
    topics = MENTOR_TOPICS[:count]
    if not crafted:
        return [(t, FALLBACK_VISUAL_PROMPT.format(topic=t) + VISUAL_PROMPT_CONSTRAINTS)
                for t in topics]

    import asyncio

    from src.agents import build_visual_prompt

    key = (os.environ.get("GEMINI_API_KEY") or "").strip()

    async def _all() -> List[Tuple[str, str]]:
        return [(t, await build_visual_prompt(key, t)) for t in topics]

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
    out_dir: Optional[str], models: List[str], count: int, crafted: bool = False
) -> None:
    """Same prompt, same call path, one image per (topic, model).

    Files are named ``<topic>__<model>.png`` — topic first on purpose, so a plain
    alphabetical listing puts each topic's variants next to each other and the
    comparison is a scroll rather than a lookup.

    This is the step that makes the model choice evidence rather than taste: the
    2026-10-19 probe compared one lighthouse, and one image cannot carry a
    decision that changes every picture in the feed.
    """
    prompts = _bakeoff_prompts(count, crafted)
    if not prompts:
        print("Bake-off: no Mentor topics available; nothing to compare.")
        return

    client = _client()
    print(f"\n=== bake-off: {len(prompts)} Mentor topics x {len(models)} models "
          f"({len(prompts) * len(models)} images, "
          f"{'crafted — production path' if crafted else 'static fallback prompt'}) ===")
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


def _run_cloudflare(out_dir: Optional[str]) -> None:
    account_id, token = _cloudflare_creds()
    print("\n=== Cloudflare Workers AI (each model at its own default steps) ===")
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as client:
        for model, steps_param, steps in _CLOUDFLARE_CANDIDATES:
            verdict = _probe_cloudflare(
                client, account_id, model, out_dir, steps_param, steps)
            print(f"  {model:<46} {steps_param}={steps:<3} {verdict}")
    print("\nThe decision this informs is how the images LOOK, not whether the call "
          "works — read the saved files, not the byte counts.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only image-model diagnostic. Probes which models return "
                    "real image bytes; --out writes them so they can be compared by eye.")
    parser.add_argument(
        "--provider", choices=("gemini", "cloudflare", "both"), default="gemini",
        help="which provider to probe (default: gemini, the bot's live path)")
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
        help="--bakeoff: send the real production prompt (model-written + appended "
             "constraints) instead of the static fallback. Non-deterministic, so use "
             "it to check a prompt change, not to compare models")
    args = parser.parse_args(argv)

    if args.bakeoff:
        models = [m.strip() for m in args.bakeoff_models.split(",") if m.strip()]
        if not models:
            parser.error("--bakeoff-models resolved to no models")
        _run_bakeoff(args.out, models, max(1, args.topics), args.crafted)
        if args.out:
            print(f"\nImages written to {args.out}")
        return 0

    if args.provider in ("gemini", "both"):
        _run_gemini(args.out)
    if args.provider in ("cloudflare", "both"):
        _run_cloudflare(args.out)
    if args.out:
        print(f"\nImages written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
