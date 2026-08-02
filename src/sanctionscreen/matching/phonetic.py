"""Double Metaphone phonetic layer.

The index key is the SORTED sequence of per-token Double Metaphone primary
codes, which makes lookups invariant to name-order swaps ("Ali Hassan" and
"Hassan Ali" share a key). Non-Latin tokens (Arabic, Cyrillic) produce no
Metaphone code and fall back to the raw token so original-script names stay
indexable.
"""

from __future__ import annotations

from collections import Counter

from metaphone import doublemetaphone


def token_codes(normalised: str) -> list[str]:
    """Per-token Double Metaphone primary codes (raw token as fallback)."""
    codes: list[str] = []
    for token in normalised.split():
        primary, _secondary = doublemetaphone(token)
        codes.append(primary or token)
    return codes


def metaphone_key(normalised: str) -> str:
    """Order-invariant phonetic index key for a normalised name."""
    return " ".join(sorted(token_codes(normalised)))


def phonetic_similarity(query_normalised: str, name_normalised: str) -> float:
    """Sub-score 0-100: Dice coefficient over token Metaphone code multisets.

    Identical keys score 100; names sharing most phonetic tokens (e.g. a
    dropped middle name) score proportionally.
    """
    q_codes = Counter(token_codes(query_normalised))
    n_codes = Counter(token_codes(name_normalised))
    if not q_codes or not n_codes:
        return 0.0
    overlap = sum((q_codes & n_codes).values())
    total = sum(q_codes.values()) + sum(n_codes.values())
    return 200.0 * overlap / total
