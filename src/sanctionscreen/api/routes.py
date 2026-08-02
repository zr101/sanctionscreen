"""API routes: /screen, /health, /lists."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from sanctionscreen.api import audit
from sanctionscreen.api.schemas import (
    HealthResponse,
    ListInfo,
    Match,
    ScreenRequest,
    ScreenResponse,
)
from sanctionscreen.matching.engine import MatchingEngine, MatchResult
from sanctionscreen.normalise import normalise_name

router = APIRouter()


def _to_match(result: MatchResult) -> Match:
    payload = asdict(result)
    payload["layers"] = asdict(result.layers)
    payload["entity"] = asdict(result.entity)
    return Match.model_validate(payload)


@router.post(
    "/screen",
    response_model=ScreenResponse,
    summary="Screen a name against all loaded sanctions lists",
    description="Runs the layered matching pipeline (exact, phonetic, fuzzy and, "
    "when enabled, multilingual embeddings) over the DFAT, UN and OFAC lists and "
    "returns ranked, explainable matches. Every call is persisted to the audit "
    "table under the returned screening_id — a regulatory expectation for "
    "reporting entities under the AML/CTF Act.",
)
def screen(request: Request, body: ScreenRequest) -> ScreenResponse:
    if not normalise_name(body.name):
        raise HTTPException(
            status_code=422,
            detail="name contains no matchable characters after normalisation",
        )
    engine: MatchingEngine = request.app.state.engine
    scoring = engine.settings.scoring
    threshold = scoring.default_threshold if body.threshold is None else body.threshold
    max_results = scoring.default_max_results if body.max_results is None else body.max_results

    started = time.perf_counter()
    results = engine.screen(
        body.name,
        threshold=threshold,
        max_results=max_results,
        entity_type=body.entity_type,
    )
    latency_ms = (time.perf_counter() - started) * 1000

    screening_id = str(uuid.uuid4())
    audit_conn = request.app.state.audit_conn_factory()
    try:
        audit.record_screening(
            audit_conn,
            screening_id=screening_id,
            query_name=body.name,
            threshold=threshold,
            entity_type=body.entity_type,
            max_results=max_results,
            results=results,
            latency_ms=latency_ms,
        )
    finally:
        audit_conn.close()

    return ScreenResponse(
        screening_id=screening_id,
        query_name=body.name,
        threshold=threshold,
        match_count=len(results),
        matches=[_to_match(r) for r in results],
    )


def _list_infos(conn: sqlite3.Connection) -> list[ListInfo]:
    infos = []
    for row in conn.execute(
        "SELECT e.source_list, COUNT(DISTINCT e.id) AS entity_count,"
        " COUNT(n.id) AS name_count"
        " FROM entities e LEFT JOIN names n ON n.entity_id = e.id"
        " GROUP BY e.source_list ORDER BY e.source_list"
    ):
        last = conn.execute(
            "SELECT run_at, status FROM ingestion_log WHERE source_list = ?"
            " AND status IN ('success', 'fallback_cache')"
            " ORDER BY id DESC LIMIT 1",
            (row["source_list"],),
        ).fetchone()
        infos.append(
            ListInfo(
                source_list=row["source_list"],
                entity_count=row["entity_count"],
                name_count=row["name_count"],
                last_refreshed=last["run_at"] if last else None,
                last_status=last["status"] if last else None,
            )
        )
    return infos


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health, list versions and layer status",
)
def health(request: Request) -> HealthResponse:
    conn = request.app.state.audit_conn_factory()
    try:
        infos = _list_infos(conn)
    finally:
        conn.close()
    return HealthResponse(
        status="ok",
        embedding_layer=request.app.state.embedding_status,
        lists=infos,
    )


@router.get(
    "/lists",
    response_model=list[ListInfo],
    summary="Metadata for every loaded sanctions list",
    description="Entity/name counts and the most recent refresh per source list, "
    "sourced from the ingestion_log audit table.",
)
def lists(request: Request) -> list[ListInfo]:
    conn = request.app.state.audit_conn_factory()
    try:
        return _list_infos(conn)
    finally:
        conn.close()
