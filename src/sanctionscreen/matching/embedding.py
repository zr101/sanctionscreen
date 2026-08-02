"""Embedding layer: multilingual sentence-transformer similarity.

Vectors for every list name are precomputed at ingestion time and cached in a
separate SQLite file (data/embeddings.db) as float32 blobs, so query time is
one model encode plus one matrix-vector product. The vectors file is
deliberately NOT committed to git — it is regenerated from the committed
sanctions.db on first run (DECISIONS.md D6).

The layer is gracefully optional (DECISIONS.md D2): if sentence-transformers
is not installed, the model is unavailable, or embedding.enabled is false,
create_embedder returns None and the engine runs on layers 1-3.
"""

from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from sanctionscreen.config import Settings

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_DTYPE = np.float32

_VECTORS_DDL = """
CREATE TABLE IF NOT EXISTS name_embeddings (
    name_id INTEGER PRIMARY KEY,
    model   TEXT NOT NULL,
    vector  BLOB NOT NULL
);
"""


def open_vectors_db(path: str | Path) -> sqlite3.Connection:
    vectors_path = Path(path)
    vectors_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(vectors_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_VECTORS_DDL)
    return conn


@lru_cache(maxsize=2)
def _load_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    logger.info("loading embedding model %s", model_name)
    return SentenceTransformer(model_name)


def _encode(model_name: str, texts: list[str]) -> NDArray[np.float32]:
    model = _load_model(model_name)
    vectors = model.encode(
        texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    )
    return np.asarray(vectors, dtype=_DTYPE)


def precompute_embeddings(
    conn: sqlite3.Connection,
    model_name: str,
    vectors_path: str | Path,
    *,
    batch_size: int = 512,
) -> int:
    """Embed every name that has no cached vector for model_name.

    Incremental: unchanged names keep their vectors (name ids are stable
    across re-ingestion). Vectors for other models or for deleted names are
    dropped. Returns the number of vectors computed.
    """
    names = {
        row["id"]: row["name_normalised"]
        for row in conn.execute("SELECT id, name_normalised FROM names")
    }
    vectors_conn = open_vectors_db(vectors_path)
    try:
        with vectors_conn:
            vectors_conn.execute("DELETE FROM name_embeddings WHERE model != ?", (model_name,))
            existing = {
                row["name_id"]
                for row in vectors_conn.execute("SELECT name_id FROM name_embeddings")
            }
            stale = existing - names.keys()
            if stale:
                vectors_conn.executemany(
                    "DELETE FROM name_embeddings WHERE name_id = ?", [(i,) for i in stale]
                )
            missing = sorted(names.keys() - existing)
            if not missing:
                return 0
            logger.info("computing %d name embeddings", len(missing))
            computed = 0
            for start in range(0, len(missing), batch_size):
                batch_ids = missing[start : start + batch_size]
                vectors = _encode(model_name, [names[i] for i in batch_ids])
                vectors_conn.executemany(
                    "INSERT OR REPLACE INTO name_embeddings (name_id, model, vector)"
                    " VALUES (?, ?, ?)",
                    [
                        (name_id, model_name, vector.tobytes())
                        for name_id, vector in zip(batch_ids, vectors, strict=True)
                    ],
                )
                computed += len(batch_ids)
            return computed
    finally:
        vectors_conn.close()


class QuerySims:
    """Similarities of one query against the whole precomputed matrix."""

    def __init__(
        self,
        sims: NDArray[np.float32],
        name_ids: NDArray[np.int64],
        row_of: dict[int, int],
    ) -> None:
        self._sims = sims
        self._name_ids = name_ids
        self._row_of = row_of

    def top(self, limit: int, cutoff: float) -> list[tuple[int, float]]:
        limit = min(limit, len(self._sims))
        if limit == 0:
            return []
        indices = np.argpartition(-self._sims, limit - 1)[:limit]
        indices = indices[np.argsort(-self._sims[indices])]
        return [
            (int(self._name_ids[i]), float(self._sims[i]))
            for i in indices
            if self._sims[i] >= cutoff
        ]

    def get(self, name_id: int) -> float | None:
        row = self._row_of.get(name_id)
        return float(self._sims[row]) if row is not None else None


class EmbeddingIndex:
    """In-memory (N, dim) matrix of precomputed name vectors."""

    def __init__(
        self,
        model_name: str,
        matrix: NDArray[np.float32],
        name_ids: NDArray[np.int64],
    ) -> None:
        self.model_name = model_name
        self._matrix = matrix
        self._name_ids = name_ids
        self._row_of = {int(nid): row for row, nid in enumerate(name_ids)}

    def __len__(self) -> int:
        return len(self._name_ids)

    @classmethod
    def load(cls, vectors_path: str | Path, model_name: str) -> EmbeddingIndex | None:
        if not Path(vectors_path).is_file():
            return None
        vectors_conn = open_vectors_db(vectors_path)
        try:
            rows = vectors_conn.execute(
                "SELECT name_id, vector FROM name_embeddings WHERE model = ? ORDER BY name_id",
                (model_name,),
            ).fetchall()
        finally:
            vectors_conn.close()
        if not rows:
            return None
        matrix = np.vstack([np.frombuffer(r["vector"], dtype=_DTYPE) for r in rows])
        name_ids = np.array([r["name_id"] for r in rows], dtype=np.int64)
        return cls(model_name, matrix, name_ids)

    def query(self, normalised: str) -> QuerySims:
        vector = _encode(self.model_name, [normalised])[0]
        sims = self._matrix @ vector
        return QuerySims(sims, self._name_ids, self._row_of)


def embeddings_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def create_embedder(settings: Settings) -> EmbeddingIndex | None:
    """Build the embedding layer, or None (with a warning) when unavailable."""
    if not settings.embedding.enabled:
        logger.warning("embedding layer disabled by configuration")
        return None
    if not embeddings_available():
        logger.warning(
            "embedding layer unavailable: sentence-transformers not installed"
            " (install the [embeddings] extra); running on layers 1-3 only"
        )
        return None
    index = EmbeddingIndex.load(settings.embedding.vectors_path, settings.embedding.model)
    if index is None:
        logger.warning(
            "no precomputed embeddings for model %s; run ingestion (or"
            " precompute_embeddings) first",
            settings.embedding.model,
        )
    return index
