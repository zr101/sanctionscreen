"""Replay successful read-only tool calls to export sources for a prose audit.

Uses a temporary fictional DB; does not invoke a model or write to live state.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sanctionscreen.config import AssistantConfig  # noqa: E402
from sanctionscreen.db import connect  # noqa: E402
from sanctionscreen.review.demo import build_demo_db, make_agent  # noqa: E402
from sanctionscreen.review.schemas import ReviewRequest  # noqa: E402
from sanctionscreen.review.tools import ReviewTools  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="eval/review_results_openrouter.json")
    parser.add_argument("--out", default="eval/review_sources_openrouter.json")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text())
    sources = []
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "audit.db"
        build_demo_db(db_path)
        agent = make_agent(db_path, AssistantConfig())
        for scenario in report["scenarios"]:
            result = scenario["result"]
            tools = ReviewTools(
                agent.engine, lambda: connect(db_path), ReviewRequest(**result["request"])
            )
            records = []
            for event in result["trace"]:
                if event["kind"] == "tool_call" and event["name"] in {"screen_name", "get_record"}:
                    outcome = tools.call(event["name"], event["arguments"])
                    if outcome.error:
                        raise ValueError(f"cannot replay {scenario['id']}: {outcome.error}")
                    records.append({"tool": event["name"], "result": outcome.content})
            sources.append(
                {"scenario": scenario["id"], "customer": result["request"], "sources": records}
            )
    Path(args.out).write_text(
        json.dumps(
            {"model": report["model"], "date": report["date"], "scenarios": sources}, indent=2
        )
        + "\n"
    )
    print(f"Exported {len(sources)} scenario sources to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
