"""Parser for the UN Security Council Consolidated List (XML).

Format notes:
- Root <CONSOLIDATED_LIST> holds <INDIVIDUALS><INDIVIDUAL> and
  <ENTITIES><ENTITY> nodes.
- Individual names are split across FIRST_NAME..FOURTH_NAME; join the
  non-empty parts.
- <INDIVIDUAL_ALIAS>/<ENTITY_ALIAS> nodes are frequently empty and must be
  skipped; QUALITY is Good/Low.
- Dates of birth come as TYPE_OF_DATE EXACT/APPROXIMATELY/BETWEEN with
  DATE, YEAR, or FROM_YEAR/TO_YEAR.
- REFERENCE_NUMBER is occasionally missing; DATAID is the stable fallback
  upsert key.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from sanctionscreen.ingestion.base import ParsedEntity, ParsedName

_NAME_PARTS = ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")


def _text(node: ET.Element | None, tag: str) -> str | None:
    if node is None:
        return None
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _full_name(node: ET.Element) -> str:
    parts = [_text(node, part) for part in _NAME_PARTS]
    return " ".join(p for p in parts if p)


def _dob(node: ET.Element, tag: str) -> str | None:
    formatted: list[str] = []
    for dob in node.findall(tag):
        type_of_date = (_text(dob, "TYPE_OF_DATE") or "").upper()
        exact = _text(dob, "DATE") or _text(dob, "YEAR")
        if type_of_date == "BETWEEN":
            frm, to = _text(dob, "FROM_YEAR"), _text(dob, "TO_YEAR")
            if frm or to:
                formatted.append(f"between {frm or '?'} and {to or '?'}")
        elif type_of_date == "APPROXIMATELY" and exact:
            formatted.append(f"approximately {exact}")
        elif exact:
            formatted.append(exact)
    return "; ".join(formatted) or None


def _nationalities(node: ET.Element) -> str | None:
    values = [
        v.text.strip()
        for nat in node.findall("NATIONALITY")
        for v in nat.findall("VALUE")
        if v.text and v.text.strip()
    ]
    return "; ".join(values) or None


def _aliases(node: ET.Element, tag: str) -> list[ParsedName]:
    aliases: list[ParsedName] = []
    for alias in node.findall(tag):
        name = _text(alias, "ALIAS_NAME")
        if name:  # empty alias nodes are common — skip them
            aliases.append(
                ParsedName(name_type="alias", original=name, quality=_text(alias, "QUALITY"))
            )
    return aliases


def _raw(node: ET.Element) -> dict:
    record: dict = {}
    for child in node:
        if len(child):
            value: object = _raw(child)
        else:
            value = child.text.strip() if child.text and child.text.strip() else None
        if child.tag in record:
            existing = record[child.tag]
            if not isinstance(existing, list):
                record[child.tag] = [existing]
            record[child.tag].append(value)
        else:
            record[child.tag] = value
    return record


def parse_un(path: Path) -> list[ParsedEntity]:
    root = ET.parse(path).getroot()
    entities: list[ParsedEntity] = []

    for node in root.iter("INDIVIDUAL"):
        name = _full_name(node)
        if not name:
            continue
        reference = _text(node, "REFERENCE_NUMBER") or f"DATAID-{_text(node, 'DATAID')}"
        entities.append(
            ParsedEntity(
                source_list="UN",
                reference_number=reference,
                primary_name=name,
                entity_type="individual",
                nationality=_nationalities(node),
                date_of_birth=_dob(node, "INDIVIDUAL_DATE_OF_BIRTH"),
                listed_date=_text(node, "LISTED_ON"),
                raw_record=_raw(node),
                names=[
                    ParsedName(name_type="primary", original=name),
                    *_aliases(node, "INDIVIDUAL_ALIAS"),
                ],
            )
        )

    for node in root.iter("ENTITY"):
        ent_name = _text(node, "FIRST_NAME")
        if not ent_name:
            continue
        reference = _text(node, "REFERENCE_NUMBER") or f"DATAID-{_text(node, 'DATAID')}"
        entities.append(
            ParsedEntity(
                source_list="UN",
                reference_number=reference,
                primary_name=ent_name,
                entity_type="entity",
                nationality=None,
                date_of_birth=None,
                listed_date=_text(node, "LISTED_ON"),
                raw_record=_raw(node),
                names=[
                    ParsedName(name_type="primary", original=ent_name),
                    *_aliases(node, "ENTITY_ALIAS"),
                ],
            )
        )

    return entities
