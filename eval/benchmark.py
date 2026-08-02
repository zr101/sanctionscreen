"""Benchmark the full matching pipeline over the adversarial fixture.

Reports precision / recall / F1 at several thresholds, per-perturbation-type
recall, and p50/p95 screening latency, as markdown tables (stdout and
eval/results.md) ready to paste into the README.

Definitions (case level, top-10 results per screen):
- a case is FLAGGED at threshold t when any match scores >= t;
- a positive case is a TRUE POSITIVE at t when its truth entity is among the
  matches scoring >= t, and a FALSE POSITIVE when it is flagged without the
  truth entity;
- a negative case that is flagged is a FALSE POSITIVE.
Latency is engine time per screen call (in-process, model warm — HTTP adds
~1-2 ms on top).

Usage: uv run python eval/benchmark.py [--no-embeddings]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sanctionscreen.config import Settings
from sanctionscreen.db import connect
from sanctionscreen.matching.embedding import create_embedder
from sanctionscreen.matching.engine import MatchingEngine

THRESHOLDS = [60, 70, 75, 80, 90]
FLOOR = min(THRESHOLDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-embeddings", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    fixture = json.loads((root / "eval" / "fixtures" / "testset.json").read_text())
    cases = fixture["cases"]

    settings = Settings()
    if args.no_embeddings:
        settings.embedding.enabled = False
    conn = connect(root / "data" / "sanctions.db")
    embedder = create_embedder(settings) if settings.embedding.enabled else None
    engine = MatchingEngine(conn, settings, embedder=embedder)
    conn.close()
    if embedder is not None:
        embedder.query("warm up")
    embedding_state = "on" if embedder is not None else "off"
    print(f"engine ready: {len(engine.entries)} names, embedding layer {embedding_state}\n")

    latencies: list[float] = []
    screened: list[tuple[dict, list]] = []
    for case in cases:
        start = time.perf_counter()
        results = engine.screen(case["query"], threshold=FLOOR, max_results=10)
        latencies.append((time.perf_counter() - start) * 1000)
        screened.append((case, results))

    lines: list[str] = []

    lines.append("### Precision / recall by threshold\n")
    lines.append("| Threshold | Precision | Recall | F1 | TP | FP | FN |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for threshold in THRESHOLDS:
        tp = fp = fn = 0
        for case, results in screened:
            hits = [r for r in results if r.score >= threshold]
            truth = case["truth"]
            if truth is None:
                if hits:
                    fp += 1
                continue
            truth_hit = any(
                r.entity.source_list == truth["source_list"]
                and r.entity.reference_number == truth["reference_number"]
                for r in hits
            )
            if truth_hit:
                tp += 1
            else:
                fn += 1
                if hits:
                    fp += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        lines.append(
            f"| {threshold} | {precision:.3f} | {recall:.3f} | {f1:.3f} | {tp} | {fp} | {fn} |"
        )

    lines.append("\n### Recall by perturbation type (threshold 75)\n")
    lines.append("| Perturbation | Cases | Recall |")
    lines.append("|---|---:|---:|")
    by_type: dict[str, list[bool]] = {}
    for case, results in screened:
        truth = case["truth"]
        if truth is None:
            continue
        hit = any(
            r.score >= 75
            and r.entity.source_list == truth["source_list"]
            and r.entity.reference_number == truth["reference_number"]
            for r in results
        )
        by_type.setdefault(case["perturbation"], []).append(hit)
    for perturbation in sorted(by_type):
        hits = by_type[perturbation]
        lines.append(f"| {perturbation} | {len(hits)} | {sum(hits) / len(hits):.3f} |")

    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[18]
    lines.append("\n### Latency per screen call\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| p50 | {p50:.1f} ms |")
    lines.append(f"| p95 | {p95:.1f} ms |")
    lines.append(f"| calls | {len(latencies)} |")
    lines.append(f"| embedding layer | {embedding_state} |")

    report = "\n".join(lines) + "\n"
    print(report)
    out = root / "eval" / "results.md"
    out.write_text(report)
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
