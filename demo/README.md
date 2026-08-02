---
title: SanctionScreen
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8501
pinned: false
license: mit
short_description: Explainable KYC name screening vs DFAT, UN & OFAC lists
---

# SanctionScreen — live demo

Runs on Streamlit Community Cloud (main file `demo/app.py`, deps from the
repo-root `requirements.txt`); the databases download at boot from the public
HF dataset [zaeemr/sanctionscreen-data](https://huggingface.co/datasets/zaeemr/sanctionscreen-data).
The frontmatter above also makes this folder deployable as an HF Docker Space.

**Is this person on a sanctions list?** Sanctions lists are government
registers of people, companies and ships that everyone else is banned from
dealing with — assets frozen, funds blocked, travel barred — so regulated
businesses must check customers against them.

This screens a name against the **DFAT Consolidated List** (Australia), the
**UN Security Council Consolidated List** and the **US OFAC SDN list**
(≈24k listed parties / 54k searchable names, refreshed weekly).

Four matching layers — exact, Double Metaphone phonetics, RapidFuzz fuzzy and
multilingual sentence embeddings — combine into one 0–100 score with
per-layer sub-scores, so you can see *why* a name matched. Try:

- `Vladimr Putin` (typo) · `Lavrov Sergei` (order swap + transliteration)
- `Владимир Путин` (Cyrillic → Latin via embeddings)
- `Kim Jong Un` (exact match)

Source, benchmarks and threshold-tuning guide:
**[github.com/zr101/sanctionscreen](https://github.com/zr101/sanctionscreen)**

*Demonstration software — not legal advice, not a substitute for a
commercial screening product.*
