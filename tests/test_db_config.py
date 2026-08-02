import sqlite3

from sanctionscreen.config import Settings
from sanctionscreen.db import connect


class TestDb:
    def test_schema_bootstrap(self, tmp_path):
        conn = connect(tmp_path / "test.db")
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"entities", "names", "name_embeddings", "ingestion_log", "screenings"} <= tables
        conn.close()

    def test_connect_is_idempotent(self, tmp_path):
        path = tmp_path / "test.db"
        connect(path).close()
        connect(path).close()

    def test_upsert_key_enforced(self, tmp_path):
        conn = connect(tmp_path / "test.db")
        conn.execute(
            "INSERT INTO entities (source_list, reference_number, primary_name,"
            " entity_type, raw_record) VALUES ('DFAT', '1', 'X', 'individual', '{}')"
        )
        try:
            conn.execute(
                "INSERT INTO entities (source_list, reference_number, primary_name,"
                " entity_type, raw_record) VALUES ('DFAT', '1', 'Y', 'individual', '{}')"
            )
            raise AssertionError("expected IntegrityError")
        except sqlite3.IntegrityError:
            pass
        conn.close()

    def test_cascade_delete_names(self, tmp_path):
        conn = connect(tmp_path / "test.db")
        cur = conn.execute(
            "INSERT INTO entities (source_list, reference_number, primary_name,"
            " entity_type, raw_record) VALUES ('UN', 'QDi.1', 'X', 'individual', '{}')"
        )
        conn.execute(
            "INSERT INTO names (entity_id, name_type, name_original, name_normalised)"
            " VALUES (?, 'primary', 'X', 'x')",
            (cur.lastrowid,),
        )
        conn.execute("DELETE FROM entities WHERE id = ?", (cur.lastrowid,))
        assert conn.execute("SELECT COUNT(*) c FROM names").fetchone()["c"] == 0
        conn.close()


class TestConfig:
    def test_defaults_load(self):
        s = Settings()
        assert s.scoring.default_threshold == 75.0
        assert s.scoring.default_max_results == 10
        assert s.embedding.enabled is True
        assert "haji" in s.normalisation.honorifics
        assert s.sources.ofac.sdn_url.startswith("https://sanctionslistservice")

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SANCTIONSCREEN_SCORING__DEFAULT_THRESHOLD", "82.5")
        monkeypatch.setenv("SANCTIONSCREEN_EMBEDDING__ENABLED", "false")
        s = Settings()
        assert s.scoring.default_threshold == 82.5
        assert s.embedding.enabled is False
