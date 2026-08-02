"""App factory: builds the matching index at startup via the lifespan hook.

The SQLite-backed index and (optionally) the embedding matrix are loaded once
per process; screening requests then run entirely in memory. When embedding
vectors are missing but the model stack is installed, they are recomputed at
startup (config: embedding.precompute_on_startup) — the vectors file is
regenerable by design (DECISIONS.md D6).
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sanctionscreen import __version__
from sanctionscreen.api.routes import router
from sanctionscreen.config import Settings, get_settings
from sanctionscreen.db import connect
from sanctionscreen.matching.embedding import (
    create_embedder,
    embeddings_available,
    precompute_embeddings,
)
from sanctionscreen.matching.engine import MatchingEngine

logger = logging.getLogger(__name__)

DESCRIPTION = """
KYC name-screening microservice for the DFAT Consolidated List, the UN
Security Council Consolidated List and the US OFAC SDN list.

Matching is layered — exact, Double Metaphone phonetics, RapidFuzz fuzzy and
multilingual sentence embeddings — and every result carries per-layer
sub-scores so an analyst can see *why* a name matched. Every screening call
is persisted to an audit table under its screening_id.

**Disclaimer**: portfolio/demonstration software, not legal advice and not a
substitute for a commercial screening product.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(settings.database.path)
        try:
            embedder = create_embedder(settings)
            if (
                embedder is None
                and settings.embedding.enabled
                and settings.embedding.precompute_on_startup
                and embeddings_available()
            ):
                logger.info("no cached vectors found; precomputing at startup")
                precompute_embeddings(
                    conn, settings.embedding.model, settings.embedding.vectors_path
                )
                embedder = create_embedder(settings)

            app.state.engine = MatchingEngine(conn, settings, embedder=embedder)
            if embedder is not None:
                embedder.query("warm up")  # load the model before the first request
                app.state.embedding_status = "loaded"
            elif not settings.embedding.enabled:
                app.state.embedding_status = "disabled"
            else:
                app.state.embedding_status = "unavailable"
            logger.info(
                "index ready: %d names, embedding layer %s",
                len(app.state.engine.entries),
                app.state.embedding_status,
            )
        finally:
            conn.close()

        def audit_conn_factory() -> sqlite3.Connection:
            return connect(settings.database.path)

        app.state.audit_conn_factory = audit_conn_factory
        yield

    app = FastAPI(
        title="SanctionScreen",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
