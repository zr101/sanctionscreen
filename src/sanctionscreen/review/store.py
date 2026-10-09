"""Persistence for review runs in the review_cases table (db.py).

The full result — draft, trace and usage — is stored as JSON next to the
screening audit rows it references, so a reviewer can reconstruct what the
assistant saw and did.
"""

from __future__ import annotations

import json
import sqlite3

from sanctionscreen.review.schemas import ReviewResult


def save_case(conn: sqlite3.Connection, result: ReviewResult) -> None:
    with conn:
        conn.execute(
            "INSERT INTO review_cases (case_id, query_name, status, provider, model,"
            " is_mock, screening_ids, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.case_id,
                result.request.name,
                result.status,
                result.provider,
                result.model,
                int(result.is_mock),
                json.dumps(result.screening_ids),
                result.model_dump_json(),
            ),
        )


def get_case(conn: sqlite3.Connection, case_id: str) -> ReviewResult | None:
    row = conn.execute(
        "SELECT result_json FROM review_cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    return ReviewResult.model_validate_json(row["result_json"]) if row else None
