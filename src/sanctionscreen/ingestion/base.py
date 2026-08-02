"""Common ingestion model and idempotent upsert into the shared schema.

Every source parser yields ParsedEntity records; this module owns writing
them. Upserts key on (source_list, reference_number). Name rows are only
rewritten when the name set actually changed, which preserves name ids and
therefore any precomputed embeddings for unchanged names.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sanctionscreen.matching.phonetic import metaphone_key
from sanctionscreen.normalise import DEFAULT_HONORIFICS, normalise_name

VALID_NAME_TYPES = {"primary", "alias", "aka", "fka", "nka", "original_script"}
VALID_ENTITY_TYPES = {"individual", "entity", "vessel", "aircraft"}


@dataclass
class ParsedName:
    name_type: str
    original: str
    quality: str | None = None


@dataclass
class ParsedEntity:
    source_list: str
    reference_number: str
    primary_name: str
    entity_type: str
    raw_record: dict
    nationality: str | None = None
    date_of_birth: str | None = None
    listed_date: str | None = None
    names: list[ParsedName] = field(default_factory=list)


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    names_total: int = 0


def upsert_entities(
    conn: sqlite3.Connection,
    entities: Iterable[ParsedEntity],
    honorifics: Sequence[str] = DEFAULT_HONORIFICS,
) -> UpsertStats:
    """Idempotently write parsed entities and their names. Caller commits."""
    stats = UpsertStats()
    for entity in entities:
        if entity.entity_type not in VALID_ENTITY_TYPES:
            raise ValueError(f"invalid entity_type {entity.entity_type!r}")
        raw_json = json.dumps(entity.raw_record, ensure_ascii=False, sort_keys=True)
        row = conn.execute(
            "SELECT id, raw_record FROM entities WHERE source_list = ? AND reference_number = ?",
            (entity.source_list, entity.reference_number),
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO entities (source_list, reference_number, primary_name,"
                " entity_type, nationality, date_of_birth, listed_date, raw_record)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity.source_list,
                    entity.reference_number,
                    entity.primary_name,
                    entity.entity_type,
                    entity.nationality,
                    entity.date_of_birth,
                    entity.listed_date,
                    raw_json,
                ),
            )
            entity_id = cur.lastrowid
            assert entity_id is not None
            stats.inserted += 1
        else:
            entity_id = row["id"]
            if row["raw_record"] == raw_json:
                stats.unchanged += 1
            else:
                conn.execute(
                    "UPDATE entities SET primary_name = ?, entity_type = ?, nationality = ?,"
                    " date_of_birth = ?, listed_date = ?, raw_record = ?,"
                    " last_updated_at = datetime('now') WHERE id = ?",
                    (
                        entity.primary_name,
                        entity.entity_type,
                        entity.nationality,
                        entity.date_of_birth,
                        entity.listed_date,
                        raw_json,
                        entity_id,
                    ),
                )
                stats.updated += 1
        stats.names_total += _sync_names(conn, entity_id, entity.names)
    return stats


def _sync_names(conn: sqlite3.Connection, entity_id: int, names: list[ParsedName]) -> int:
    """Rewrite an entity's name rows only if the name set changed."""
    deduped: dict[tuple[str, str], ParsedName] = {}
    for name in names:
        if name.name_type not in VALID_NAME_TYPES:
            raise ValueError(f"invalid name_type {name.name_type!r}")
        if name.original.strip():
            deduped.setdefault((name.name_type, name.original), name)

    existing = {
        (r["name_type"], r["name_original"]): r["alias_quality"]
        for r in conn.execute(
            "SELECT name_type, name_original, alias_quality FROM names WHERE entity_id = ?",
            (entity_id,),
        )
    }
    desired = {key: n.quality for key, n in deduped.items()}
    if existing != desired:
        conn.execute("DELETE FROM names WHERE entity_id = ?", (entity_id,))
        conn.executemany(
            "INSERT INTO names (entity_id, name_type, alias_quality, name_original,"
            " name_normalised, metaphone_key) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    entity_id,
                    n.name_type,
                    n.quality,
                    n.original,
                    norm,
                    metaphone_key(norm),
                )
                for n in deduped.values()
                if (norm := normalise_name(n.original))
            ],
        )
    return len(deduped)


def log_ingestion(
    conn: sqlite3.Connection,
    *,
    source_list: str,
    status: str,
    source_url: str | None = None,
    file_sha256: str | None = None,
    stats: UpsertStats | None = None,
    duration_ms: int | None = None,
    message: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ingestion_log (source_list, status, source_url, file_sha256,"
        " entities_inserted, entities_updated, names_total, duration_ms, message)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_list,
            status,
            source_url,
            file_sha256,
            stats.inserted if stats else None,
            stats.updated if stats else None,
            stats.names_total if stats else None,
            duration_ms,
            message,
        ),
    )
