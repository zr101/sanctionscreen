import json

import pytest

from sanctionscreen.db import connect
from sanctionscreen.matching.engine import MatchingEngine
from sanctionscreen.review.demo import build_demo_db, demo_settings
from sanctionscreen.review.schemas import DraftCaseArgs, ReviewRequest
from sanctionscreen.review.tools import TOOL_SPECS, ReviewTools


@pytest.fixture(scope="module")
def review_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("review") / "review.db"
    build_demo_db(db_path)
    conn = connect(db_path)
    engine = MatchingEngine(conn, demo_settings(db_path))
    conn.close()
    return engine, db_path


def make_tools(review_db, **request):
    engine, db_path = review_db
    request.setdefault("name", "Ivan Petrovich Testov")
    return ReviewTools(
        engine=engine, conn_factory=lambda: connect(db_path), request=ReviewRequest(**request)
    )


def valid_draft(**overrides) -> dict:
    draft = {
        "summary": "One candidate record returned on name similarity.",
        "candidates": [
            {
                "record_id": "OFAC:12345",
                "assessment": "possible_match",
                "evidence": [
                    {
                        "record_id": "OFAC:12345",
                        "field": "names",
                        "value": "TESTOV, Ivan Petrovich",
                        "kind": "supports",
                        "statement": "Primary name is highly similar.",
                    },
                    {
                        "record_id": "OFAC:12345",
                        "field": "date_of_birth",
                        "value": "15 Feb 1965",
                        "kind": "supports",
                        "statement": "Date of birth is identical.",
                    },
                ],
                "rationale": "Name and date of birth are consistent.",
            }
        ],
        "suggested_next_steps": ["Reviewer to check identity documents."],
    }
    draft.update(overrides)
    return draft


def screened_and_fetched(review_db, **request):
    tools = make_tools(review_db, **request)
    tools.call("screen_name", {"name": "Ivan Petrovich Testov"})
    tools.call("get_record", {"record_id": "OFAC:12345"})
    return tools


def test_tool_specs_are_flat_json_schema():
    names = [t["name"] for t in TOOL_SPECS]
    assert names == ["screen_name", "get_record", "request_information", "draft_case"]
    for spec in TOOL_SPECS:
        assert "$defs" not in json.dumps(spec)
        assert "$ref" not in json.dumps(spec)
        assert spec["parameters"]["type"] == "object"


class TestScreenName:
    def test_returns_engine_scores_and_writes_audit(self, review_db):
        tools = make_tools(review_db)
        outcome = tools.call("screen_name", {"name": "Ivan Petrovich Testov"})
        assert outcome.error is None
        top = outcome.content["candidates"][0]
        assert top["record_id"] == "OFAC:12345"
        assert set(top["layer_scores"]) == {"exact", "phonetic", "fuzzy", "embedding"}
        assert "similarity only" in outcome.content["note"]
        conn = connect(review_db[1])
        row = conn.execute(
            "SELECT query_name, max_results FROM screenings WHERE screening_id = ?",
            (outcome.content["screening_id"],),
        ).fetchone()
        conn.close()
        assert row["query_name"] == "Ivan Petrovich Testov"
        assert row["max_results"] == 5

    def test_threshold_floor(self, review_db):
        tools = make_tools(review_db, name="Ivan Testov")
        outcome = tools.call("screen_name", {"name": "Ivan Testov", "threshold": 5})
        assert outcome.content["threshold"] == 60

    def test_empty_result_note(self, review_db):
        outcome = make_tools(review_db, name="Zebadiah Quirkwhistle").call(
            "screen_name", {"name": "Zebadiah Quirkwhistle"}
        )
        assert outcome.content["candidate_count"] == 0
        assert "No list record reached" in outcome.content["note"]

    @pytest.mark.parametrize(
        "args",
        [{"name": ""}, {}, {"name": "x", "threshold": 500}, '{"name": ', {"name": "x", "y": 1}],
    )
    def test_malformed_arguments(self, review_db, args):
        outcome = make_tools(review_db).call("screen_name", args)
        assert outcome.error == "invalid_arguments"

    def test_json_string_arguments_accepted(self, review_db):
        outcome = make_tools(review_db, name="Ivan Testov").call(
            "screen_name", '{"name": "Ivan Testov"}'
        )
        assert outcome.error is None


