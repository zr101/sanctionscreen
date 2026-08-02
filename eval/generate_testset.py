"""Generate the adversarial evaluation fixture from real list entries.

Seeded and deterministic: ~200 positive cases derived from names in the
committed data/sanctions.db, each perturbed one way (transliteration, typo,
name-order swap, dropped middle name, nickname/diminutive, spacing/hyphen)
and labelled with the true entity's stable (source_list, reference_number)
key — plus ~100 clean negative names that should not match anything.

Unperturbed originals are excluded: a case is only kept when the perturbed
query normalises differently from every name of the truth entity.

Usage: uv run python eval/generate_testset.py  (writes eval/fixtures/testset.json)
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sanctionscreen.db import connect
from sanctionscreen.normalise import normalise_name

SEED = 42
POSITIVE_TARGET = {
    "transliteration": 40,
    "typo": 40,
    "order_swap": 30,
    "dropped_middle": 30,
    "nickname": 20,
    "spacing_hyphen": 40,
}
NEGATIVE_TARGET = 100

TRANSLITERATION_RULES: list[tuple[str, str]] = [
    ("mohammed", "muhammad"),
    ("mohammed", "mohamed"),
    ("muhammad", "mohammed"),
    ("hussein", "husain"),
    ("hussein", "hussain"),
    ("abdul", "abdool"),
    ("rahman", "rahmaan"),
    ("ahmed", "ahmad"),
    ("ahmad", "ahmed"),
    ("aziz", "azeez"),
    ("hakim", "hakeem"),
    ("rashid", "rasheed"),
    ("shaikh", "sheikh"),
    ("usama", "osama"),
    ("osama", "usama"),
    ("yusuf", "yousef"),
    ("yousef", "yusuf"),
    ("ibrahim", "ebrahim"),
    ("khalil", "khaleel"),
    ("jamal", "jamaal"),
    ("said", "sayid"),
    ("ali", "aly"),
    ("hasan", "hassan"),
    ("hassan", "hasan"),
    ("qasim", "kasim"),
    ("tariq", "tarek"),
    ("walid", "waleed"),
    ("sergei", "sergey"),
    ("aleksandr", "alexander"),
    ("viktor", "victor"),
    ("dmitri", "dmitriy"),
    ("yuri", "yury"),
    ("vladimir", "wladimir"),
    ("oleg", "olegh"),
    ("kim", "gim"),
    ("ou", "u"),
    ("oo", "u"),
    ("ee", "i"),
]

NICKNAMES: dict[str, str] = {
    "mohammed": "mohd",
    "muhammad": "mohd",
    "abdul": "abd",
    "vladimir": "vova",
    "alexander": "sasha",
    "aleksandr": "sasha",
    "mikhail": "misha",
    "sergei": "seryoga",
    "nikolai": "kolya",
    "dmitri": "dima",
    "dmitry": "dima",
    "ivan": "vanya",
    "viktor": "vitya",
    "yevgeny": "zhenya",
    "evgeny": "zhenya",
    "konstantin": "kostya",
    "william": "bill",
    "robert": "bob",
    "richard": "dick",
    "ibrahim": "ibro",
    "ismail": "isma",
    "abdullah": "abdu",
}

NEGATIVE_FIRST = [
    "Bronwyn",
    "Lachlan",
    "Matilda",
    "Angus",
    "Imogen",
    "Declan",
    "Sienna",
    "Callum",
    "Freya",
    "Hamish",
    "Isla",
    "Rory",
    "Tahlia",
    "Ewan",
    "Greta",
    "Quentin",
    "Marigold",
    "Barnaby",
    "Clementine",
    "Digby",
    "Prudence",
    "Alistair",
    "Rosalind",
    "Fergus",
    "Winifred",
    "Horace",
    "Beatrix",
    "Cedric",
    "Gwendolyn",
    "Rupert",
]
NEGATIVE_LAST = [
    "Fairweather",
    "Thistlewood",
    "Brightwater",
    "Copperfield",
    "Dunstable",
    "Everingham",
    "Featherstone",
    "Greenhalgh",
    "Honeybourne",
    "Inglewood",
    "Kingsley-Smith",
    "Larkspur",
    "Merriweather",
    "Netherwood",
    "Oakhurst",
    "Pemberton",
    "Quigley",
    "Ravenscroft",
    "Silverthorne",
    "Tunbridge",
    "Underhill",
    "Waverley",
    "Wetherby",
    "Yarrow",
    "Ashcombe",
]


def load_candidates(conn) -> list[dict]:
    """Primary names of individuals with multi-token Latin-script names."""
    rows = conn.execute(
        "SELECT e.id, e.source_list, e.reference_number, e.primary_name, e.entity_type"
        " FROM entities e WHERE e.entity_type = 'individual'"
    ).fetchall()
    candidates = []
    for row in rows:
        name = row["primary_name"]
        norm = normalise_name(name)
        tokens = norm.split()
        if len(tokens) < 2 or not re.fullmatch(r"[a-z0-9 ]+", norm):
            continue
        candidates.append(
            {
                "source_list": row["source_list"],
                "reference_number": row["reference_number"],
                "name": name,
                "norm": norm,
                "tokens": tokens,
            }
        )
    return candidates


def entity_norms(conn) -> dict[tuple[str, str], set[str]]:
    """All normalised names per entity, to exclude unperturbed queries."""
    norms: dict[tuple[str, str], set[str]] = {}
    for row in conn.execute(
        "SELECT e.source_list, e.reference_number, n.name_normalised"
        " FROM names n JOIN entities e ON e.id = n.entity_id"
    ):
        norms.setdefault((row["source_list"], row["reference_number"]), set()).add(
            row["name_normalised"]
        )
    return norms


def perturb_transliteration(rng: random.Random, cand: dict) -> str | None:
    applicable = [(src, dst) for src, dst in TRANSLITERATION_RULES if src in cand["norm"]]
    if not applicable:
        return None
    src, dst = rng.choice(applicable)
    return cand["norm"].replace(src, dst, 1)


def perturb_typo(rng: random.Random, cand: dict) -> str | None:
    name = cand["norm"]
    letters = "abcdefghijklmnopqrstuvwxyz"
    edits = rng.choice([1, 1, 2])
    chars = list(name)
    for _ in range(edits):
        positions = [i for i, c in enumerate(chars) if c.isalpha()]
        if not positions:
            return None
        i = rng.choice(positions)
        op = rng.choice(["sub", "del", "ins", "swap"])
        if op == "sub":
            chars[i] = rng.choice(letters.replace(chars[i], ""))
        elif op == "del" and len(chars) > 4:
            del chars[i]
        elif op == "ins":
            chars.insert(i, rng.choice(letters))
        elif op == "swap" and i + 1 < len(chars) and chars[i + 1].isalpha():
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def perturb_order_swap(rng: random.Random, cand: dict) -> str | None:
    tokens = cand["tokens"][:]
    if len(tokens) < 2:
        return None
    for _ in range(10):
        rng.shuffle(tokens)
        if tokens != cand["tokens"]:
            return " ".join(tokens)
    return None


def perturb_dropped_middle(rng: random.Random, cand: dict) -> str | None:
    tokens = cand["tokens"]
    if len(tokens) < 3:
        return None
    keep = [tokens[0], tokens[-1]]
    return " ".join(keep)


def perturb_nickname(rng: random.Random, cand: dict) -> str | None:
    tokens = cand["tokens"][:]
    hits = [i for i, t in enumerate(tokens) if t in NICKNAMES]
    if not hits:
        return None
    i = rng.choice(hits)
    tokens[i] = NICKNAMES[tokens[i]]
    return " ".join(tokens)


def perturb_spacing_hyphen(rng: random.Random, cand: dict) -> str | None:
    tokens = cand["tokens"]
    if len(tokens) < 2:
        return None
    i = rng.randrange(len(tokens) - 1)
    style = rng.choice(["merge", "hyphen", "split"])
    if style == "merge":
        merged = [*tokens[:i], tokens[i] + tokens[i + 1], *tokens[i + 2 :]]
        return " ".join(merged)
    if style == "hyphen":
        merged = [*tokens[:i], tokens[i] + "-" + tokens[i + 1], *tokens[i + 2 :]]
        return " ".join(merged)
    token = tokens[i] if len(tokens[i]) >= 6 else tokens[-1]
    if len(token) < 6:
        return None
    cut = rng.randrange(2, len(token) - 2)
    parts = [t for t in tokens if t != token]
    return " ".join([*parts, token[:cut], token[cut:]])


PERTURBERS = {
    "transliteration": perturb_transliteration,
    "typo": perturb_typo,
    "order_swap": perturb_order_swap,
    "dropped_middle": perturb_dropped_middle,
    "nickname": perturb_nickname,
    "spacing_hyphen": perturb_spacing_hyphen,
}


def main() -> None:
    rng = random.Random(SEED)
    root = Path(__file__).resolve().parent.parent
    conn = connect(root / "data" / "sanctions.db")
    candidates = load_candidates(conn)
    norms = entity_norms(conn)
    all_norms = {n for values in norms.values() for n in values}
    conn.close()
    rng.shuffle(candidates)

    cases: list[dict] = []
    case_id = 0
    used: set[tuple[str, str, str]] = set()
    for perturbation, target in POSITIVE_TARGET.items():
        produced = 0
        perturber = PERTURBERS[perturbation]
        pool = iter(candidates)
        while produced < target:
            cand = next(pool, None)
            if cand is None:
                print(f"warning: pool exhausted for {perturbation} at {produced}/{target}")
                break
            key = (cand["source_list"], cand["reference_number"], perturbation)
            if key in used:
                continue
            query = perturber(rng, cand)
            if not query:
                continue
            truth_key = (cand["source_list"], cand["reference_number"])
            if normalise_name(query) in norms.get(truth_key, set()):
                continue  # unperturbed or collapsed back to an indexed name
            used.add(key)
            case_id += 1
            cases.append(
                {
                    "case_id": f"P{case_id:03d}",
                    "query": query,
                    "perturbation": perturbation,
                    "original_name": cand["name"],
                    "truth": {
                        "source_list": cand["source_list"],
                        "reference_number": cand["reference_number"],
                    },
                }
            )
            produced += 1

    negatives = 0
    for first in NEGATIVE_FIRST:
        for last in NEGATIVE_LAST:
            if negatives >= NEGATIVE_TARGET:
                break
            query = f"{first} {last}"
            if normalise_name(query) in all_norms:
                continue
            negatives += 1
            cases.append(
                {
                    "case_id": f"N{negatives:03d}",
                    "query": query,
                    "perturbation": "negative",
                    "original_name": None,
                    "truth": None,
                }
            )
        if negatives >= NEGATIVE_TARGET:
            break

    out = root / "eval" / "fixtures" / "testset.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"seed": SEED, "cases": cases}, indent=1, ensure_ascii=False) + "\n")
    by_type: dict[str, int] = {}
    for case in cases:
        by_type[case["perturbation"]] = by_type.get(case["perturbation"], 0) + 1
    print(f"wrote {len(cases)} cases to {out}")
    print(by_type)


if __name__ == "__main__":
    main()
