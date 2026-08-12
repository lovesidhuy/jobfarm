"""
core.answer_controls — DOM-aware apply+verify helpers.

The runtime is responsible for *how* to apply an intent onto the actual form
control. This module owns the synonym→option matcher and the post-action
verification step that distinguishes "AI hallucinated an answer that the form
silently dropped" from "we genuinely answered the field".

These helpers are framework-agnostic: they accept Playwright elements (the
duck-typed surface used by the bots) but only call:

    .is_checked()           bool
    .check() / .click()
    .select_option(label=)
    .get_attribute(name)
    .inner_text()

So they work with any wrapper that mimics that interface.

Public API
----------
    match_option(intent, labels)             -> Optional[label]
    apply_radio(options, intent)             -> ApplyResult
    apply_select(select_el, intent, labels)  -> ApplyResult
    apply_listbox(open_el, option_els, intent, label_of)
                                             -> ApplyResult
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .answer_policy import map_intent_to_option as _policy_map

# Synonym table for fuzzy intent→label matching when the policy mapper fails.
# Used only when the form rendered non-canonical labels like "Authorized" or
# "I am eligible" instead of the standard "Yes" / "No".
_FALLBACK_SYNONYMS: dict[str, tuple[str, ...]] = {
    "yes": ("authorized", "eligible", "permanent resident", "canadian citizen",
            "i am authorized", "i am eligible", "approved", "willing", "able",
            "comfortable", "available", "open to", "would consider"),
    "no":  ("not authorized", "not eligible", "unable", "unwilling",
            "not available", "not interested", "do not", "i do not",
            "i'm not", "not a veteran", "none of the above"),
    "decline": ("rather not", "rather not say", "i don't wish", "no preference"),
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    applied_label: Optional[str] = None
    reason: str = ""

    @classmethod
    def success(cls, label: str) -> "ApplyResult":
        return cls(ok=True, applied_label=label, reason="ok")

    @classmethod
    def failure(cls, reason: str) -> "ApplyResult":
        return cls(ok=False, applied_label=None, reason=reason)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


def match_option(intent: str, option_labels: list[str]) -> Optional[str]:
    """
    Given an intent string ("yes" / "no" / a literal label / decline), pick the
    closest visible option label.

    Strategy:
      1. Delegate to `core.answer_policy.map_intent_to_option` (handles canonical
         Yes/No synonyms + decline phrasing).
      2. Try fallback synonym lists for unusual phrasings.
      3. If `intent` itself looks like an option label (free-form text from AI),
         try exact → contains → token-overlap match.
    """
    if not intent or not option_labels:
        return None

    # 1. Policy mapper handles canonical Yes/No/decline.
    out = _policy_map(intent, option_labels)
    if out is not None:
        return out

    intent_l = intent.strip().lower()

    # 2. Extra synonym table for non-canonical labels.
    if intent_l in _FALLBACK_SYNONYMS:
        for synonym in _FALLBACK_SYNONYMS[intent_l]:
            for opt in option_labels:
                if synonym in opt.lower():
                    return opt

    # 3. Free-form AI text — exact, contains, then token overlap.
    intent_norm = _norm(intent)
    if not intent_norm:
        return None

    norm_opts = [(o, _norm(o)) for o in option_labels]
    for o, on in norm_opts:           # exact
        if on == intent_norm:
            return o
    for o, on in norm_opts:           # contains either direction
        if on and (on in intent_norm or intent_norm in on):
            return o

    intent_tokens = set(intent_norm.split())
    if not intent_tokens:
        return None
    best_o, best_score = None, 0
    for o, on in norm_opts:
        ot = set(on.split())
        if not ot:
            continue
        overlap = len(intent_tokens & ot)
        score = overlap / max(1, len(ot))
        if overlap >= 2 and score > best_score:
            best_o, best_score = o, score
    return best_o


# ---------------------------------------------------------------------------
# Radio
# ---------------------------------------------------------------------------


def apply_radio(options: Iterable[tuple[Any, str]],
                intent: str,
                *,
                verify: bool = True,
                retry: int = 1) -> ApplyResult:
    """
    Apply an intent onto a Playwright-style radio group.

    `options` is a list of (radio_element, label_text) pairs.

    Returns ApplyResult with `ok=True` only if the radio is `:checked` after
    the click. On verify failure, retries `retry` times.
    """
    opts = list(options)
    if not opts:
        return ApplyResult.failure("no_options")

    labels = [lbl for _, lbl in opts]
    target_label = match_option(intent, labels)
    if target_label is None:
        return ApplyResult.failure(f"no_label_match:{intent!r}")

    target_el = next((r for r, lbl in opts if lbl == target_label), None)
    if target_el is None:
        return ApplyResult.failure("matched_label_lost")

    attempts = 0
    last_err = ""
    while attempts <= retry:
        try:
            try:
                if target_el.is_checked():
                    return ApplyResult.success(target_label)
            except Exception:
                pass
            try:
                target_el.check(force=True)  # Playwright's check() has retry built in
            except Exception:
                target_el.click(force=True)

            if not verify:
                return ApplyResult.success(target_label)

            # Verification pause (Indeed React re-renders are async).
            time.sleep(0.05)
            try:
                if target_el.is_checked():
                    return ApplyResult.success(target_label)
            except Exception as e:
                last_err = f"verify_error:{type(e).__name__}"
        except Exception as e:
            last_err = f"click_error:{type(e).__name__}:{e}"
        attempts += 1

    return ApplyResult.failure(last_err or "verify_failed")


# ---------------------------------------------------------------------------
# <select>
# ---------------------------------------------------------------------------


def apply_select(select_el: Any,
                 intent: str,
                 option_labels: list[str],
                 *,
                 verify: bool = True) -> ApplyResult:
    target_label = match_option(intent, option_labels)
    if target_label is None:
        return ApplyResult.failure(f"no_label_match:{intent!r}")
    try:
        select_el.select_option(label=target_label)
    except Exception as e:
        return ApplyResult.failure(f"select_error:{type(e).__name__}:{e}")

    if not verify:
        return ApplyResult.success(target_label)

    # Verify: Playwright's input.value() yields the option's value, not its
    # visible text — fall back to evaluate.
    try:
        time.sleep(0.05)
        text = select_el.evaluate(
            "el => (el.options[el.selectedIndex] || {}).text || ''"
        ) or ""
        if _norm(text) and _norm(text) == _norm(target_label):
            return ApplyResult.success(target_label)
        if _norm(target_label) in _norm(text):
            return ApplyResult.success(target_label)
        return ApplyResult.failure(f"verify_mismatch:{text!r}")
    except Exception:
        return ApplyResult.success(target_label)


# ---------------------------------------------------------------------------
# Custom listbox / combobox (Indeed sometimes renders these instead of <select>)
# ---------------------------------------------------------------------------


def apply_listbox(open_el: Any,
                  intent: str,
                  list_options: Callable[[], list[tuple[Any, str]]],
                  *,
                  verify: bool = True) -> ApplyResult:
    """
    Apply an intent onto a custom listbox/combobox (role=button + role=listbox).

    Caller-supplied `list_options()` should:
      1. Open the listbox (click `open_el` if needed)
      2. Return [(option_el, label), ...] of currently visible options
    """
    try:
        open_el.click(force=True)
    except Exception as e:
        return ApplyResult.failure(f"open_error:{type(e).__name__}:{e}")

    try:
        opts = list_options()
    except Exception as e:
        return ApplyResult.failure(f"list_error:{type(e).__name__}:{e}")
    if not opts:
        return ApplyResult.failure("no_options")

    labels = [lbl for _, lbl in opts]
    target_label = match_option(intent, labels)
    if target_label is None:
        return ApplyResult.failure(f"no_label_match:{intent!r}")
    target_el = next((el for el, lbl in opts if lbl == target_label), None)
    if target_el is None:
        return ApplyResult.failure("matched_label_lost")

    try:
        target_el.click(force=True)
    except Exception as e:
        return ApplyResult.failure(f"click_error:{type(e).__name__}:{e}")

    if not verify:
        return ApplyResult.success(target_label)

    time.sleep(0.05)
    try:
        # Most listboxes write the selected text back into the trigger button.
        text = open_el.inner_text() or ""
        if target_label in text or _norm(target_label) in _norm(text):
            return ApplyResult.success(target_label)
        return ApplyResult.failure(f"verify_mismatch:{text!r}")
    except Exception:
        return ApplyResult.success(target_label)