class TestGetRecord:
    def test_rejects_unscreened_record(self, review_db):
        tools = make_tools(review_db)
        outcome = tools.call("get_record", {"record_id": "OFAC:12345"})
        assert outcome.error == "not_permitted"

    def test_returns_comparisons_against_analyst_details(self, review_db):
        tools = make_tools(
            review_db, name="Ivan Testov", date_of_birth="1980", nationality="Russia"
        )
        tools.call("screen_name", {"name": "Ivan Testov"})
        content = tools.call("get_record", {"record_id": "OFAC:12345"}).content
        verdicts = {c["field"]: c["result"] for c in content["comparison"]}
        assert verdicts == {
            "date_of_birth": "conflict",
            "nationality": "match",
            "entity_type": "unknown",
        }
        assert "untrusted_source_text" in content

    def test_model_cannot_replace_the_customer_identity(self, review_db):
        tools = make_tools(review_db)
        outcome = tools.call("screen_name", {"name": "Zebadiah Quirkwhistle"})
        assert outcome.error == "not_permitted"
        assert not tools.screening_ids

    def test_model_cannot_invent_a_filter_to_hide_matches(self, review_db):
        tools = make_tools(review_db)
        outcome = tools.call(
            "screen_name", {"name": "Ivan Petrovich Testov", "entity_type": "vessel"}
        )
        assert outcome.error == "not_permitted"
        assert not tools.screening_ids

    def test_unknown_tool(self, review_db):
        assert make_tools(review_db).call("delete_record", {}).error == "unknown_tool"


