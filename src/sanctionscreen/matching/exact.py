"""Exact layer: normalised-string equality across indexed variants."""

from __future__ import annotations

from collections.abc import Sequence


def exact_score(query_variants: Sequence[str], name_variants: Sequence[str]) -> float:
    """100 when any normalised query variant equals any indexed name variant."""
    return 100.0 if set(query_variants) & set(name_variants) else 0.0
