---
title: SanctionScreen
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: "1.60.0"
app_file: app.py
pinned: false
license: mit
short_description: Explainable KYC name screening vs DFAT, UN & OFAC lists
---

# SanctionScreen — live demo

Layered, explainable KYC name screening against the **DFAT Consolidated
List**, the **UN Security Council Consolidated List** and the **US OFAC SDN
list** (≈24k entities / 54k searchable names, refreshed weekly).

Four matching layers — exact, Double Metaphone phonetics, RapidFuzz fuzzy and
multilingual sentence embeddings — combine into one 0–100 score with
per-layer sub-scores, so you can see *why* a name matched. Try:

- `Usama bin Ladin` · `Osama bin Laden` · `Laden Osama bin` (transliteration, order)
- `Vladimr Putin` (typo) · `Владимир Путин` (Cyrillic → Latin via embeddings)
- `Haji Abdul Manan` (honorific stripping)

Source, benchmarks and threshold-tuning guide:
**[github.com/zr101/sanctionscreen](https://github.com/zr101/sanctionscreen)**

*Portfolio/demonstration software — not legal advice, not a substitute for a
commercial screening product.*
