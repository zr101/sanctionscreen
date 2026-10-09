# Free live review runtime

The local portfolio demo uses OpenRouter's explicit
`nvidia/nemotron-3-super-120b-a12b:free` model and the existing API/UI. The model
chooses native tools. Deterministic code supplies matching scores, comparisons
and draft checks. Every draft remains pending human review.

For the public app linked from GitHub, the same assistant is integrated directly
into `demo/app.py`. Use its **Review assistant** workspace; it does not connect
to the local API. Cloud secrets and operation are described in
[the hosted demo instructions](../demo/README.md). The local services below remain
an independent option with persistent storage on this Mac.

## Free options checked on 2026-10-09

| Option | Cost / access | Practical limit | Implementation |
|---|---|---|---|
| OpenRouter `:free` | Zero token prices on catalogued free variants; API key required | Account and upstream quotas, changing model availability | Live evaluated; default local demo configuration |
| Local Ollama | No API charges or account; uses your computer | RAM, computation and electricity; computer must stay awake | Native-tool client supported; not the verified runtime model |
| Groq Free plan | Free API access within account limits; key required | Account billing tier and model-specific quotas | Adapter tested offline; not live evaluated |
| Gemini Free tier | Free tokens on eligible models; key required | Project quotas; free-tier content may be used to improve products | Researched alternative; no Gemini adapter added |

Sources: [OpenRouter free variants](https://openrouter.ai/docs/guides/routing/model-variants/free),
[quota API](https://openrouter.ai/docs/api_reference/limits),
[Ollama native tools](https://docs.ollama.com/capabilities/tool-calling),
[Groq limits](https://console.groq.com/docs/rate-limits),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

The public OpenRouter catalog listed Nemotron 3 Super, Gemma 4 31B, Gemma 4 26B
A4B and Liquid LFM 2.5 2.6B as free tool-capable options when checked. This is
availability evidence, not a comparative quality benchmark. Discover current
options and your actual quota:

```bash
.venv/bin/python scripts/free_models.py
.venv/bin/python scripts/free_models.py --quota
```

Each tool round trip consumes an inference request. This account reported a
1,000-request daily allowance during setup; other accounts may have a different
ceiling. Read the quota API instead of relying on a universal free-tier number.
The service enforces `:free`, zero prompt/completion price ceilings, no plugins
and no model/provider fallback. An unavailable model or exhausted quota produces
a stored failed run with its trace and retry guidance. It never upgrades billing.

## Install and run locally

Python 3.12 and the existing base + UI dependencies are sufficient. No additional
provider SDK is required.

```bash
uv sync --extra ui
```

Create `.env.local` at the repository root with
`SANCTIONSCREEN_ASSISTANT__API_KEY=your-openrouter-key`, then `chmod 600 .env.local`.
The file is ignored by Git. You can instead supply `OPENROUTER_API_KEY` in the
process environment. Keep the credential out of versioned TOML files.

```bash
.venv/bin/python scripts/local_service.py prepare
.venv/bin/python scripts/local_service.py install
.venv/bin/python scripts/local_service.py status
```

On macOS, install registers `local.sanctionscreen.api` and
`local.sanctionscreen.ui` under `~/Library/LaunchAgents/`. They run at login and
restart after exits. Both listen on `127.0.0.1`:

- UI: <http://127.0.0.1:8501/Review_assistant>
- API docs: <http://127.0.0.1:8000/docs>
- Configured model: <http://127.0.0.1:8000/assistant>

`config/openrouter-demo.toml` selects the provider, model and limits. The manager
seeds a separate `data/review-demo.db` using fictional source-format fixtures;
it preserves `data/sanctions.db`. Logs and verification results go to ignored
`.runtime/`. The synthetic fixtures do not need periodic list downloads. For
screening against official lists, use the existing ingestion process and main
configuration; refresh the in-memory index by restarting the API after ingestion.

```bash
.venv/bin/python scripts/local_service.py install    # restart using current config
.venv/bin/python scripts/local_service.py stop       # unload jobs; preserve cases and credentials
```

For foreground operation on any supported OS:

```bash
SANCTIONSCREEN_CONFIG=config/openrouter-demo.toml PYTHONPATH=src \
  .venv/bin/python -m uvicorn sanctionscreen.api.main:app --host 127.0.0.1
# another terminal
SANCTIONSCREEN_DEMO_DATA=1 .venv/bin/streamlit run ui/app.py --server.address=127.0.0.1
```

Docker Compose retains restart policies and accepts explicit provider/model/key
environment values; `docker compose --env-file .env.local up --build` forwards
the credential. Compose uses the original official-list database and mock model
unless provider and model are explicitly overridden.

## Verify the full path

```bash
.venv/bin/python scripts/check_local.py --live
.venv/bin/python scripts/check_local.py --restart-check
SANCTIONSCREEN_CONFIG=config/openrouter-demo.toml .venv/bin/python eval/review_eval.py \
  --provider openrouter --model nvidia/nemotron-3-super-120b-a12b:free \
  --out eval/review_results_openrouter.md
```

The live check drives the Streamlit form using its test runner, makes a real
HTTP review request, checks the live tool trace, verifies the stored case round
trip, compares screening scores before/after and checks that a UI rerun retains
the case. The restart check intentionally terminates only the demo API and waits
for a different healthy PID. The eval saves both Markdown and JSON; a scenario
failure returns exit code 1.

## Limits and recovery

The demo bounds each run to 8 model calls, 10 tool calls, 2,048 output tokens per
call, 48,000 total tokens and 90 seconds. A conservative input budget reservation
can stop earlier. The model request deadline covers connect/write/read together.
Independent record fetches can share a model turn. Reasoning is disabled on the
OpenRouter path. One review is admitted per API process, with HTTP 429 for a busy
slot; screening and health remain available.

Provider HTTP 429 enters a cooldown using `Retry-After` (60 seconds when absent).
The caller can resubmit after the cooldown. Daily quota, connection and model
errors keep the API alive and the configured model unchanged. There is no
unlimited hosted free API guarantee: login services resume when the Mac is awake
and logged in; Internet access and free-provider availability are required.

Review prose still needs human verification. Structured evidence is validated,
but the keyword and record-ID/year checks are not a general factual verifier.
The measured scenarios use fictional data and cannot establish production
compliance quality. The initial implementation stayed local; publishing the
follow-up hosted integration uses the existing Streamlit app and GitHub branch.
