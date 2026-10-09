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

## D12 — Review assistant: narrow tools, deterministic validator, no disposition field
The assistant's model chooses among four tools (`screen_name`, `get_record`,
`request_information`, `draft_case`). It cannot run arbitrary queries, can open
only the records it has just screened, and cannot record a decision. Facts come
from code:
- engine scores, unchanged;
- DOB, nationality and type verdicts from `review/compare.py`;
- record fields quoted verbatim.

`draft_case` is validated deterministically (grounding, coverage of every
screened candidate, comparator agreement, disposition and identity wording)
and returns the problems so the model can retry. The draft schema has no
disposition field and its status is fixed to `pending_human_review`.
Alternative: rely on the system prompt — rejected, because a prompt is
advice, not a control, and injected text in a record or a note can override
it.

## D13 — Explicit live provider and model, no fallback
`assistant.provider` defaults to the labelled offline mock. Live choices are
Ollama, OpenRouter and Groq, using the existing `httpx` dependency. A live model
must be named explicitly. OpenRouter accepts only specific `:free` catalog IDs,
sets zero prompt/completion price ceilings, requires tool parameters to be supported,
and disables provider fallbacks. Groq requires an account on its free plan; the
application cannot control Groq's account billing tier. The runnable local demo
uses OpenRouter's `nvidia/nemotron-3-super-120b-a12b:free`.

Unreachable or missing models return `model_unavailable`. A quota error returns
`rate_limited` with a retry delay, and the provider remains in cooldown until that
delay expires. Nothing switches to another model, the mock, or a paid variant.
Credentials belong in environment variables or the ignored, permission-restricted
`.env.local`, and are excluded from results and provider error messages.

## D14 — Mock model output is labelled everywhere
`MockReviewModel` is a deterministic policy that reacts to tool results; it
is not an LLM. `is_mock` is part of every `ReviewResult`, every
`review_cases` row, the demo banner, the UI banner and the eval report
header. Measured results produced by the mock are presented as checks of the
harness and guardrails, not of model quality.

## D15 — Local login services and separate fictional demo state
The portfolio runtime uses `data/review-demo.db`, built from fictional source-format
fixtures. It preserves the original list database and matching defaults. macOS
launchd starts API/UI at login and restarts them after process exits. Both bind
to loopback. Reinstalling identical job definitions uses `kickstart -k`, avoiding
the unload/re-register race observed during verification. Docker Compose remains
an alternative local runtime and forwards explicit model configuration.

## D16 — Bound resources before execution, validate decisions at tool boundaries
One review runs at a time per API process; a busy request gets HTTP 429 while
ordinary screening remains available. Model requests have an absolute timeout
across connection, writing and reading. Before another model request, the loop
reserves a conservative UTF-8-byte token bound for the transcript and tool schemas,
plus the output allowance. It checks time and token usage again before tools.

Information requests cannot bypass failed record retrieval. When comparable
identifiers exist, the assistant drafts the available evidence and lists remaining
details in the draft. Multiple candidates with only name evidence require an
information request. The model still chooses and supplies every tool call; the
loop never constructs a substitute draft.

The screening tool also refuses to replace the analyst's customer name or invent
an entity-type filter. Numeric evidence scores must equal the engine result and
cite the exact scored name, preventing alias scores being assigned to primary
names. Information requests and drafts both reject disposition wording, including
claims that no further screening is required.

## D17 — Measure live quality separately from mocks and validator coverage
The evaluation records the provider, exact model ID, dataset, date, command,
limits, task outcomes, failed requests, tool errors, rejections and usage. JSON
artifacts preserve live drafts and traces. A passing validator is not proof that
arbitrary free-text statements are factual: prose requires human source review.
Incomplete live runs return a failing command exit code rather than a green demo.

## D18 — Keep the existing hosted demo URL and run reviews in its process
The public Streamlit Community Cloud app starts `demo/app.py`, independently of
local API/UI services. Its Review assistant workspace calls the same bounded
`ReviewAgent` directly, using a separate fictional SQLite database and the
explicit free OpenRouter profile. The ordinary official-list screening workspace
retains its existing engine. Cloud credentials come from Streamlit Secrets or
environment variables; missing credentials disable live submission without a
fallback. A shared runtime retains provider cooldown and admits one review at a
time. A default allowance of 25 live runs per UTC day limits public-demo use.

Cases can be downloaded with their traces because free cloud disk state is not
durable across hosting-instance replacement. `demo/requirements.txt` takes
precedence over the root development lockfile on Community Cloud and includes
the pinned demo requirements. The package is installed from the checked-out
revision, avoiding a separate cached GitHub package that can lag behind the UI.
