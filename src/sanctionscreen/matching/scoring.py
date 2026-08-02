"""Combination of per-layer sub-scores into one 0-100 match score.

Strategy "max" (default, DECISIONS.md D4):
    score = max(w_exact*exact, w_fuzzy*fuzzy, w_phonetic*phonetic,
                w_embedding*embedding)
plus a corroboration bonus when several independent non-exact layers agree.
An exact hit is always 100; a fuzzy-only hit caps at 97, phonetic-only at 90,
embedding-only at 85 — so the threshold keeps an intuitive meaning.

Strategy "weighted_sum" is available for experimentation: the same weights,
renormalised over the layers that fired.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sanctionscreen.config import ScoringConfig


@dataclass
class LayerScores:
    exact: float = 0.0
    phonetic: float = 0.0
    fuzzy: float = 0.0
    embedding: float = 0.0

    def fired(self) -> list[str]:
        return [
            name
            for name in ("exact", "phonetic", "fuzzy", "embedding")
            if getattr(self, name) > 0.0
        ]


@dataclass
class CombinedScore:
    score: float
    layers: LayerScores
    layers_fired: list[str] = field(default_factory=list)


def combine(layers: LayerScores, config: ScoringConfig) -> CombinedScore:
    weighted = {
        "exact": config.weight_exact * layers.exact,
        "fuzzy": config.weight_fuzzy * layers.fuzzy,
        "phonetic": config.weight_phonetic * layers.phonetic,
        "embedding": config.weight_embedding * layers.embedding,
    }
    if config.strategy == "weighted_sum":
        weights = {
            "exact": config.weight_exact,
            "fuzzy": config.weight_fuzzy,
            "phonetic": config.weight_phonetic,
            "embedding": config.weight_embedding,
        }
        fired = layers.fired()
        total_weight = sum(weights[name] for name in fired) or 1.0
        score = sum(weighted[name] for name in fired) / total_weight
    else:
        score = max(weighted.values())

    if layers.exact == 0.0:
        corroborating = sum(
            1
            for sub in (layers.phonetic, layers.fuzzy, layers.embedding)
            if sub >= config.corroboration_floor
        )
        if corroborating >= config.corroboration_min_layers:
            score = min(score + config.corroboration_bonus, config.score_cap)

    return CombinedScore(score=round(score, 2), layers=layers, layers_fired=layers.fired())
