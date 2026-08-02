"""Parser for the US OFAC SDN list (legacy CSV flat files).

Format notes (Sanctions List Service exports):
- No header rows. The null sentinel is literally "-0- " (trailing space).
- sdn.csv: ent_num, SDN_Name, SDN_Type, Program, Title, Call_Sign,
  Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks.
  SDN_Type is individual/vessel/aircraft, or the sentinel for entities.
- alt.csv: ent_num, alt_num, alt_type (aka/fka/nka), alt_name, alt_remarks.
- add.csv: ent_num, add_num, address, city_state_zip, country, add_remarks.
- Remarks longer than the flat-file limit spill into sdn_comments.csv.
- DOB and nationality only exist inside free-text Remarks
  ("DOB 28 Apr 1937; POB Tikrit, Iraq; nationality Iraq") — extracted
  best-effort, with the full remarks preserved in raw_record.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from sanctionscreen.ingestion.base import ParsedEntity, ParsedName

_SENTINEL = "-0-"
_DOB_RE = re.compile(r"DOB[:\s]+([^;]+)", re.IGNORECASE)
_NATIONALITY_RE = re.compile(r"nationality[:\s]+([^;]+)", re.IGNORECASE)

_SDN_COLUMNS = (
    "ent_num", "sdn_name", "sdn_type", "program", "title", "call_sign",
    "vess_type", "tonnage", "grt", "vess_flag", "vess_owner", "remarks",
)

_ALT_TYPES = {"aka": "aka", "fka": "fka", "nka": "nka"}


def _clean(value: str) -> str | None:
    value = value.strip()
    return None if not value or value == _SENTINEL else value


def _read_rows(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return list(csv.reader(text.splitlines()))


def parse_ofac(
    sdn_path: Path,
    alt_path: Path | None = None,
    add_path: Path | None = None,
    comments_path: Path | None = None,
) -> list[ParsedEntity]:
    aliases: dict[str, list[dict[str, str | None]]] = {}
    if alt_path is not None and alt_path.is_file():
        for row in _read_rows(alt_path):
            if len(row) < 4:
                continue
            ent_num = row[0].strip()
            aliases.setdefault(ent_num, []).append(
                {"alt_type": _clean(row[2]), "alt_name": _clean(row[3])}
            )

    addresses: dict[str, list[dict[str, str | None]]] = {}
    if add_path is not None and add_path.is_file():
        for row in _read_rows(add_path):
            if len(row) < 5:
                continue
            ent_num = row[0].strip()
            addresses.setdefault(ent_num, []).append(
                {
                    "address": _clean(row[2]),
                    "city_state_zip": _clean(row[3]),
                    "country": _clean(row[4]),
                }
            )

    extra_remarks: dict[str, str] = {}
    if comments_path is not None and comments_path.is_file():
        for row in _read_rows(comments_path):
            if len(row) >= 2 and (comment := _clean(row[1])):
                extra_remarks[row[0].strip()] = comment

    entities: list[ParsedEntity] = []
    for row in _read_rows(sdn_path):
        if len(row) < 12:
            continue
        record = {col: _clean(cell) for col, cell in zip(_SDN_COLUMNS, row, strict=False)}
        ent_num = row[0].strip()
        name = record["sdn_name"]
        if not ent_num or not name:
            continue

        sdn_type = (record["sdn_type"] or "").lower()
        entity_type = sdn_type if sdn_type in ("individual", "vessel", "aircraft") else "entity"

        remarks = " ".join(r for r in (record["remarks"], extra_remarks.get(ent_num)) if r)
        dob_matches = _DOB_RE.findall(remarks)
        nat_match = _NATIONALITY_RE.search(remarks)

        names = [ParsedName(name_type="primary", original=name)]
        for alias in aliases.get(ent_num, []):
            if alias["alt_name"]:
                names.append(
                    ParsedName(
                        name_type=_ALT_TYPES.get((alias["alt_type"] or "").lower(), "aka"),
                        original=alias["alt_name"],
                    )
                )

        entities.append(
            ParsedEntity(
                source_list="OFAC",
                reference_number=ent_num,
                primary_name=name,
                entity_type=entity_type,
                nationality=nat_match.group(1).strip() if nat_match else None,
                date_of_birth="; ".join(m.strip() for m in dob_matches) or None,
                listed_date=None,  # not present in the legacy flat files
                raw_record={
                    **record,
                    "remarks": remarks or None,
                    "aliases": aliases.get(ent_num, []),
                    "addresses": addresses.get(ent_num, []),
                },
                names=names,
            )
        )
    return entities
