"""Screening audit trail.

Persisting every screening call — query, parameters, outcome and the full
result payload — is a regulatory expectation for AML/CTF reporting entities
(AUSTRAC expects screening decisions to be reconstructable). Each row is
keyed by the screening_id returned to the caller.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from sanctionscreen.matching.engine import MatchResult
from sanctionscreen.normalise import normalise_name


def record_screening(
    conn: sqlite3.Connection,
    *,
    screening_id: str,
    query_name: str,
    threshold: float,
    entity_type: str | None,
    max_results: int,
    results: list[MatchResult],
    latency_ms: float,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO screenings (screening_id, query_name, query_name_normalised,"
            " threshold, entity_type_filter, max_results, match_count, top_score,"
            " latency_ms, results_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                screening_id,
                query_name,
                normalise_name(query_name),
                threshold,
                entity_type,
                max_results,
                len(results),
                results[0].score if results else None,
                round(latency_ms, 2),
                json.dumps([asdict(r) for r in results], ensure_ascii=False),
            ),
        )