class TestDraftValidation:
    def test_valid_draft_accepted(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1965-02-15")
        outcome = tools.call("draft_case", valid_draft())
        assert outcome.terminal == "drafted"
        assert outcome.draft is not None
        assert outcome.draft.status == "pending_human_review"
        assert outcome.draft.screening_ids == tools.screening_ids

    def test_requires_screening_first(self, review_db):
        problems = make_tools(review_db).validate_draft(DraftCaseArgs(**valid_draft()))
        assert problems == ["call screen_name before drafting a case"]

    def test_requires_retrieval_and_full_coverage(self, review_db):
        tools = make_tools(review_db)
        tools.call("screen_name", {"name": "Ivan Petrovich Testov"})
        problems = tools.validate_draft(DraftCaseArgs(**valid_draft(candidates=[])))
        assert any("OFAC:12345 is not assessed" in p for p in problems)
        problems = tools.validate_draft(DraftCaseArgs(**valid_draft()))
        assert any("call get_record before" in p for p in problems)

    def test_invented_value_rejected(self, review_db):
        draft = valid_draft()
        draft["candidates"][0]["evidence"][1]["value"] = "12 Mar 1970"
        problems = screened_and_fetched(review_db, date_of_birth="1965").validate_draft(
            DraftCaseArgs(**draft)
        )
        assert any("does not appear in the record's date_of_birth" in p for p in problems)

    def test_numeric_name_score_cannot_be_invented_or_assigned_to_other_name(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1965")
        draft = valid_draft()
        draft["candidates"][0]["evidence"][0]["statement"] = "Name similarity score 12.34."
        problems = tools.validate_draft(DraftCaseArgs(**draft))
        assert any("name score must be the engine" in p for p in problems)
        # The numeric score is real, but it belongs to a different scored alias.
        tools.screened["OFAC:12345"]["matched_name"] = "TESTOV, Ivan"
        draft["candidates"][0]["evidence"][0]["statement"] = "Name score 94.18."
        problems = tools.validate_draft(DraftCaseArgs(**draft))
        assert any("score belongs to matched_name" in p for p in problems)

    def test_invented_field_value_on_missing_field_rejected(self, review_db):
        draft = valid_draft()
        draft["candidates"][0]["evidence"].append(
            {
                "record_id": "OFAC:12345",
                "field": "listed_date",
                "value": "2014-01-01",
                "kind": "unknown",
                "statement": "Listing date.",
            }
        )
        problems = screened_and_fetched(review_db, date_of_birth="1965").validate_draft(
            DraftCaseArgs(**draft)
        )
        assert any("record has no listed_date" in p for p in problems)

    def test_kind_must_follow_comparator(self, review_db):
        # Analyst DOB conflicts, but the draft calls it supporting.
        tools = screened_and_fetched(review_db, date_of_birth="1980")
        problems = tools.validate_draft(DraftCaseArgs(**valid_draft()))
        assert any("contradicts the deterministic date_of_birth" in p for p in problems)

    def test_conflicts_cannot_be_hidden(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1980")
        draft = valid_draft()
        draft["candidates"][0]["evidence"] = draft["candidates"][0]["evidence"][:1]
        problems = tools.validate_draft(DraftCaseArgs(**draft))
        assert any("date_of_birth conflict must be reported" in p for p in problems)

    def test_unlikely_requires_conflict(self, review_db):
        tools = screened_and_fetched(review_db)
        draft = valid_draft()
        draft["candidates"][0]["assessment"] = "unlikely_match"
        draft["candidates"][0]["evidence"] = draft["candidates"][0]["evidence"][:1]
        problems = tools.validate_draft(DraftCaseArgs(**draft))
        assert any("unlikely_match requires conflicting evidence" in p for p in problems)

    @pytest.mark.parametrize(
        "summary",
        [
            "Customer cleared.",
            "This is a false positive.",
            "Confirmed match with the listed individual.",
            "The customer is the same person as OFAC:12345.",
            "No further review required.",
            "No further screening steps required at this time.",
            "Recommend we approve the customer.",
        ],
    )
    def test_disposition_wording_rejected(self, review_db, summary):
        tools = screened_and_fetched(review_db, date_of_birth="1965-02-15")
        problems = tools.validate_draft(DraftCaseArgs(**valid_draft(summary=summary)))
        assert any("disposition or confirmed identity" in p for p in problems)

    def test_neutral_wording_allowed(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1965-02-15")
        draft = valid_draft(
            suggested_next_steps=["Obtain a passport to confirm the date of birth."]
        )
        assert tools.validate_draft(DraftCaseArgs(**draft)) == []

    def test_unknown_record_and_year_in_free_text(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1965-02-15")
        draft = valid_draft(summary="Also resembles UN:QDi.999, listed in 1999.")
        problems = tools.validate_draft(DraftCaseArgs(**draft))
        assert any("unknown record UN:QDi.999" in p for p in problems)
        assert any("year 1999" in p for p in problems)

    def test_record_citation_followed_by_sentence_punctuation(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1965-02-15")
        draft = valid_draft(summary="Evidence is from OFAC:12345. Human review is required.")
        assert tools.validate_draft(DraftCaseArgs(**draft)) == []

    def test_ambiguous_name_only_requires_information(self, review_db):
        tools = make_tools(review_db, name="Abdul Rahim Testman")
        tools.call("screen_name", {"name": "Abdul Rahim Testman"})
        for rid in tools.screened:
            tools.call("get_record", {"record_id": rid})
        problems = tools.validate_draft(
            DraftCaseArgs(
                summary="Two candidates.", suggested_next_steps=["Human review required."]
            )
        )
        assert any("request_information" in p for p in problems)

    @pytest.mark.parametrize("extra", [{"disposition": "cleared"}, {"status": "closed"}])
    def test_no_disposition_field_exists(self, review_db, extra):
        tools = screened_and_fetched(review_db, date_of_birth="1965-02-15")
        outcome = tools.call("draft_case", valid_draft(**extra))
        assert outcome.error == "invalid_arguments"


class TestRequestInformation:
    def test_information_request_cannot_bypass_human_review(self, review_db):
        tools = screened_and_fetched(review_db)
        outcome = tools.call(
            "request_information",
            {"fields": ["nationality"], "reason": "Customer cleared, but ask nationality."},
        )
        assert outcome.error == "not_permitted"
        assert outcome.terminal is None

    def test_ends_run(self, review_db):
        outcome = screened_and_fetched(review_db).call(
            "request_information", {"fields": ["date_of_birth"], "reason": "two candidates"}
        )
        assert outcome.terminal == "needs_information"

    def test_retrieval_failure_cannot_be_bypassed_by_question(self, review_db):
        tools = make_tools(review_db)
        tools.call("screen_name", {"name": "Ivan Petrovich Testov"})
        outcome = tools.call(
            "request_information", {"fields": ["date_of_birth"], "reason": "could not fetch record"}
        )
        assert outcome.error == "not_permitted"
        assert "get_record" in outcome.content["error"]

    def test_existing_comparison_should_be_drafted(self, review_db):
        tools = screened_and_fetched(review_db, date_of_birth="1965")
        outcome = tools.call(
            "request_information", {"fields": ["nationality"], "reason": "missing nationality"}
        )
        assert outcome.error == "not_permitted"
        assert "draft" in outcome.content["error"]

    def test_rejects_asking_for_provided_details(self, review_db):
        outcome = make_tools(review_db, date_of_birth="1965").call(
            "request_information", {"fields": ["date_of_birth"], "reason": "x"}
        )
        assert outcome.error == "not_permitted"
