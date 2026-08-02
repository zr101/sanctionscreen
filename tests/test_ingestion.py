from pathlib import Path

import httpx
import pytest

from sanctionscreen.db import connect
from sanctionscreen.ingestion.base import upsert_entities
from sanctionscreen.ingestion.dfat import parse_dfat
from sanctionscreen.ingestion.download import DownloadError, fetch
from sanctionscreen.ingestion.ofac import parse_ofac
from sanctionscreen.ingestion.un import parse_un

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "cache" / "samples"


@pytest.fixture(scope="module")
def dfat_entities():
    return parse_dfat(SAMPLES / "dfat_sample.xlsx")


@pytest.fixture(scope="module")
def un_entities():
    return parse_un(SAMPLES / "un_sample.xml")


@pytest.fixture(scope="module")
def ofac_entities():
    return parse_ofac(
        SAMPLES / "ofac_sdn_sample.csv",
        SAMPLES / "ofac_alt_sample.csv",
        SAMPLES / "ofac_add_sample.csv",
        SAMPLES / "ofac_sdn_comments_sample.csv",
    )


class TestDfatParser:
    @pytest.fixture
    def entities(self, dfat_entities):
        return dfat_entities

    def test_groups_reference_prefix(self, entities):
        assert len(entities) == 3
        refs = {e.reference_number for e in entities}
        assert refs == {"1", "2", "3"}

    def test_primary_and_aliases(self, entities):
        individual = next(e for e in entities if e.reference_number == "1")
        assert individual.primary_name == "Abdul Rahim TESTMAN"
        assert individual.entity_type == "individual"
        types = sorted(n.name_type for n in individual.names)
        assert types == ["alias", "original_script", "primary"]
        alias = next(n for n in individual.names if n.name_type == "alias")
        assert alias.quality == "Strong"

    def test_original_script_kept(self, entities):
        individual = next(e for e in entities if e.reference_number == "1")
        scripts = [n for n in individual.names if n.name_type == "original_script"]
        assert scripts and "عبد" in scripts[0].original

    def test_control_date_is_listed_date(self, entities):
        individual = next(e for e in entities if e.reference_number == "1")
        assert individual.listed_date == "2010-05-04"

    def test_vessel_type(self, entities):
        vessel = next(e for e in entities if e.reference_number == "3")
        assert vessel.entity_type == "vessel"


class TestUnParser:
    @pytest.fixture
    def entities(self, un_entities):
        return un_entities

    def test_counts(self, entities):
        assert len(entities) == 3

    def test_name_joined_from_parts(self, entities):
        ind = next(e for e in entities if e.reference_number == "QDi.900")
        assert ind.primary_name == "ABDUL RAHIM TESTMAN"

    def test_empty_alias_nodes_skipped(self, entities):
        ind = next(e for e in entities if e.reference_number == "QDi.900")
        aliases = [n for n in ind.names if n.name_type == "alias"]
        assert len(aliases) == 2
        assert {a.quality for a in aliases} == {"Good", "Low"}

    def test_multiple_nationalities_joined(self, entities):
        ind = next(e for e in entities if e.reference_number == "QDi.900")
        assert ind.nationality == "Testland; Exampleia"

    def test_approx_dob(self, entities):
        ind = next(e for e in entities if e.reference_number == "QDi.900")
        assert ind.date_of_birth == "approximately 1971"

    def test_between_dob_and_dataid_fallback(self, entities):
        ind = next(e for e in entities if e.reference_number == "DATAID-900002")
        assert ind.date_of_birth == "between 1958 and 1960"

    def test_entity_alias(self, entities):
        ent = next(e for e in entities if e.reference_number == "QDe.900")
        assert ent.entity_type == "entity"
        assert any(n.original == "ELG Holdings" for n in ent.names)


