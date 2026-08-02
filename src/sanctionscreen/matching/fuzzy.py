"""Fuzzy layer: RapidFuzz over normalised names.

token_sort_ratio handles name-order swaps ("Ali Hassan" vs "Hassan Ali");
partial_ratio catches containment but over-scores substrings, so it is
down-weighted in the sub-score (DECISIONS.md D5). The full token_sort scan
over ~50-70k names is C++-backed and doubles as the candidate generator.
"""

from __future__ import annotations

from collections.abc import Sequence

from rapidfuzz import fuzz, process


def fuzzy_candidates(
    query_normalised: str,
    names_normalised: Sequence[str],
    *,
    score_cutoff: float,
    limit: int,
) -> list[tuple[int, float]]:
    """(index, token_sort_ratio) pairs for the best-matching names."""
    return [
        (int(index), float(score))
        for _name, score, index in process.extract(
            query_normalised,
            names_normalised,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=score_cutoff,
            limit=limit,
        )
    ]


def fuzzy_score(
    query_normalised: str,
    name_normalised: str,
    *,
    token_sort_weight: float,
    partial_weight: float,
) -> float:
    """Weighted blend of token_sort_ratio and partial_ratio, 0-100."""
    return token_sort_weight * fuzz.token_sort_ratio(
        query_normalised, name_normalised
    ) + partial_weight * fuzz.partial_ratio(query_normalised, name_normalised)
