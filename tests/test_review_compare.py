import pytest

from sanctionscreen.review.compare import (
    DateSpec,
    compare_dob,
    compare_entity_type,
    compare_nationality,
    parse_dates,
)


class TestParseDates:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1964-11-28", [DateSpec(1964, 1964, 11, 28)]),
            ("02 Aug 1987", [DateSpec(1987, 1987, 8, 2)]),
            ("Mar 1945", [DateSpec(1945, 1945, 3)]),
            ("1941", [DateSpec(1941, 1941)]),
            ("Approximately 1971", [DateSpec(1971, 1971)]),
            ("between 1958 and 1960", [DateSpec(1958, 1960)]),
            ("1958-1960", [DateSpec(1958, 1960)]),
        ],
    )
    def test_source_formats(self, text, expected):
        assert parse_dates(text) == expected

    def test_multiple_dates(self):
        assert parse_dates("01 Apr 1972; 1972") == [
            DateSpec(1972, 1972, 4, 1),
            DateSpec(1972, 1972),
        ]

    def test_empty_and_unparseable(self):
        assert parse_dates(None) == []
        assert parse_dates("unknown") == []


class TestCompareDob:
    def test_full_date_match(self):
        assert compare_dob("1965-02-15", "15 Feb 1965").result == "match"

    def test_full_date_conflict_same_year(self):
        assert compare_dob("1965-03-01", "15 Feb 1965").result == "conflict"

    def test_year_only_customer_is_consistent(self):
        assert compare_dob("1965", "15 Feb 1965").result == "match"

    def test_year_conflict(self):
        result = compare_dob("1980-06-01", "15 Feb 1965")
        assert result.result == "conflict"
        assert "1980-06-01" in result.detail

    def test_range_and_approximate(self):
        assert compare_dob("1959-01-01", "between 1958 and 1960").result == "match"
        assert compare_dob("1961", "between 1958 and 1960").result == "conflict"
        assert compare_dob("1971-05-05", "approximately 1971").result == "match"

    def test_any_listed_date_can_match(self):
        assert compare_dob("1972", "01 Apr 1972; 1972").result == "match"

    @pytest.mark.parametrize(("customer", "record"), [(None, "1965"), ("1965", None)])
    def test_missing_side_is_unknown(self, customer, record):
        assert compare_dob(customer, record).result == "unknown"

    def test_unparseable_customer_is_unknown(self):
        assert compare_dob("sometime", "1965").result == "unknown"


class TestCompareNationality:
    def test_match_among_several(self):
        assert compare_nationality("Exampleia", "Testland; Exampleia").result == "match"

    def test_trailing_punctuation_and_case(self):
        assert compare_nationality("russia", "Russia.").result == "match"

    def test_prefix_variant(self):
        assert compare_nationality("Russian Federation", "Russia").result == "match"

    def test_conflict(self):
        assert compare_nationality("Exampleia", "Russia").result == "conflict"

    def test_unknown(self):
        assert compare_nationality(None, "Russia").result == "unknown"
        assert compare_nationality("Russia", None).result == "unknown"


def test_compare_entity_type():
    assert compare_entity_type("individual", "individual").result == "match"
    assert compare_entity_type("entity", "individual").result == "conflict"
    assert compare_entity_type(None, "individual").result == "unknown"