class TestOfacParser:
    @pytest.fixture
    def entities(self, ofac_entities):
        return ofac_entities

    def test_counts_and_types(self, entities):
        assert len(entities) == 3
        by_ref = {e.reference_number: e for e in entities}
        assert by_ref["12345"].entity_type == "individual"
        assert by_ref["67890"].entity_type == "entity"  # "-0- " sentinel
        assert by_ref["24680"].entity_type == "vessel"

    def test_dob_and_nationality_from_remarks(self, entities):
        ind = next(e for e in entities if e.reference_number == "12345")
        assert ind.date_of_birth == "15 Feb 1965"
        assert ind.nationality == "Russia"

    def test_aliases_joined_and_sentinel_skipped(self, entities):
        ind = next(e for e in entities if e.reference_number == "12345")
        assert {(n.name_type, n.original) for n in ind.names} == {
            ("primary", "TESTOV, Ivan Petrovich"),
            ("aka", "TESTOV, Ivan"),
            ("fka", "PRIMEROV, Ivan Petrovich"),
        }
        ent = next(e for e in entities if e.reference_number == "67890")
        akas = [n for n in ent.names if n.name_type == "aka"]
        assert [n.original for n in akas] == ["ETF TRADING"]  # "-0- " alt skipped

    def test_comments_merged_into_remarks(self, entities):
        ent = next(e for e in entities if e.reference_number == "67890")
        assert "fictional remarks continuation" in ent.raw_record["remarks"]

    def test_addresses_in_raw_record(self, entities):
        ent = next(e for e in entities if e.reference_number == "67890")
        assert ent.raw_record["addresses"][0]["address"] == "PO Box 900001"


class TestUpsertIdempotency:
    def test_second_run_inserts_nothing(self, tmp_path, sample_entities):
        conn = connect(tmp_path / "idem.db")
        with conn:
            first = upsert_entities(conn, sample_entities)
        with conn:
            second = upsert_entities(conn, sample_entities)
        assert first.inserted == 9
        assert second.inserted == 0
        assert second.updated == 0
        assert second.unchanged == 9
        conn.close()

    def test_name_ids_stable_across_reingestion(self, tmp_path, sample_entities):
        conn = connect(tmp_path / "stable.db")
        with conn:
            upsert_entities(conn, sample_entities)
        ids_before = [r["id"] for r in conn.execute("SELECT id FROM names ORDER BY id")]
        with conn:
            upsert_entities(conn, sample_entities)
        ids_after = [r["id"] for r in conn.execute("SELECT id FROM names ORDER BY id")]
        assert ids_before == ids_after
        conn.close()

    def test_names_normalised_and_keyed(self, sample_db):
        row = sample_db.execute(
            "SELECT name_normalised, metaphone_key FROM names WHERE name_original = ?",
            ("TESTOV, Ivan Petrovich",),
        ).fetchone()
        assert row["name_normalised"] == "testov ivan petrovich"
        assert row["metaphone_key"]


class TestFetch:
    def test_live_download_writes_cache(self, tmp_path):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"payload"))
        client = httpx.Client(transport=transport, follow_redirects=True)
        result = fetch("https://example.org/list.csv", tmp_path / "list.csv", client=client)
        assert not result.from_cache
        assert (tmp_path / "list.csv").read_bytes() == b"payload"

    def test_failure_falls_back_to_cache(self, tmp_path):
        cache = tmp_path / "list.csv"
        cache.write_bytes(b"cached")
        transport = httpx.MockTransport(lambda req: httpx.Response(503))
        client = httpx.Client(transport=transport, follow_redirects=True)
        result = fetch("https://example.org/list.csv", cache, attempts=1, client=client)
        assert result.from_cache
        assert cache.read_bytes() == b"cached"

    def test_failure_without_cache_raises(self, tmp_path):
        transport = httpx.MockTransport(lambda req: httpx.Response(503))
        client = httpx.Client(transport=transport, follow_redirects=True)
        with pytest.raises(DownloadError):
            fetch("https://example.org/x.csv", tmp_path / "x.csv", attempts=1, client=client)

    def test_offline_uses_cache(self, tmp_path):
        cache = tmp_path / "list.csv"
        cache.write_bytes(b"cached")
        result = fetch("https://example.org/list.csv", cache, offline=True)
        assert result.from_cache

    def test_offline_without_cache_raises(self, tmp_path):
        with pytest.raises(DownloadError):
            fetch("https://example.org/list.csv", tmp_path / "missing.csv", offline=True)
