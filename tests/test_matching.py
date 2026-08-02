import pytest

from sanctionscreen.config import ScoringConfig, Settings
from sanctionscreen.matching.engine import MatchingEngine
from sanctionscreen.matching.fuzzy import fuzzy_score
from sanctionscreen.matching.phonetic import metaphone_key, phonetic_similarity
from sanctionscreen.matching.scoring import LayerScores, combine


class TestPhonetic:
    def test_key_is_order_invariant(self):
        assert metaphone_key("ali hassan") == metaphone_key("hassan ali")

    def test_transliterations_share_key(self):
        assert metaphone_key("mohammed") == metaphone_key("muhammad")
        assert metaphone_key("mohamed") == metaphone_key("mohammed")

    def test_similarity_full_and_partial(self):
        assert phonetic_similarity("ali hassan", "hassan ali") == 100.0
        partial = phonetic_similarity("ali hassan", "ali hassan omar")
        assert 0 < partial < 100

    def test_non_latin_tokens_fall_back(self):
        # Arabic tokens have no metaphone code but still index consistently.
        assert metaphone_key("عبد الرحيم") == metaphone_key("الرحيم عبد")
        assert phonetic_similarity("عبد الرحيم", "عبد الرحيم") == 100.0

    def test_empty(self):
        assert phonetic_similarity("", "ali") == 0.0


class TestFuzzy:
    def test_order_swap_scores_high(self):
        score = fuzzy_score("ali hassan", "hassan ali", token_sort_weight=0.7, partial_weight=0.3)
        assert score > 90

    def test_substring_downweighted(self):
        score = fuzzy_score("ali", "ali baba trading co", token_sort_weight=0.7, partial_weight=0.3)
        assert score < 60  # partial_ratio alone would be 100


class TestScoring:
    CFG = ScoringConfig()

    def test_exact_wins(self):
        result = combine(LayerScores(exact=100, fuzzy=100, phonetic=100), self.CFG)
        assert result.score == 100.0

    def test_fuzzy_only_caps_at_97(self):
        assert combine(LayerScores(fuzzy=100), self.CFG).score == 97.0

    def test_phonetic_only_caps_at_90(self):
        assert combine(LayerScores(phonetic=100), self.CFG).score == 90.0

    def test_embedding_only_caps_at_85(self):
        assert combine(LayerScores(embedding=100), self.CFG).score == 85.0

    def test_corroboration_bonus(self):
        without = combine(LayerScores(fuzzy=85), self.CFG)
        with_bonus = combine(LayerScores(fuzzy=85, phonetic=85), self.CFG)
        assert with_bonus.score == pytest.approx(without.score + 3.0)

    def test_no_bonus_on_exact(self):
        result = combine(LayerScores(exact=100, fuzzy=90, phonetic=90), self.CFG)
        assert result.score == 100.0

    def test_layers_fired(self):
        result = combine(LayerScores(fuzzy=80.0), self.CFG)
        assert result.layers_fired == ["fuzzy"]


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    from pathlib import Path

    from sanctionscreen.db import connect
    from sanctionscreen.ingestion.base import upsert_entities
    from sanctionscreen.ingestion.dfat import parse_dfat
    from sanctionscreen.ingestion.ofac import parse_ofac
    from sanctionscreen.ingestion.un import parse_un

    SAMPLES = Path(__file__).resolve().parent.parent / "data" / "cache" / "samples"
    conn = connect(tmp_path_factory.mktemp("db") / "engine.db")
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
        upsert_entities(conn, entities)
    yield MatchingEngine(conn, Settings())
    conn.close()


class TestEngine:
    def test_exact_match_scores_100(self, engine):
        results = engine.screen("Testov, Ivan Petrovich")
        assert results and results[0].score == 100.0
        assert results[0].entity.source_list == "OFAC"
        assert "exact" in results[0].layers_fired

    def test_alias_matches_and_groups_to_one_entity(self, engine):
        results = engine.screen("Abd al-Rahim Testman")
        dfat = [r for r in results if r.entity.source_list == "DFAT"]
        assert len(dfat) == 1  # multiple name rows, one grouped result
        assert dfat[0].score == 100.0
        assert dfat[0].matched_name_type == "alias"

    def test_name_order_swap(self, engine):
        results = engine.screen("Ivan Petrovich Testov")
        assert results and results[0].entity.reference_number == "12345"
        assert results[0].score >= 90

    def test_typo_matches_via_fuzzy(self, engine):
        results = engine.screen("Testov Ivan Petrovick", threshold=70)
        assert results and results[0].entity.reference_number == "12345"
        assert results[0].layers.fuzzy > 80

    def test_transliteration_via_phonetic(self, engine):
        results = engine.screen("Abdool Raheem Testmann", threshold=70)
        assert results
        assert results[0].entity.primary_name == "Abdul Rahim TESTMAN"
        assert results[0].layers.phonetic == 100.0

    def test_honorific_stripped_query(self, engine):
        results = engine.screen("Haji Abdul Rahim Testman")
        assert results and results[0].score == 100.0

    def test_entity_type_filter(self, engine):
        unfiltered = engine.screen("Example Star", threshold=60)
        assert any(r.entity.entity_type == "vessel" for r in unfiltered)
        filtered = engine.screen("Example Star", threshold=60, entity_type="individual")
        assert all(r.entity.entity_type == "individual" for r in filtered)

    def test_no_match_for_clean_name(self, engine):
        assert engine.screen("Zebadiah Quirkwhistle") == []

    def test_empty_query(self, engine):
        assert engine.screen("") == []
        assert engine.screen("   !!! ") == []

    def test_max_results(self, engine):
        results = engine.screen("Testov", threshold=0, max_results=2)
        assert len(results) <= 2

    def test_results_ranked_descending(self, engine):
        results = engine.screen("Ivan Testov", threshold=0, max_results=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_original_script_query(self, engine):
        results = engine.screen("عبد الرحيم تستمان")
        assert results and results[0].score == 100.0
        assert results[0].matched_name_type == "original_script"
