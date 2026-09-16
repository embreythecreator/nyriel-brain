"""Generate short follow-up suggestions from the turn that just finished.

Modelled on :mod:`agent.title_generator` — same ``call_llm`` auxiliary path,
same lazy config reads, same swallow-everything failure posture. It differs in
one way that matters: titling is fire-and-forget on a daemon thread, but these
suggestions ride the *terminal chunk* of the response, so this call happens
inline and its timeout is charged to the tail of the turn. Keep it short.

Contract with the Face: ``suggested_follow_ups`` is an additive key on the
existing terminal payload, NOT a new SSE event. A new ``event:`` name would
miss ``ACTIVITY_EVENT_NAMES`` on the Face side and get parsed as a completion
chunk. Always return a list — never None — so the key's type is stable.
"""

import logging
from typing import List, Optional

from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
# A suggestion is a chip in a transcript row, not a paragraph. Past this the
# Face would ellipsize anyway, so spend the tokens on fewer, tighter lines.
MAX_SUGGESTION_CHARS = 80

_FOLLOW_UP_PROMPT = (
    "Given the exchange below, write up to three short follow-up questions the user "
    "might plausibly ask next. Each must be under 80 characters, phrased as the user "
    "would type it, and must not repeat what was already answered. "
    "Write them in the same language the user is writing in. "
    "Return ONLY the questions, one per line. No numbering, no bullets, no preamble."
)


def _follow_ups_config() -> dict:
    """Return the ``auxiliary.suggested_follow_ups`` block, or ``{}``.

    Lazy imports mirror ``title_generator._auto_title_enabled``: this module is
    imported from gateway code paths where a module-level ``nyriel_cli``
    import risks circularity, and the read-only loader avoids triggering a
    config-migration write on a hot request path.
    """
    try:
        from nyriel_cli.config import load_config_readonly

        config = load_config_readonly() or {}
        return (config.get("auxiliary") or {}).get("suggested_follow_ups") or {}
    except Exception:
        logger.debug("Failed to read auxiliary.suggested_follow_ups", exc_info=True)
        return {}


def _follow_ups_enabled() -> bool:
    """Return whether follow-up suggestion generation is enabled."""
    try:
        from utils import is_truthy_value

        return is_truthy_value(_follow_ups_config().get("enabled"), default=True)
    except Exception:
        logger.debug("Failed to read suggested_follow_ups.enabled", exc_info=True)
        return True


def _follow_ups_timeout() -> float:
    """Return the configured timeout, defaulting to 4s.

    Charged to the tail of every turn, so the default sits far below the 30s
    used for titling — which runs on a background thread and costs nobody wait.
    """
    try:
        return float(_follow_ups_config().get("timeout") or 4)
    except Exception:
        return 4.0


def _clean_line(line: str) -> str:
    """Strip the list scaffolding a model adds despite being told not to."""
    text = line.strip()
    # Leading bullet glyphs.
    while text[:1] in {"-", "*", "•", "–", "—"}:
        text = text[1:].strip()
    # "1." / "1)" numbering.
    if len(text) > 2 and text[0].isdigit():
        head, sep, tail = text.partition(".")
        if not sep:
            head, sep, tail = text.partition(")")
        if sep and head.strip().isdigit():
            text = tail.strip()
    return text.strip().strip("\"'").strip()


def generate_follow_ups(
    user_message: str,
    assistant_response: str,
    timeout: Optional[float] = None,
    main_runtime: dict = None,
) -> List[str]:
    """Return up to three short follow-up questions, or ``[]``.

    Never raises and never returns None. Every failure mode — disabled by
    config, empty input, auxiliary provider down, model returning prose —
    collapses to ``[]``, which the Face renders as no follow-up block. A
    suggestion row is a nicety; it must never be able to fail a turn.
    """
    if not user_message or not assistant_response:
        return []

    if not _follow_ups_enabled():
        logger.debug("Follow-ups skipped: auxiliary.suggested_follow_ups.enabled=false")
        return []

    # Same 500-char clamp as titling. Head of each side: the opening is what
    # identifies the task, and the whole point is to guess what comes next.
    messages = [
        {"role": "system", "content": _FOLLOW_UP_PROMPT},
        {
            "role": "user",
            "content": f"User: {user_message[:500]}\n\nAssistant: {assistant_response[:500]}",
        },
    ]

    try:
        response = call_llm(
            task="suggested_follow_ups",
            messages=messages,
            max_tokens=300,
            temperature=0.4,
            timeout=timeout if timeout is not None else _follow_ups_timeout(),
            main_runtime=main_runtime,
        )
        content = response.choices[0].message.content or ""
        # Think-enabled models emit <think> blocks even for trivial prompts;
        # without this the raw XML becomes the first "suggestion". Reuses the
        # canonical scrubber so unterminated and mixed-case tags are handled.
        from agent.agent_runtime_helpers import strip_think_blocks

        text = strip_think_blocks(None, content) or ""

        suggestions: List[str] = []
        for raw_line in text.splitlines():
            cleaned = _clean_line(raw_line)
            # An over-long line is a model ignoring the brief and answering the
            # question instead. Drop it rather than truncate it mid-sentence.
            if not cleaned or len(cleaned) > MAX_SUGGESTION_CHARS:
                continue
            if cleaned in suggestions:
                continue
            suggestions.append(cleaned)
            if len(suggestions) >= MAX_SUGGESTIONS:
                break
        return suggestions
    except Exception as e:
        # Debug, not warning: unlike a missing session title, a missing
        # suggestion row leaves no visible gap for an operator to chase.
        logger.debug("Follow-up generation failed: %s", e, exc_info=True)
        return []


if __name__ == "__main__":
    # ponytail: parsing self-check, no framework. The LLM call is the part that
    # can't be asserted offline; the line cleaner is the part that breaks.
    assert _clean_line("- What about errors?") == "What about errors?"
    assert _clean_line("1. How do I retry?") == "How do I retry?"
    assert _clean_line("2) How do I retry?") == "How do I retry?"
    assert _clean_line('  "Quoted question?"  ') == "Quoted question?"
    assert _clean_line("• Bulleted") == "Bulleted"
    assert _clean_line("") == ""
    # A digit-led sentence that is NOT numbering must survive intact.
    assert _clean_line("3D rendering, how?") == "3D rendering, how?"
    assert generate_follow_ups("", "anything") == []
    assert generate_follow_ups("anything", "") == []
    print("follow_up_generator self-check OK")
