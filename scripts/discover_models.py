"""One-shot model-discovery diagnostic — what can this key actually reach today?

Run this to decide whether a newer/better primary model is available before
touching GEMINI_MODEL_PRIORITY. It makes NO changes and posts NOTHING.

    python -m scripts.discover_models

Reads GEMINI_API_KEY from the environment (or .env via python-dotenv).

Two modes, auto-selected:
  - LIST mode: queries the full model list (one API call). Preferred.
  - PROBE mode: if ListModels is blocked for the key (a common API-key
    restriction — 403 API_KEY_SERVICE_BLOCKED — that still allows
    generateContent), falls back to probing a curated candidate set with
    a tiny generate call each. Slower, costs a few trivial calls, but
    works when listing is denied and tests the exact capability we care
    about (can this key generate with this model).

Why this exists: model availability is account- and time-specific, and
Claude's training cutoff can't see recent releases. The honest way to pick
a primary model is to find what's actually reachable now, then test the
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

# Candidate model ids to probe when ListModels is blocked. Mix of known-good
# controls (to prove the probe works) and the real current lineup.
# Edit freely — probing an absent name just reports "absent", no harm.
#
# Lineup confirmed from Google's Gemini 3.5 announcement (2026-05-19) +
# the deepmind.google benchmark table: 3.5 Flash is GA via the Gemini API
# and beats 3.1 Pro on most agentic/coding/multimodal benchmarks; 3.5 Pro
# was "rolling out next month" (so possibly live by 2026-06). API id strings
# weren't published verbatim, so a few naming variants are probed per model.
PROBE_CANDIDATES = [
    # Controls — should be present (the bot uses these today)
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    # 3.5 — the real upgrade targets (Flash is GA, Pro imminent/maybe live)
    "gemini-3.5-flash",
    "gemini-3.5-flash-preview",
    "gemini-3.5-pro",
    "gemini-3.5-pro-preview",
    # Older 3.x — present per the benchmark table; fallbacks if 3.5 absent
    "gemini-3.1-pro",
    "gemini-3-flash",
]


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
    return not any(x in n for x in ("embedding", "embed", "aqa", "imagen", "-tts", "image-generation", "-image"))


def _generation(name: str):
    """Return (major, minor) version tuple, or (0, 0) if none found."""
    m = re.search(r"(\d+)\.(\d+)", name.lower())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _generation_key(name: str):
    """Sort key: newest generation first, then by name."""
    major, minor = _generation(name)
    return (-major, -minor, name.lower())


def _probe_one(client, name: str) -> str:
    """Probe one model id with a tiny generate call. Classify the outcome.

    Returns: "available" | "absent" | "blocked" | "error: <Type>".
    We only care whether the call is accepted (model resolves + key may
    call it), not the output quality — an empty response still counts as
    available.
    """
    try:
        client.models.generate_content(
            model=name, contents="ping", config={"max_output_tokens": 16}
        )
        return "available"
    except Exception as e:
        msg = str(e).lower()
        if "not_found" in msg or "404" in msg or "is not found" in msg:
            return "absent"
        if "permission_denied" in msg or "403" in msg or "blocked" in msg:
            return "blocked"
        if "429" in msg or "resource_exhausted" in msg or "quota" in msg:
            return "rate-limited (try again later)"
        return f"error: {type(e).__name__}"


def _print_chain_status(available_names: set) -> None:
    print("\n-- Current GEMINI_MODEL_PRIORITY chain --")
    if not GEMINI_MODEL_PRIORITY:
        print("  (could not import config)")
    for i, name in enumerate(GEMINI_MODEL_PRIORITY, 1):
        mark = "OK  " if name in available_names else "????"
        primary = "  <- PRIMARY" if i == 1 else ""
        print(f"  [{mark}] {name}{primary}")


def _print_upgrade_candidates(available_names: set) -> None:
    print("\n-- Upgrade candidates (generation newer than 2.5, Pro/Flash tier, NOT Flash-Lite) --")
    # Flash-Lite is a LOWER tier than our current 2.5-pro, so it is never an
    # upgrade for us, regardless of generation.
    pro_flash = sorted(
        (n for n in available_names
         if _generation(n) > (2, 5)
         and ("pro" in n or "flash" in n)
         and "flash-lite" not in n),
        key=_generation_key,
    )
    if pro_flash:
        for c in pro_flash:
            print(f"  CANDIDATE: {c}")
        print("\n  Next step: put ONE candidate first in GEMINI_MODEL_PRIORITY, run once,")
        print("  and READ the feed output vs 2.5-pro. Also pin its thinking budget in")
        print("  agents.py _thinking_budget_for() — a 3.x model currently falls through")
        print("  to None and may hit the 2026-05-11 empty-output bug.")
    else:
        print("  None reachable above 2.5-pro tier. Staying on gemini-2.5-pro is the")
        print("  correct call — no quality upgrade is available to this key today.")


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

    client = genai.Client(api_key=api_key)

    print("=" * 72)
    print(f"Gemini model discovery   (google-genai SDK {sdk_ver})")
    print("=" * 72)

    # --- Try LIST mode first ---
    list_ok = False
    available_names: set = set()
    try:
        models = list(client.models.list())
        list_ok = True
    except Exception as e:
        msg = str(e)
        blocked = "blocked" in msg.lower() or "permission_denied" in msg.lower()
        print("\n[LIST mode unavailable] " + (
            "ListModels is blocked for this key (a common API-key restriction "
            "that still allows generateContent). Falling back to PROBE mode."
            if blocked else f"models.list() failed: {type(e).__name__}. Falling back to PROBE mode."
        ))

    if list_ok:
        text_models = sorted(
            (m for m in models if _is_text_model(_short(m)) and _supports_generate(m)),
            key=lambda m: _generation_key(_short(m)),
        )
        available_names = {_short(m) for m in models}
        _print_chain_status(available_names)
        print(f"\n-- Text-generation models reachable by this key ({len(text_models)}) --")
        print("   (newest generation first; output-token limit shown where exposed)")
        for m in text_models:
            name = _short(m)
            out_limit = getattr(m, "output_token_limit", None)
            disp = getattr(m, "display_name", "") or ""
            in_chain = " *in chain*" if name in GEMINI_MODEL_PRIORITY else ""
            limit_str = f"out={out_limit}" if out_limit else ""
            print(f"  {name:42s} {limit_str:12s} {disp}{in_chain}")
    else:
        # --- PROBE mode ---
        print(f"\n-- Probing {len(PROBE_CANDIDATES)} candidate models (tiny generate call each) --")
        results = {}
        for name in PROBE_CANDIDATES:
            status = _probe_one(client, name)
            results[name] = status
            if status == "available":
                available_names.add(name)
            print(f"  {name:30s} -> {status}")
        _print_chain_status(available_names)

    _print_upgrade_candidates(available_names)

    print("\n" + "=" * 72)
    print("Reminder: this is a read-only report. Nothing changed, nothing posted.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
