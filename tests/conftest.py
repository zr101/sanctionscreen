from pathlib import Path

import pytest

from sanctionscreen.db import connect
from sanctionscreen.ingestion.base import upsert_entities
from sanctionscreen.ingestion.dfat import parse_dfat
from sanctionscreen.ingestion.ofac import parse_ofac
from sanctionscreen.ingestion.un import parse_un

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "cache" / "samples"


@pytest.fixture(scope="session")
def sample_entities():
    """All entities parsed from the fictional sample fixtures."""
    return (
        parse_dfat(SAMPLES / "dfat_sample.xlsx")
        + parse_un(SAMPLES / "un_sample.xml")
        + parse_ofac(
            SAMPLES / "ofac_sdn_sample.csv",
            SAMPLES / "ofac_alt_sample.csv",
            SAMPLES / "ofac_add_sample.csv",
            SAMPLES / "ofac_sdn_comments_sample.csv",
        )
    )


@pytest.fixture
def sample_db(tmp_path, sample_entities):
    """A temporary database ingested from the sample fixtures."""
    conn = connect(tmp_path / "sample.db")
    with conn:
        upsert_entities(conn, sample_entities)
    yield conn
    conn.close()
