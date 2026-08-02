"""Request/response schemas for the screening API.

The descriptions below surface directly in the OpenAPI docs (/docs), which
double as the service's user-facing reference.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal["individual", "entity", "vessel", "aircraft"]


class ScreenRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=500,
        description="Customer name to screen, as captured (any script; "
        "normalisation, transliteration and fuzzy matching are handled server-side).",
        examples=["Usama bin Ladin"],
    )
    threshold: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Minimum combined score (0-100) for a match to be returned. "
        "Defaults to the configured service threshold (75). Lower it for higher "
        "recall in enhanced-due-diligence contexts; raise it to cut review noise.",
    )
    max_results: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of matched entities to return (default 10).",
    )
    entity_type: EntityType | None = Field(
        default=None,
        description="Restrict matching to one listed type, e.g. 'individual' when "
        "screening a natural person, to suppress vessel/entity noise.",
    )


class LayerScoresModel(BaseModel):
    exact: float = Field(description="100 when the normalised names are identical, else 0.")
    phonetic: float = Field(
        description="Double Metaphone similarity (0-100), order-invariant across tokens."
    )
    fuzzy: float = Field(
        description="RapidFuzz blend: 0.7*token_sort_ratio + 0.3*partial_ratio (0-100)."
    )
    embedding: float = Field(
        description="Multilingual sentence-embedding cosine similarity scaled to 0-100; "
        "0 when the embedding layer is disabled."
    )


class MatchedEntity(BaseModel):
    source_list: str = Field(description="Originating list: DFAT, UN or OFAC.")
    reference_number: str = Field(
        description="The list's own reference for the entity (DFAT reference, "
        "UN permanent reference e.g. QDi.361, or OFAC ent_num)."
    )
    primary_name: str
    entity_type: EntityType
    nationality: str | None
    date_of_birth: str | None = Field(
        description="As published by the source list; may be approximate or a range."
    )
    listed_date: str | None = Field(
        description="Listing (DFAT: control) date as published; None where the "
        "source provides no structured date (OFAC legacy files)."
    )


class Match(BaseModel):
    matched_name: str = Field(description="The exact list name (or alias) that matched.")
    matched_name_normalised: str
    matched_name_type: str = Field(description="primary, alias, aka, fka, nka or original_script.")
    alias_quality: str | None = Field(
        description="Source-assigned alias strength (DFAT Strong/Weak, UN Good/Low)."
    )
    score: float = Field(description="Combined 0-100 score across all layers.")
    layers: LayerScoresModel = Field(
        description="Per-layer sub-scores — the explainability trail for the match."
    )
    layers_fired: list[str] = Field(
        description="Which matching layers contributed a non-zero sub-score."
    )
    entity: MatchedEntity


class ScreenResponse(BaseModel):
    screening_id: str = Field(
        description="UUID of this screening. Every call is persisted to the audit "
        "table under this id — quote it in case notes and regulator responses."
    )
    query_name: str
    threshold: float
    match_count: int
    matches: list[Match] = Field(description="Ranked best-first.")


class ListInfo(BaseModel):
    source_list: str
    entity_count: int
    name_count: int = Field(description="Searchable name rows, including aliases.")
    last_refreshed: str | None = Field(
        description="UTC timestamp of the most recent successful ingestion run."
    )
    last_status: str | None = Field(
        description="Outcome of the most recent ingestion run: success, fallback_cache or failed."
    )


class HealthResponse(BaseModel):
    status: str
    embedding_layer: str = Field(
        description="'loaded' when semantic matching is active; otherwise "
        "'disabled' (by config) or 'unavailable' (dependencies or vectors missing)."
    )
    lists: list[ListInfo]
