"""SQLite connection management and schema bootstrap.

One file holds the sanctions data, the ingestion log and the screening audit
trail so the whole state of the service is a single committable artifact.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS entities (
    id               INTEGER PRIMARY KEY,
    source_list      TEXT NOT NULL CHECK (source_list IN ('DFAT','UN','OFAC')),
    reference_number TEXT NOT NULL,
    primary_name     TEXT NOT NULL,
    entity_type      TEXT NOT NULL CHECK
        (entity_type IN ('individual','entity','vessel','aircraft')),
    nationality      TEXT,
    date_of_birth    TEXT,
    listed_date      TEXT,
    raw_record       TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_list, reference_number)
);

CREATE TABLE IF NOT EXISTS names (
    id              INTEGER PRIMARY KEY,
    entity_id       INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name_type       TEXT NOT NULL CHECK
        (name_type IN ('primary','alias','aka','fka','nka','original_script')),
    alias_quality   TEXT,
    name_original   TEXT NOT NULL,
    name_normalised TEXT NOT NULL,
    metaphone_key   TEXT,
    UNIQUE (entity_id, name_type, name_original)
);
CREATE INDEX IF NOT EXISTS idx_names_norm      ON names(name_normalised);
CREATE INDEX IF NOT EXISTS idx_names_metaphone ON names(metaphone_key);
CREATE INDEX IF NOT EXISTS idx_names_entity    ON names(entity_id);

CREATE TABLE IF NOT EXISTS name_embeddings (
    name_id INTEGER PRIMARY KEY REFERENCES names(id) ON DELETE CASCADE,
    model   TEXT NOT NULL,
    vector  BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    id                INTEGER PRIMARY KEY,
    run_at            TEXT NOT NULL DEFAULT (datetime('now')),
    source_list       TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('success','fallback_cache','failed')),
    source_url        TEXT,
    file_sha256       TEXT,
    entities_inserted INTEGER,
    entities_updated  INTEGER,
    names_total       INTEGER,
    duration_ms       INTEGER,
    message           TEXT
);

CREATE TABLE IF NOT EXISTS screenings (
    screening_id          TEXT PRIMARY KEY,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    query_name            TEXT NOT NULL,
    query_name_normalised TEXT NOT NULL,
    threshold             REAL NOT NULL,
    entity_type_filter    TEXT,
    max_results           INTEGER NOT NULL,
    match_count           INTEGER NOT NULL,
    top_score             REAL,
    latency_ms            REAL,
    results_json          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screenings_created ON screenings(created_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the SanctionScreen database."""
    db_path = Path(path)
    if db_path.parent and str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL)
    return conn
