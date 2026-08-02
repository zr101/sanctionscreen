import pytest

from sanctionscreen.config import Settings
from sanctionscreen.matching.embedding import create_embedder


def settings_with_vectors(path) -> Settings:
    settings = Settings()
    settings.embedding.vectors_path = path
    return settings


class TestGracefulOff:
    def test_disabled_by_config(self, tmp_path):
        settings = settings_with_vectors(tmp_path / "vec.db")
        settings.embedding.enabled = False
        assert create_embedder(settings) is None

    def test_no_precomputed_vectors(self, tmp_path):
        pytest.importorskip("sentence_transformers")
        # enabled and installed, but nothing precomputed -> None + warning
        assert create_embedder(settings_with_vectors(tmp_path / "vec.db")) is None


@pytest.mark.embeddings
class TestEmbeddingLayer:
    @pytest.fixture(scope="class")
    def embedded(self, tmp_path_factory):
        pytest.importorskip("sentence_transformers")
        from pathlib import Path

        from sanctionscreen.db import connect
        from sanctionscreen.ingestion.base import upsert_entities
        from sanctionscreen.ingestion.dfat import parse_dfat
        from sanctionscreen.matching.embedding import precompute_embeddings

        samples = Path(__file__).resolve().parent.parent / "data" / "cache" / "samples"
        base = tmp_path_factory.mktemp("embdb")
        conn = connect(base / "emb.db")
        vectors_path = base / "vectors.db"
        with conn:
            upsert_entities(conn, parse_dfat(samples / "dfat_sample.xlsx"))
        computed = precompute_embeddings(conn, Settings().embedding.model, vectors_path)
        assert computed > 0
        yield conn, vectors_path
        conn.close()

    def test_precompute_is_incremental(self, embedded):
        from sanctionscreen.matching.embedding import precompute_embeddings

        conn, vectors_path = embedded
        assert precompute_embeddings(conn, Settings().embedding.model, vectors_path) == 0

    def test_blob_roundtrip_normalised(self, embedded):
        import numpy as np

        from sanctionscreen.matching.embedding import open_vectors_db

        _conn, vectors_path = embedded
        vectors_conn = open_vectors_db(vectors_path)
        row = vectors_conn.execute("SELECT vector FROM name_embeddings LIMIT 1").fetchone()
        vectors_conn.close()
        vector = np.frombuffer(row["vector"], dtype=np.float32)
        assert vector.shape == (384,)
        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-3)

    def test_query_top_and_get(self, embedded):
        _conn, vectors_path = embedded
        embedder = create_embedder(settings_with_vectors(vectors_path))
        assert embedder is not None and len(embedder) > 0
        sims = embedder.query("abdul rahim testman")
        top = sims.top(limit=5, cutoff=0.3)
        assert top, "expected at least one similar name"
        best_id, best_cosine = top[0]
        assert best_cosine > 0.9  # near-identical string
        assert sims.get(best_id) == pytest.approx(best_cosine)
        assert sims.get(999999) is None

    def test_engine_uses_embedding_layer(self, embedded):
        from sanctionscreen.matching.engine import MatchingEngine

        conn, vectors_path = embedded
        settings = settings_with_vectors(vectors_path)
        embedder = create_embedder(settings)
        engine = MatchingEngine(conn, settings, embedder=embedder)
        results = engine.screen("Abdul Rahim Testman")
        assert results and results[0].layers.embedding > 90
        assert "embedding" in results[0].layers_fired

    def test_arabic_script_crosses_to_latin(self, embedded):
        # The multilingual model should relate the Arabic original-script row
        # to a Latin query even where layers 1-3 are weak.
        conn, vectors_path = embedded
        embedder = create_embedder(settings_with_vectors(vectors_path))
        sims = embedder.query("عبد الرحيم تستمان")
        top_ids = [name_id for name_id, _ in sims.top(limit=10, cutoff=0.2)]
        names = {
            r["id"]: r["name_original"] for r in conn.execute("SELECT id, name_original FROM names")
        }
        matched = [names[i] for i in top_ids if i in names]
        assert any("تستمان" in m or "Testman" in m for m in matched)

    def test_stale_vectors_removed(self, tmp_path):
        pytest.importorskip("sentence_transformers")
        from sanctionscreen.db import connect
        from sanctionscreen.ingestion.base import ParsedEntity, ParsedName, upsert_entities
        from sanctionscreen.matching.embedding import open_vectors_db, precompute_embeddings

        conn = connect(tmp_path / "stale.db")
        vectors_path = tmp_path / "vectors.db"

        def entity(names):
            return ParsedEntity(
                source_list="UN",
                reference_number="X.1",
                primary_name=names[0],
                entity_type="individual",
                raw_record={"v": names},
                names=[ParsedName(name_type="primary", original=names[0])]
                + [ParsedName(name_type="alias", original=n) for n in names[1:]],
            )

        with conn:
            upsert_entities(conn, [entity(["Alpha Tester", "Beta Tester"])])
        precompute_embeddings(conn, Settings().embedding.model, vectors_path)
        with conn:
            upsert_entities(conn, [entity(["Alpha Tester"])])  # alias removed
        precompute_embeddings(conn, Settings().embedding.model, vectors_path)

        vectors_conn = open_vectors_db(vectors_path)
        count = vectors_conn.execute("SELECT COUNT(*) c FROM name_embeddings").fetchone()["c"]
        names_count = conn.execute("SELECT COUNT(*) c FROM names").fetchone()["c"]
        vectors_conn.close()
        conn.close()
        assert count == names_count
