# DECISIONS

Design decisions made during the build. Each entry records what was decided,
why, and what the alternative was.

## D1 — Python 3.12, managed by uv
The system Python is 3.14, which is ahead of reliable wheel coverage for the
torch/tokenizers stack. Pinned 3.12 via `.python-version` and
`requires-python = ">=3.12,<3.13"`; uv auto-installs the interpreter and produces
`uv.lock`. Alternative: pip-tools — rejected because uv also solves interpreter
management and is faster in CI.

## D2 — Embeddings are an optional extra, on by default in Docker
`sentence-transformers` + `torch` live in the `[embeddings]` extra so a lightweight
install (`uv sync`) works without ~1.5 GB of ML dependencies. The matching engine
treats the embedding layer as gracefully optional: if the library or model is
unavailable (or `embedding.enabled = false` in config), layers 1–3 still serve
results, a warning is logged, and `/health` reports the layer as disabled. The spec's
layer 4 exists and is exercised in Docker and in `-m embeddings` tests.
On Linux, torch resolves from the CPU-only PyTorch index to keep the Docker image
free of CUDA (~250 MB wheel instead of ~2.5 GB).

## D3 — Embedding model: paraphrase-multilingual-MiniLM-L12-v2
384-dim, small enough for CPU inference, multilingual (needed for Arabic/Cyrillic
"Original Script" rows on the DFAT list). Exactly the model suggested in the brief.

## D4 — Score combination: max-with-weights, not weighted sum
`score = max(1.00·exact, 0.97·fuzzy, 0.90·phonetic, 0.85·embedding)`, plus a small
corroboration bonus (+3, capped at 99) when two or more non-exact layers score ≥ 80.
A weighted sum dilutes single-signal hits — a pure transliteration where only the
embedding layer fires would be dragged below threshold by three zeros. Max keeps each
layer's contribution interpretable ("fuzzy-only match caps at 97") and makes
threshold tuning intuitive. All constants live in `config/default.toml` and can be
overridden with `SANCTIONSCREEN_*` environment variables.

## D5 — Fuzzy sub-score down-weights partial_ratio
`fuzzy = 0.7·token_sort_ratio + 0.3·partial_ratio`. partial_ratio alone over-scores
substring hits ("Ali" vs "Ali Baba Trading Co"); token_sort_ratio handles name-order
swaps, which is the case the brief calls out.

## D6 — Embedding vectors live in a separate, uncommitted SQLite file
Embedding blobs would push the committed DB past 100 MB and bloat every git clone.
Vectors sit in `data/embeddings.db` (gitignored, regenerable): the committed
`data/sanctions.db` carries entities/names/log tables only, and the vectors file is
recomputed incrementally at ingestion or on first run (~1–2 min CPU for all 54k
names). Alternative: Git LFS — rejected to keep the repo dependency-free for
reviewers.

## D7 — Source URLs live in config, not code
All three publishers have moved their endpoints within the last two years (DFAT to a
new XLSX in Nov 2025, OFAC to the Sanctions List Service). URLs sit in
`config/default.toml` with the cached-copy fallback covering outages.

## D8 — DFAT "Control Date" is used as listed_date
The DFAT XLSX has no clean listing-date column; the legal listing date is buried in
free-text "Listing Information". Control Date is the closest structured field; the
full row is preserved in `raw_record` for audit.

## D9 — Honorifics are stripped only as leading tokens, both variants indexed
"Haji"/"Mullah" etc. are honorifics in some names and genuine name parts in others.
Normalisation strips them only from the front of a name, and when stripping changes
the string the index keeps both the stripped and unstripped normalised forms.

## D10 — `metaphone` package, not jellyfish
The brief allowed either; jellyfish only implements original Metaphone, while
the `metaphone` package provides true Double Metaphone (primary + secondary
codes). Non-Latin tokens yield no code and fall back to the raw token so
original-script names stay phonetically indexable.

## D11 — Docker bakes the model and vectors into the API image
Refinement of D6: at image build time the HF model is downloaded and all name
vectors precomputed, so containers start in seconds and run fully offline
(`HF_HUB_OFFLINE=1`). `--build-arg WITH_EMBEDDINGS=0` produces a lite image
(layers 1–3 only) at roughly a tenth of the size.
