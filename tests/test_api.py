import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sanctionscreen.api.main import create_app
from sanctionscreen.config import Settings
from sanctionscreen.db import connect
from sanctionscreen.ingestion.base import log_ingestion, upsert_entities
from sanctionscreen.ingestion.dfat import parse_dfat
from sanctionscreen.ingestion.ofac import parse_ofac
from sanctionscreen.ingestion.un import parse_un

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "cache" / "samples"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    base = tmp_path_factory.mktemp("apidb")
    db_path = base / "api.db"
    conn = connect(db_path)
    entities = (
        parse_dfat(SAMPLES / "dfat_sample.xlsx")
        + parse_un(SAMPLES / "un_sample.xml")
        + parse_ofac(
            SAMPLES / "ofac_sdn_sample.csv",
            SAMPLES / "ofac_alt_sample.csv",
            SAMPLES / "ofac_add_sample.csv",
        )
    )
    with conn:
        stats = upsert_entities(conn, entities)
        log_ingestion(conn, source_list="DFAT", status="success", stats=stats)
    conn.close()

    settings = Settings()
    settings.database.path = db_path
    settings.embedding.enabled = False
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, db_path


class TestScreen:
    def test_exact_match(self, api):
        client, _ = api
        response = client.post("/screen", json={"name": "Testov, Ivan Petrovich"})
        assert response.status_code == 200
        body = response.json()
        assert body["screening_id"]
        assert body["match_count"] >= 1
        top = body["matches"][0]
        assert top["score"] == 100.0
        assert top["entity"]["source_list"] == "OFAC"
        assert top["entity"]["reference_number"] == "12345"
        assert "exact" in top["layers_fired"]
        assert set(top["layers"]) == {"exact", "phonetic", "fuzzy", "embedding"}

    def test_no_match(self, api):
        client, _ = api
        response = client.post("/screen", json={"name": "Zebadiah Quirkwhistle"})
        assert response.status_code == 200
        assert response.json()["match_count"] == 0

    def test_threshold_and_max_results_respected(self, api):
        client, _ = api
        loose = client.post("/screen", json={"name": "Ivan Testov", "threshold": 40}).json()
        tight = client.post("/screen", json={"name": "Ivan Testov", "threshold": 99}).json()
        assert loose["match_count"] >= tight["match_count"]
        capped = client.post(
            "/screen", json={"name": "Ivan Testov", "threshold": 0, "max_results": 1}
        ).json()
        assert capped["match_count"] == 1

    def test_entity_type_filter(self, api):
        client, _ = api
        body = client.post(
            "/screen",
            json={"name": "Example Star", "threshold": 60, "entity_type": "individual"},
        ).json()
        assert all(m["entity"]["entity_type"] == "individual" for m in body["matches"])

    def test_validation_errors(self, api):
        client, _ = api
        assert client.post("/screen", json={}).status_code == 422
        assert client.post("/screen", json={"name": ""}).status_code == 422
        assert client.post("/screen", json={"name": "x", "threshold": 101}).status_code == 422
        assert client.post("/screen", json={"name": "x", "max_results": 0}).status_code == 422
        assert (
            client.post("/screen", json={"name": "x", "entity_type": "robot"}).status_code == 422
        )

    def test_whitespace_only_name_rejected(self, api):
        client, _ = api
        response = client.post("/screen", json={"name": "  ... "})
        assert response.status_code == 422
        assert "normalisation" in response.json()["detail"]

    def test_audit_row_written(self, api):
        client, db_path = api
        response = client.post("/screen", json={"name": "Abd al-Rahim Testman"})
        screening_id = response.json()["screening_id"]
        conn = connect(db_path)
        row = conn.execute(
            "SELECT * FROM screenings WHERE screening_id = ?", (screening_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["query_name"] == "Abd al-Rahim Testman"
        assert row["query_name_normalised"] == "abd al rahim testman"
        assert row["match_count"] == response.json()["match_count"]
        assert row["top_score"] == response.json()["matches"][0]["score"]
        assert row["latency_ms"] > 0
        stored = json.loads(row["results_json"])
        assert stored[0]["matched_name"] == response.json()["matches"][0]["matched_name"]


class TestHealthAndLists:
    def test_health(self, api):
        client, _ = api
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["embedding_layer"] == "disabled"
        sources = {info["source_list"] for info in body["lists"]}
        assert sources == {"DFAT", "UN", "OFAC"}
        dfat = next(i for i in body["lists"] if i["source_list"] == "DFAT")
        assert dfat["last_refreshed"] is not None
        assert dfat["last_status"] == "success"

    def test_lists_counts(self, api):
        client, db_path = api
        body = client.get("/lists").json()
        conn = connect(db_path)
        expected = {
            row["source_list"]: row["n"]
            for row in conn.execute("SELECT source_list, COUNT(*) n FROM entities GROUP BY 1")
        }
        conn.close()
        for info in body:
            assert info["entity_count"] == expected[info["source_list"]]
            assert info["name_count"] >= info["entity_count"]

    def test_entity_detail(self, api):
        client, _ = api
        body = client.get("/entity/OFAC/12345").json()
        assert body["primary_name"] == "TESTOV, Ivan Petrovich"
        assert body["raw_record"]["remarks"]
        assert any(n["name_type"] == "primary" for n in body["names"])
        assert client.get("/entity/OFAC/nope").status_code == 404

    def test_openapi_descriptions(self, api):
        client, _ = api
        spec = client.get("/openapi.json").json()
        screen_doc = spec["paths"]["/screen"]["post"]
        assert "audit" in screen_doc["description"]
        assert spec["info"]["title"] == "SanctionScreen"
