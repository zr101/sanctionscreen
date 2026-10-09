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

Runs on Streamlit Community Cloud (main file `demo/app.py`, with
`demo/requirements.txt` including the repo-root requirements); the databases download at boot from the public
HF dataset [zaeemr/sanctionscreen-data](https://huggingface.co/datasets/zaeemr/sanctionscreen-data).
The frontmatter above can also be used for an HF Docker Space built from the full repository.

## Review assistant in the existing live app

The existing app URL is <https://sanctionscreens.streamlit.app>. Select
**Review assistant** in its sidebar, or open
<https://sanctionscreens.streamlit.app/?workspace=review> directly.
The `demo/app.py` entry point runs the bounded review agent in the Streamlit
process, so no separate API server or running laptop is required. The ordinary
name-screening workspace still uses the official lists and embedding engine.
Review demonstrations use a separate fictional fixture database.

The live model is explicitly configured by `config/openrouter-demo.toml`:
`nvidia/nemotron-3-super-120b-a12b:free`. In this app's Streamlit Community Cloud
**Settings → Secrets**, add this TOML entry:

```toml
OPENROUTER_API_KEY = "your-openrouter-key"
# Optional public-demo allowance, shared by visitors; default 25 live runs/UTC day.
SANCTIONSCREEN_DEMO_DAILY_REVIEWS = 25
```

Keep the key in the cloud secret store, outside Git. The app also supports these
environment variables for local or container runs. Missing credentials disable
the live submit button; it does not silently use a mock. **Offline mock** is an
explicitly selected, labelled scripted demonstration.

Community Cloud watches the configured GitHub branch and updates the existing
app when source changes are committed. Dependencies install the checked-out
package, keeping the UI and agent on the same revision. Follow the official
[secrets instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
and [app update instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/edit-your-app).

Review runs share a concurrency slot, provider cooldown and UTC-day allowance.
Cases and traces are stored locally in ignored `data/hosted-review.db`, and each
case can be downloaded as JSON. Cloud storage may reset after a restart or
redeployment; download cases you want to keep. Free model quotas, outages and
Community Cloud sleep remain limits. Every draft is pending human review.

For a local preview, run from the repository root:

```bash
uv sync --all-extras
OPENROUTER_API_KEY=your-key uv run streamlit run demo/app.py
```

The optional Docker Space uses the full repository as its build context and
`demo/Dockerfile` as the Dockerfile; it runs the same `demo/app.py` entry point.

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
