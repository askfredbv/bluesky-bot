"""One-shot model-discovery diagnostic — what can this key actually reach today?

Run this to decide whether a newer/better primary model is available before
touching GEMINI_MODEL_PRIORITY. It makes NO changes and posts NOTHING — it
queries the Gemini API for the live model list and prints a report.

    python -m scripts.discover_models

Reads GEMINI_API_KEY from the environment (or .env via python-dotenv).

Why this exists: model availability is account- and time-specific, and
Claude's training cutoff can't see recent releases. The honest way to pick
a primary model is to list what's actually reachable now, then test the
best candidate by reading one run's feed output — not by assuming. See the
"Primary-model upgrade evaluation" item in docs/BACKLOG.md §3 for the full
finding and the two switch-time gotchas (thinking-budget + SDK ceiling).
"""

from __future__ import annotations

import os
import re

# The bot's current chain — so the report can mark what's in use vs available.
try:
    from src.config import GEMINI_MODEL_PRIORITY
except Exception:
    GEMINI_MODEL_PRIORITY = []


def _supports_generate(model) -> bool:
    """True if the model can do text generation (generateContent).

    The google-genai SDK exposes supported methods as `supported_actions`
    on 1.x; older shapes used `supported_generation_methods`. Check both,
    and default to True if neither is present (better to over-list than
    silently hide a usable model).
    """
    for attr in ("supported_actions", "supported_generation_methods"):
        actions = getattr(model, attr, None)
        if actions:
            return any("generatecontent" in str(a).lower() for a in actions)
    return True


def _short(model) -> str:
    """Bare model id without the 'models/' prefix."""
    return getattr(model, "name", "?").split("/")[-1]


def _is_text_model(name: str) -> bool:
    """Heuristic: a Gemini/Gemma text model, not embedding/image/tts/aqa."""
    n = name.lower()
    if not (n.startswith("gemini") or n.startswith("gemma")):
        return False
    return not any(x in n for x in ("embedding", "embed", "aqa", "imagen", "-tts", "image-generation"))


def _generation(name: str):
    """Return (major, minor) version tuple, or (0, 0) if none found."""
    m = re.search(r"(\d+)\.(\d+)", name.lower())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _generation_key(name: str):
    """Sort key: newest generation first, then by name."""
    major, minor = _generation(name)
    return (-major, -minor, name.lower())


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # pragma: no cover
        pass

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        print("[error] GEMINI_API_KEY not set (env or .env). Cannot query the API.")
        return 1

    try:
        from google import genai
    except ImportError as e:
        print(f"[error] google-genai not importable: {e}")
        return 1

    try:
        import importlib.metadata
        sdk_ver = importlib.metadata.version("google-genai")
    except Exception:
        sdk_ver = "unknown"

    try:
        client = genai.Client(api_key=api_key)
        models = list(client.models.list())
    except Exception as e:
        print(f"[error] model list query failed: {type(e).__name__}: {e}")
        return 1

    text_models = []
    for m in models:
        name = _short(m)
        if _is_text_model(name) and _supports_generate(m):
            text_models.append(m)
    text_models.sort(key=lambda m: _generation_key(_short(m)))

    available_names = {_short(m) for m in models}

    print("=" * 72)
    print(f"Gemini model discovery   (google-genai SDK {sdk_ver})")
    print("=" * 72)

    print("\n-- Current GEMINI_MODEL_PRIORITY chain --")
    if not GEMINI_MODEL_PRIORITY:
        print("  (could not import config)")
    for i, name in enumerate(GEMINI_MODEL_PRIORITY, 1):
        mark = "OK " if name in available_names else "GONE"
        primary = "  <- PRIMARY" if i == 1 else ""
        print(f"  [{mark}] {name}{primary}")

    print(f"\n-- Text-generation models reachable by this key ({len(text_models)}) --")
    print("   (newest generation first; output-token limit shown where exposed)")
    for m in text_models:
        name = _short(m)
        out_limit = getattr(m, "output_token_limit", None)
        disp = getattr(m, "display_name", "") or ""
        in_chain = " *in chain*" if name in GEMINI_MODEL_PRIORITY else ""
        limit_str = f"out={out_limit}" if out_limit else ""
        print(f"  {name:42s} {limit_str:12s} {disp}{in_chain}")

    print("\n-- Upgrade candidates (generation newer than 2.5, Pro/Flash tier, NOT Flash-Lite) --")
    # A candidate is newer than gen 2.5 AND a Pro/Flash tier (Flash-Lite is a
    # LOWER tier than our current 2.5-pro, so it is never an upgrade for us).
    pro_flash = [
        _short(m) for m in text_models
        if _generation(_short(m)) > (2, 5)
        and ("pro" in _short(m) or "flash" in _short(m))
        and "flash-lite" not in _short(m)
    ]
    if pro_flash:
        for c in pro_flash:
            print(f"  CANDIDATE: {c}")
        print("\n  Next step: put ONE candidate first in GEMINI_MODEL_PRIORITY, run once,")
        print("  and READ the feed output vs 2.5-pro. Also pin its thinking budget in")
        print("  agents.py _thinking_budget_for() — a 3.x model currently falls through")
        print("  to None and may hit the 2026-05-11 empty-output bug.")
    else:
        print("  None found above 2.5-pro tier. Staying on gemini-2.5-pro is the")
        print("  correct call — no quality upgrade is available to this key today.")

    print("\n" + "=" * 72)
    print("Reminder: this is a read-only report. Nothing changed, nothing posted.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
