"""Deterministic comparison of analyst-supplied details against a list record.

These functions, not the language model, decide whether a date of birth,
nationality or entity type matches, conflicts or cannot be compared. The
model may only repeat their verdicts (the draft validator enforces this).

Date formats handled are the ones the three sources actually publish:
1964-11-28 (DFAT), 02 Aug 1987 / Mar 1945 / 1941 / "01 Apr 1972; 1972" (OFAC),
"approximately 1971" / "between 1958 and 1960" (UN).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Result = Literal["match", "conflict", "unknown"]

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DAY_MON_YEAR = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b")
_MON_YEAR = re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\b")
_RANGE = re.compile(r"\b(\d{4})\s*(?:-|to|and)\s*(\d{4})\b")
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


@dataclass(frozen=True)
class DateSpec:
    year_from: int
    year_to: int
    month: int | None = None
    day: int | None = None

    @property
    def is_full_date(self) -> bool:
        return self.year_from == self.year_to and self.month is not None and self.day is not None


@dataclass(frozen=True)
class Comparison:
    field: str
    result: Result
    customer_value: str | None
    record_value: str | None
    detail: str


def parse_dates(text: str | None) -> list[DateSpec]:
    """Every date or year range found in a published DOB string."""
    if not text:
        return []
    specs: list[DateSpec] = []

    def iso(m: re.Match[str]) -> str:
        y, mo, d = (int(g) for g in m.groups())
        specs.append(DateSpec(y, y, mo, d))
        return " "

    def day_mon_year(m: re.Match[str]) -> str:
        month = _MONTHS.get(m.group(2).lower())
        if month is None:
            return m.group(0)
        y = int(m.group(3))
        specs.append(DateSpec(y, y, month, int(m.group(1))))
        return " "

    def mon_year(m: re.Match[str]) -> str:
        month = _MONTHS.get(m.group(1).lower())
        if month is None:
            return m.group(0)
        y = int(m.group(2))
        specs.append(DateSpec(y, y, month))
        return " "

    def year_range(m: re.Match[str]) -> str:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        specs.append(DateSpec(lo, hi))
        return " "

    rest = _ISO.sub(iso, text)
    rest = _DAY_MON_YEAR.sub(day_mon_year, rest)
    rest = _MON_YEAR.sub(mon_year, rest)
    rest = _RANGE.sub(year_range, rest)
    specs.extend(DateSpec(int(y), int(y)) for y in _YEAR.findall(rest))
    return specs


def compare_dob(customer: str | None, record: str | None) -> Comparison:
    def out(result: Result, detail: str) -> Comparison:
        return Comparison("date_of_birth", result, customer, record, detail)

    if not customer:
        return out("unknown", "no customer date of birth was provided")
    if not record:
        return out("unknown", "the record publishes no date of birth")
    cust_specs = parse_dates(customer)
    rec_specs = parse_dates(record)
    if not cust_specs:
        return out("unknown", f"could not parse customer date of birth {customer!r}")
    if not rec_specs:
        return out("unknown", f"could not parse record date of birth {record!r}")
    cust = cust_specs[0]
    year = cust.year_from
    for spec in rec_specs:
        if not spec.year_from <= year <= spec.year_to:
            continue
        if cust.is_full_date and spec.is_full_date:
            if (spec.month, spec.day) == (cust.month, cust.day):
                return out("match", "full date of birth is identical")
            continue
        if cust.month and spec.month and cust.month != spec.month:
            continue
        return out("match", f"birth year {year} is consistent with the record ({record})")
    return out("conflict", f"customer date of birth {customer} is not consistent with {record}")


def _nationality_tokens(text: str) -> list[str]:
    parts = re.split(r"[;,/]|\band\b", text.casefold())
    return [re.sub(r"[^\w\s]", "", p).strip() for p in parts if p.strip(" .")]


def compare_nationality(customer: str | None, record: str | None) -> Comparison:
    def out(result: Result, detail: str) -> Comparison:
        return Comparison("nationality", result, customer, record, detail)

    if not customer:
        return out("unknown", "no customer nationality was provided")
    if not record:
        return out("unknown", "the record publishes no nationality")
    for c in _nationality_tokens(customer):
        for r in _nationality_tokens(record):
            # prefix match tolerates "Russia" vs "Russian Federation" / "Russian"
            if c and r and (c == r or c.startswith(r) or r.startswith(c)):
                return out("match", f"customer nationality {customer!r} appears in {record!r}")
    return out("conflict", f"customer nationality {customer!r} not among {record!r}")


def compare_entity_type(customer: str | None, record: str | None) -> Comparison:
    if not customer:
        return Comparison("entity_type", "unknown", customer, record, "no customer type given")
    if customer == record:
        return Comparison("entity_type", "match", customer, record, f"both are type {record}")
    return Comparison(
        "entity_type", "conflict", customer, record, f"customer is {customer}, record is {record}"
    )
