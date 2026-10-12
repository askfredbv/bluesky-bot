"""Non-Gemini text generation, kept behind one seam.

Why this module exists
----------------------
``GEMINI_MODEL_PRIORITY`` is four models deep, but all four are Gemini on one
key and one account. A revoked key, a quota decision or a surprise deprecation
takes the whole chain down at once, and the project's history is full of
Google moving things unilaterally (imagen-3 and imagen-4 shutdowns, 2.0-flash
dropped, 1.5-flash and gemma made unreachable). A single Anthropic model at the
BOTTOM of the chain turns a provider-wide outage from a missed run into a
posted one.

Deliberately narrow:

* **Text only.** Anthropic has no image generation, so ``generate_post_image``
  and ``_craft_visual_prompt`` stay Gemini-only. A run that falls through to
  Anthropic posts without a generated visual, which is already a supported
  outcome (``IMAGE_GENERATION_PROBABILITY`` is 0.85, and the image path is
  timeout-tolerant by design).
* **Last resort, not a second opinion.** It fires only when every Gemini model
  has failed. A 2026-10-10 blind voice trial over two rounds did not show a
  stable preference between this model and the Gemini line, so there is no
  case for promoting it and it is not positioned as one.
* **No bypass of the validators.** The string returned here goes through the
  same JSON parse, ``_validate_thread_shape``, ``validate_summary`` and
  ``_apply_voice_trim`` as any Gemini response. On failure the caller skips
  the broadcast. Missing a run still beats shipping garbage (AGENTS.md §2).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

ANTHROPIC_MODEL_PREFIX = "claude-"

# Thinking is pinned OFF. Anthropic bills thinking tokens as output and counts
# them against max_tokens, so leaving it on would spend the post's budget on
# reasoning the reader never sees - the same failure that produced the
# 2026-05-11 empty-output bug on gemini-2.5-pro.
#
# The way to turn it off DIFFERS BETWEEN MODELS OF THE SAME PROVIDER, which is
# why this is a lookup rather than one constant:
#   claude-haiku-5-5   accepts {"type": "disabled"}
#   claude-sonnet-5-5  REJECTS "disabled" with a 400; its off-switch is
#                      {"type": "between_tools"} (no other field, effort high
#                      or below)
# Both verified against the live API on 2026-10-12. Guessing here does not
# fail safe: the wrong value is a 400 on every call, and a value that is
# merely accepted-but-ignored silently spends the output budget on reasoning.
_THINKING_OFF: Dict[str, Dict[str, str]] = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
}

# Used for an Anthropic model not in the table above. "disabled" is the more
# common form, and a 400 on an unknown model is the correct outcome: it is a
# loud, immediate signal to add the model to the table rather than a silent
# budget leak. See `scripts/probe_fallback.py`, which exercises this.
_THINKING_OFF_DEFAULT: Dict[str, str] = {"type": "disabled"}


def thinking_off_for(model: str) -> Dict[str, str]:
    """Return the thinking-off config for `model`.

    Keyed per model, not per provider: see the table above for why.
    """
    return _THINKING_OFF.get(model.lower(), _THINKING_OFF_DEFAULT)


def is_anthropic_model(model: str) -> bool:
    """True when `model` should be routed to Anthropic rather than Gemini."""
    return model.lower().startswith(ANTHROPIC_MODEL_PREFIX)


def generate_anthropic(
    api_key: Optional[str],
    system_instr: str,
    task: str,
    model: str,
    max_output_tokens: int,
) -> str:
    """One synchronous Anthropic completion, shaped like ``_sync_generate``.

    Returns the response text, or ``""`` when the model produced no text
    block (a refusal, or a stop before any content). Callers treat ``""`` as
    the no-content sentinel exactly as they do for Gemini.

    Raises ``ValueError`` when no key is configured, so the model-chain loop
    logs it and moves on rather than the run dying.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(
            "Anthropic API key is missing or empty; cannot use an Anthropic model."
        )

    # Imported here rather than at module scope so that importing src.llm
    # never hard-depends on the SDK being present at collection time.
    import anthropic

    thinking: Any = thinking_off_for(model)
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_output_tokens,
        system=system_instr,
        thinking=thinking,
        messages=[{"role": "user", "content": task}],
    )
    # Discriminate on .type so the union narrows to TextBlock: the response can
    # also carry thinking, tool-use and tool-result blocks, none of which have
    # .text. No text block at all (a refusal) yields "".
    return "".join(block.text for block in response.content if block.type == "text")
