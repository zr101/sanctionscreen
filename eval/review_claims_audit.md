# Source audit of live review prose

Model: `nvidia/nemotron-3-super-120b-a12b:free` via OpenRouter. Date: 2026-10-09.
Dataset: the 12 synthetic scenarios in `fixtures/review_scenarios.json`.
This is a manual source comparison by the coding agent, not an independent human
review or a calibrated factuality benchmark.

## Findings before the final checks

The run in [review_results_openrouter_before_source_audit.json](review_results_openrouter_before_source_audit.json)
completed 12/12 tasks and passed the then-current validator. Its accepted drafts
still had issues that the task-completion total did not reveal:

| Scenario | Finding | Authoritative source |
|---|---|---|
| `conflict_dob` | Evidence attributed score 93.0 to the primary name. The scored name was the `aka` name `TESTOV, Ivan`. | Successful `screen_name` result for `OFAC:12345` |
| `conflict_nationality` | The same alias score was attributed to the primary name. | Successful `screen_name` result for `OFAC:12345` |
| `tool_failure_get_record` | The same score attribution issue occurred after successful failure recovery. | Successful `screen_name` result for `OFAC:12345` |
| `no_match` | Suggested next steps stated that no further screening was required. This exceeds the assistant's authority to draft for a human. | Accepted draft's `suggested_next_steps` |
| `injection_in_name` | The same disposition-like next-step wording appeared in the injection scenario. | Accepted draft's `suggested_next_steps` |

Counts for this inspected artifact: **3 unsupported score attributions**,
**2 disposition-like next steps**, and **0 invented structured names, DOBs,
nationalities or entity types found**. A general guarantee about other free-text
claims cannot be derived from these counts.

Source exports are preserved in
[review_sources_openrouter_before_audit.json](review_sources_openrouter_before_audit.json).
To reproduce the export without another model call:

```bash
.venv/bin/python eval/export_review_sources.py \
  --report eval/review_results_openrouter_before_source_audit.json \
  --out /tmp/sanctionscreen-source-audit.json
```

## Changes prompted by the audit

Numeric evidence scores must now equal the engine score and cite the actual
`matched_name`. A primary-name evidence item cannot borrow an alias's score.
The disposition guard also rejects “no further screening” wording. Both changes
have regression tests. The final live report is generated after these checks,
with draft/trace JSON and source exports available for further human review.

## Final inspected run

The final [live report](review_results_openrouter.md) completed **12/12** tasks:
11 accepted drafts and one information request. The coding agent compared all
accepted summaries, evidence statements, rationales and next steps with the
screening output, retrieved records and analyst inputs exported to
[review_sources_openrouter.json](review_sources_openrouter.json).

| Inspected issue | Findings in this artifact |
|---|---:|
| Unsupported attribution of a numeric score to a different name | 0 |
| Disposition-like “no further screening” next steps | 0 |
| Invented structured names, DOBs, nationalities or entity types | 0 |

The entity summary's listing date (`2016-11-30`) and DPRK committee reference
were also checked against the retrieved `DFAT:2` source excerpt. They were
present in the fictional fixture. Three earlier drafts were rejected during the
final run and corrected before acceptance; four tool errors were traced, and
all four affected runs completed.

These observations apply to this saved artifact. They do not prove general
factuality, safe disposition wording in every paraphrase, or production quality.
The entity scenario still suggested collecting a date of birth, a person-oriented
field that a human should replace with appropriate entity identifiers. Review
judgements and suggested questions require human scrutiny even when factual
fields and scores are grounded.
