"""Ingestion CLI: python -m sanctionscreen.ingestion.cli --source all [--offline]

Each source is downloaded (with cached-copy fallback), parsed into the common
schema and idempotently upserted. Every run is recorded in ingestion_log for
auditability, including fallbacks and failures.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sanctionscreen.config import Settings, get_settings
from sanctionscreen.db import connect
from sanctionscreen.ingestion.base import log_ingestion, upsert_entities
from sanctionscreen.ingestion.dfat import parse_dfat
from sanctionscreen.ingestion.download import DownloadError, fetch
from sanctionscreen.ingestion.ofac import parse_ofac
from sanctionscreen.ingestion.un import parse_un

CACHE_DIR = Path("data/cache")


def ingest_source(source: str, settings: Settings, *, offline: bool = False) -> bool:
    """Ingest one source; returns True unless the source completely failed."""
    conn = connect(settings.database.path)
    started = time.perf_counter()
    honorifics = settings.normalisation.honorifics
    try:
        if source == "dfat":
            result = fetch(settings.sources.dfat.url, CACHE_DIR / "dfat.xlsx", offline=offline)
            entities = parse_dfat(result.path)
            from_cache, url, sha = result.from_cache, result.url, result.sha256
        elif source == "un":
            result = fetch(settings.sources.un.url, CACHE_DIR / "un.xml", offline=offline)
            entities = parse_un(result.path)
            from_cache, url, sha = result.from_cache, result.url, result.sha256
        else:
            ofac = settings.sources.ofac
            sdn = fetch(ofac.sdn_url, CACHE_DIR / "ofac_sdn.csv", offline=offline)
            alt = fetch(ofac.alt_url, CACHE_DIR / "ofac_alt.csv", offline=offline)
            add = fetch(ofac.add_url, CACHE_DIR / "ofac_add.csv", offline=offline)
            try:
                comments: Path | None = fetch(
                    ofac.comments_url, CACHE_DIR / "ofac_sdn_comments.csv", offline=offline
                ).path
            except DownloadError:
                comments = None  # remarks spill-over file is optional
            entities = parse_ofac(sdn.path, alt.path, add.path, comments)
            from_cache, url, sha = sdn.from_cache, sdn.url, sdn.sha256

        with conn:
            stats = upsert_entities(conn, entities, honorifics)
            log_ingestion(
                conn,
                source_list=source.upper(),
                status="fallback_cache" if from_cache else "success",
                source_url=url,
                file_sha256=sha,
                stats=stats,
                duration_ms=int((time.perf_counter() - started) * 1000),
                message="live download failed; used cached copy" if from_cache else None,
            )
        print(
            f"[{source.upper()}] {'CACHE-FALLBACK' if from_cache else 'ok'}: "
            f"{stats.inserted} inserted, {stats.updated} updated, "
            f"{stats.unchanged} unchanged, {stats.names_total} names"
        )
        return True
    except (DownloadError, OSError, ValueError) as exc:
        with conn:
            log_ingestion(
                conn,
                source_list=source.upper(),
                status="failed",
                duration_ms=int((time.perf_counter() - started) * 1000),
                message=str(exc),
            )
        print(f"[{source.upper()}] FAILED: {exc}", file=sys.stderr)
        return False
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest sanctions lists into SQLite.")
    parser.add_argument("--source", choices=["all", "dfat", "un", "ofac"], default="all")
    parser.add_argument(
        "--offline", action="store_true", help="use cached copies, no network access"
    )
    parser.add_argument("--no-embed", action="store_true", help="skip embedding precomputation")
    args = parser.parse_args(argv)

    settings = get_settings()
    sources = ["dfat", "un", "ofac"] if args.source == "all" else [args.source]
    results = [ingest_source(s, settings, offline=args.offline) for s in sources]

    if any(results) and not args.no_embed:
        from sanctionscreen.matching.embedding import embeddings_available, precompute_embeddings

        if settings.embedding.enabled and embeddings_available():
            conn = connect(settings.database.path)
            try:
                computed = precompute_embeddings(
                    conn, settings.embedding.model, settings.embedding.vectors_path
                )
                print(f"[EMBED] {computed} new vectors ({settings.embedding.model})")
            finally:
                conn.close()
        else:
            print("[EMBED] skipped (disabled or sentence-transformers not installed)")

    return 0 if any(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
