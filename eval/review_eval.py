"""Evaluate the review assistant over synthetic scenarios.

Four metrics, reported separately:
- task completion  — expected status, every expected record assessed, every
                     expected conflict reported as `conflicts` evidence;
- unsupported claims — drafts the deterministic validator rejected (ungrounded
                     values, uncited records, comparator contradictions,
                     disposition wording), plus any that slipped into a final draft;
- tool failures    — tool errors traced (injected faults and model mistakes) and
                     whether the run still completed;
- model usage      — model calls, tool calls, tokens, wall-clock latency.

Writes eval/review_results.md (or --out). Results from the mock model measure
the harness and guardrails, NOT language-model quality; the report says so.

Usage:
    uv run python eval/review_eval.py                                   # offline mock
    uv run python eval/review_eval.py --provider ollama --model qwen2.5:7b \
        --out eval/review_results_ollama.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sanctionscreen.config import Settings
from sanctionscreen.db import connect
from sanctionscreen.matching.engine import MatchingEngine
from sanctionscreen.review.agent import ReviewAgent
from sanctionscreen.review.demo import build_demo_db, demo_settings
from sanctionscreen.review.models import build_model
from sanctionscreen.review.schemas import DraftCaseArgs, ReviewRequest, ReviewResult
from sanctionscreen.review.tools import ReviewTools

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "eval" / "fixtures" / "review_scenarios.json"


class GetRecordFailsOnce(ReviewTools):
    """Fault injection: the first get_record call raises."""

    def get_record(self, args):  # type: ignore[no-untyped-def]
        if not getattr(self, "_failed", False):
            self._failed = True
            raise RuntimeError("injected fault: database unavailable")
        return super().get_record(args)


FAULTS = {"get_record_fails_once": GetRecordFailsOnce}


def completion(result: ReviewResult, expect: dict) -> tuple[bool, list[str]]:
    misses = []
    if result.status != expect["status"]:
        misses.append(f"status {result.status} != {expect['status']}")
    if result.draft is not None:
        assessed = {c.record_id: c for c in result.draft.candidates}
        for rid in expect.get("records", []):
            if rid not in assessed:
                misses.append(f"{rid} not assessed")
        if expect.get("records") == [] and assessed:
            misses.append(f"expected no candidates, got {sorted(assessed)}")
        for rid, fields in expect.get("conflicts", {}).items():
            evidence = assessed[rid].evidence if rid in assessed else []
            cited = {e.field for e in evidence if e.kind == "conflicts"}
            for f in fields:
                if f not in cited:
                    misses.append(f"{rid} {f} conflict not reported")
    return not misses, misses


def residual_unsupported(result: ReviewResult, tools_check: ReviewTools | None) -> int:
    """Re-validate the final draft; > 0 would mean a guardrail let a claim through."""
    if result.draft is None or tools_check is None:
        return 0
    args = DraftCaseArgs(**result.draft.model_dump(exclude={"status", "screening_ids"}))
    return len(tools_check.validate_draft(args))


def git_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True)
        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=ROOT) != 0
        return sha.strip() + (" (+ uncommitted changes)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider", choices=["mock", "ollama", "groq", "openrouter"], default="mock"
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--out", default=str(ROOT / "eval" / "review_results.md"))
    args = parser.parse_args()
    if args.provider != "mock" and not args.model:
        parser.error("--model is required with a live provider (no default model)")

    config = Settings().assistant.model_copy(
        update={
            "provider": args.provider,
            "model": args.model,
            "ollama_url": args.ollama_url,
        }
    )
    model = build_model(config)
    fixture = json.loads(FIXTURE.read_text())
    rows = []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "eval.db"
        build_demo_db(db_path)
        conn = connect(db_path)
        engine = MatchingEngine(conn, demo_settings(db_path))
        conn.close()

        for sc in fixture["scenarios"]:
            captured: list[ReviewTools] = []
            tools_cls = FAULTS.get(sc.get("fault", ""), ReviewTools)

            def factory(tools_cls=tools_cls, captured=captured, **kw):  # type: ignore[no-untyped-def]
                tools = tools_cls(**kw)
                captured.append(tools)
                return tools

            agent = ReviewAgent(
                engine, lambda: connect(db_path), model, config, tools_factory=factory
            )
            result = agent.run(ReviewRequest(**sc["request"]))
            ok, misses = completion(result, sc["expect"])
            rows.append(
                {
                    "id": sc["id"],
                    "category": sc["category"],
                    "status": result.status,
                    "completed": ok,
                    "misses": misses,
                    "rejected_drafts": result.usage.rejected_drafts,
                    "residual_unsupported": residual_unsupported(
                        result, captured[0] if captured else None
                    ),
                    "tool_errors": result.usage.tool_errors,
                    "fault": sc.get("fault"),
                    "model_calls": result.usage.model_calls,
                    "failed_model_calls": result.usage.failed_model_calls,
                    "tool_calls": result.usage.tool_calls,
                    "tokens": result.usage.input_tokens + result.usage.output_tokens,
                    "elapsed_ms": result.usage.elapsed_ms,
                    "result": result.model_dump(mode="json"),
                }
            )
            print(f"{sc['id']}: {result.status}, completed={ok}", flush=True)
            if misses:
                print("  " + "; ".join(misses), flush=True)

    n = len(rows)
    done = sum(r["completed"] for r in rows)
    latencies = [r["elapsed_ms"] for r in rows]
    command = (
        shlex.quote(sys.executable)
        + " eval/review_eval.py "
        + " ".join(shlex.quote(a) for a in sys.argv[1:])
    )
    if os.environ.get("SANCTIONSCREEN_CONFIG"):
        command = (
            "SANCTIONSCREEN_CONFIG="
            + shlex.quote(os.environ["SANCTIONSCREEN_CONFIG"])
            + " "
            + command
        )
    label = (
        "**MOCK MODEL** (`mock-review-policy-v1`, a deterministic scripted policy — not an "
        "LLM). These numbers check the harness, tools and guardrails end to end; they say "
        "nothing about how a language model would perform."
        if model.is_mock
        else f"**LIVE MODEL** `{model.model}` via {model.provider}."
    )
    lines = [
        "# Review assistant evaluation",
        "",
        label,
        "",
        f"- **Provider / model:** {model.provider} / `{model.model}`",
        f"- **Dataset:** `{FIXTURE.relative_to(ROOT)}` — {n} synthetic scenarios over the "
        "fictional sample fixtures + one synthetic injection record",
        f"- **Command:** `{command.strip()}`",
        f"- **Date:** {dt.date.today().isoformat()}",
        f"- **Commit:** {git_commit()}",
        "- **Limits:** "
        f"{config.max_model_calls} model calls, {config.max_tool_calls} tool calls, "
        f"{config.max_total_tokens} tokens, {config.timeout_seconds:g}s per run",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Task completion | {done}/{n} |",
        f"| Drafts rejected by grounding / policy checks | "
        f"{sum(r['rejected_drafts'] for r in rows)} |",
        f"| Final draft validator violations | {sum(r['residual_unsupported'] for r in rows)} |",
        "| Free-text factual claims | Not independently verified by this harness |",
        f"| Tool errors traced | {sum(r['tool_errors'] for r in rows)} |",
        f"| Runs with tool errors that still completed | "
        f"{sum(1 for r in rows if r['tool_errors'] and r['completed'])}/"
        f"{sum(1 for r in rows if r['tool_errors'])} |",
        f"| Model calls (total / max per run) | {sum(r['model_calls'] for r in rows)} / "
        f"{max(r['model_calls'] for r in rows)} |",
        f"| Failed model requests | {sum(r['failed_model_calls'] for r in rows)} |",
        f"| Tool calls (total / max per run) | {sum(r['tool_calls'] for r in rows)} / "
        f"{max(r['tool_calls'] for r in rows)} |",
        f"| Tokens (total) | {sum(r['tokens'] for r in rows)} |",
        f"| Run latency p50 / max | {statistics.median(latencies):.1f} ms / "
        f"{max(latencies):.1f} ms |",
        "",
        "## Per scenario",
        "",
        "| Scenario | Category | Status | Completed | Rejected drafts | Tool errors | "
        "Model calls | Tool calls | Tokens | ms |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        mark = "yes" if r["completed"] else "no — " + "; ".join(r["misses"])
        lines.append(
            f"| {r['id']} | {r['category']} | {r['status']} | {mark} | {r['rejected_drafts']} | "
            f"{r['tool_errors']}{' (injected)' if r['fault'] else ''} | {r['model_calls']} | "
            f"{r['tool_calls']} | {r['tokens']} | {r['elapsed_ms']:.1f} |"
        )
    report = "\n".join(lines) + "\n"
    Path(args.out).write_text(report)
    Path(args.out).with_suffix(".json").write_text(
        json.dumps(
            {
                "provider": model.provider,
                "model": model.model,
                "is_mock": model.is_mock,
                "date": dt.date.today().isoformat(),
                "command": command,
                "limits": config.model_dump(mode="json", exclude={"api_key"}),
                "scenarios": rows,
            },
            indent=2,
        )
        + "\n"
    )
    print(report)
    return 0 if done == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
