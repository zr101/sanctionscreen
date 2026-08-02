"""Name normalisation shared by ingestion, matching and evaluation.

Pipeline: NFKD decompose -> drop combining marks -> casefold -> map
punctuation to spaces -> collapse whitespace. Honorifics are stripped only as
leading tokens, and callers index both the stripped and unstripped variants
when they differ (DECISIONS.md D9).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

DEFAULT_HONORIFICS: tuple[str, ...] = (
    "mr", "mrs", "ms", "miss", "dr", "prof", "sir", "lord", "lady",
    "haji", "hajji", "hadji", "al hajj", "al haj", "alhaj", "el hajj",
    "mullah", "mulla", "maulavi", "maulawi", "mawlawi", "moulavi",
    "sheikh", "shaykh", "sheik", "shaikh", "sayyid", "sayed", "syed",
    "imam", "ustad", "qari", "hafiz",
    "general", "colonel", "major", "captain", "lieutenant", "brigadier",
    "eng", "engineer",
)

# Anything that is not a letter, digit or whitespace becomes a space. \w with
# re.UNICODE covers non-Latin scripts (Arabic, Cyrillic, CJK).
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Return the canonical normalised form of a name (no honorific stripping)."""
    decomposed = unicodedata.normalize("NFKD", name)
    no_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    folded = no_marks.casefold()
    spaced = _PUNCT_RE.sub(" ", folded)
    return _WS_RE.sub(" ", spaced).strip()


def strip_leading_honorifics(
    normalised: str, honorifics: Sequence[str] = DEFAULT_HONORIFICS
) -> str:
    """Strip honorific tokens from the front of an already-normalised name.

    Multi-word honorifics ("al hajj") are matched longest-first. Stripping
    stops at the first non-honorific token, and never consumes the whole name.
    """
    phrases = sorted((h.split() for h in honorifics), key=len, reverse=True)
    tokens = normalised.split()
    while tokens:
        for phrase in phrases:
            n = len(phrase)
            if len(tokens) > n and tokens[:n] == phrase:
                tokens = tokens[n:]
                break
        else:
            break
    return " ".join(tokens) if tokens else normalised


def normalise_variants(
    name: str, honorifics: Sequence[str] = DEFAULT_HONORIFICS
) -> list[str]:
    """Normalised forms to index for a name: canonical first, then the
    honorific-stripped variant when it differs."""
    base = normalise_name(name)
    if not base:
        return []
    stripped = strip_leading_honorifics(base, honorifics)
    return [base] if stripped == base else [base, stripped]


def unique_normalised(names: Iterable[str]) -> list[str]:
    """Normalise a batch of names, deduplicated, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        norm = normalise_name(name)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out
