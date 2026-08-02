"""Matching engine: index build, layered candidate generation, ranking.

Layers run cheapest-first and their candidates are unioned:
  1. exact       — dict lookup on normalised variants
  2. phonetic    — dict lookup on order-invariant Double Metaphone keys
  3. fuzzy       — RapidFuzz token_sort scan (C++, single-digit ms at ~54k)
  4. embedding   — one matrix-vector product over precomputed vectors
Every candidate then gets all four sub-scores for explainability, and results
are grouped per entity (best-scoring name wins).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sanctionscreen.config import Settings
from sanctionscreen.matching.exact import exact_score
from sanctionscreen.matching.fuzzy import fuzzy_candidates, fuzzy_score
from sanctionscreen.matching.phonetic import metaphone_key, phonetic_similarity
from sanctionscreen.matching.scoring import LayerScores, combine
from sanctionscreen.normalise import normalise_variants


class QuerySimilarities(Protocol):
    """Embedding similarities of one query against all indexed names."""

    def top(self, limit: int, cutoff: float) -> list[tuple[int, float]]:
        """Best (name_id, cosine) pairs above cutoff."""
        ...

    def get(self, name_id: int) -> float | None:
        """Cosine for a specific name, if embedded."""
        ...


class Embedder(Protocol):
    def query(self, normalised: str) -> QuerySimilarities: ...


@dataclass
class NameEntry:
    name_id: int
    entity_id: int
    name_type: str
    alias_quality: str | None
    original: str
    normalised: str
    variants: list[str]


@dataclass
class EntityMeta:
    entity_id: int
    source_list: str
    reference_number: str
    primary_name: str
    entity_type: str
    nationality: str | None
    date_of_birth: str | None
    listed_date: str | None


@dataclass
class MatchResult:
    entity: EntityMeta
    matched_name: str
    matched_name_normalised: str
    matched_name_type: str
    alias_quality: str | None
    score: float
    layers: LayerScores
    layers_fired: list[str]


class MatchingEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        embedder: Embedder | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self._honorifics = settings.normalisation.honorifics
        self.entries: list[NameEntry] = []
        self.entities: dict[int, EntityMeta] = {}
        self._exact_index: dict[str, list[int]] = {}
        self._phonetic_index: dict[str, list[int]] = {}
        self._normalised: list[str] = []
        self._by_name_id: dict[int, int] = {}
        self._build(conn)

    def _build(self, conn: sqlite3.Connection) -> None:
        for row in conn.execute(
            "SELECT id, source_list, reference_number, primary_name, entity_type,"
            " nationality, date_of_birth, listed_date FROM entities"
        ):
            self.entities[row["id"]] = EntityMeta(
                entity_id=row["id"],
                source_list=row["source_list"],
                reference_number=row["reference_number"],
                primary_name=row["primary_name"],
                entity_type=row["entity_type"],
                nationality=row["nationality"],
                date_of_birth=row["date_of_birth"],
                listed_date=row["listed_date"],
            )
        for row in conn.execute(
            "SELECT id, entity_id, name_type, alias_quality, name_original, name_normalised"
            " FROM names"
        ):
            variants = normalise_variants(row["name_original"], self._honorifics)
            if not variants:
                continue
            entry = NameEntry(
                name_id=row["id"],
                entity_id=row["entity_id"],
                name_type=row["name_type"],
                alias_quality=row["alias_quality"],
                original=row["name_original"],
                normalised=row["name_normalised"],
                variants=variants,
            )
            index = len(self.entries)
            self.entries.append(entry)
            self._normalised.append(entry.normalised)
            self._by_name_id[entry.name_id] = index
            for variant in variants:
                self._exact_index.setdefault(variant, []).append(index)
                self._phonetic_index.setdefault(metaphone_key(variant), []).append(index)

    def screen(
        self,
        name: str,
        *,
        threshold: float | None = None,
        max_results: int | None = None,
        entity_type: str | None = None,
    ) -> list[MatchResult]:
        scoring = self.settings.scoring
        threshold = scoring.default_threshold if threshold is None else threshold
        max_results = scoring.default_max_results if max_results is None else max_results

        query_variants = normalise_variants(name, self._honorifics)
        if not query_variants:
            return []
        query = query_variants[0]

        candidates: set[int] = set()
        for variant in query_variants:
            candidates.update(self._exact_index.get(variant, ()))
            candidates.update(self._phonetic_index.get(metaphone_key(variant), ()))
        for index, _score in fuzzy_candidates(
            query,
            self._normalised,
            score_cutoff=scoring.fuzzy_cutoff,
            limit=scoring.fuzzy_candidate_limit,
        ):
            candidates.add(index)

        sims: QuerySimilarities | None = None
        if self.embedder is not None:
            sims = self.embedder.query(query)
            for name_id, _cosine in sims.top(
                scoring.embedding_candidate_limit, scoring.embedding_cosine_cutoff
            ):
                entry_index = self._by_name_id.get(name_id)
                if entry_index is not None:
                    candidates.add(entry_index)

        best_per_entity: dict[int, MatchResult] = {}
        for index in candidates:
            entry = self.entries[index]
            meta = self.entities.get(entry.entity_id)
            if meta is None or (entity_type and meta.entity_type != entity_type):
                continue
            layers = self._score_entry(query, query_variants, entry, sims)
            combined = combine(layers, scoring)
            if combined.score < threshold:
                continue
            current = best_per_entity.get(entry.entity_id)
            if current is None or combined.score > current.score:
                best_per_entity[entry.entity_id] = MatchResult(
                    entity=meta,
                    matched_name=entry.original,
                    matched_name_normalised=entry.normalised,
                    matched_name_type=entry.name_type,
                    alias_quality=entry.alias_quality,
                    score=combined.score,
                    layers=combined.layers,
                    layers_fired=combined.layers_fired,
                )

        ranked = sorted(best_per_entity.values(), key=lambda r: r.score, reverse=True)
        return ranked[:max_results]

    def _score_entry(
        self,
        query: str,
        query_variants: Sequence[str],
        entry: NameEntry,
        sims: QuerySimilarities | None,
    ) -> LayerScores:
        scoring = self.settings.scoring
        phonetic = max(
            phonetic_similarity(qv, nv) for qv in query_variants for nv in entry.variants
        )
        embedding = 0.0
        if sims is not None:
            cosine = sims.get(entry.name_id)
            if cosine is not None:
                embedding = 100.0 * max(0.0, cosine)
        return LayerScores(
            exact=exact_score(query_variants, entry.variants),
            phonetic=round(phonetic, 2),
            fuzzy=round(
                fuzzy_score(
                    query,
                    entry.normalised,
                    token_sort_weight=scoring.fuzzy_token_sort_weight,
                    partial_weight=scoring.fuzzy_partial_weight,
                ),
                2,
            ),
            embedding=round(embedding, 2),
        )
