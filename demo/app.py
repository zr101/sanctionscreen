"""SanctionScreen — hosted Streamlit demo.

Unlike ui/app.py (which talks to the FastAPI service over HTTP), this Space
imports the matching engine directly so the whole demo runs in one free
container. Official screening data loads on demand; the review assistant
uses a separate fictional database and an explicitly selected free model.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st
from huggingface_hub import hf_hub_download
from pydantic import ValidationError
from streamlit.errors import StreamlitSecretNotFoundError

from sanctionscreen.config import Settings
from sanctionscreen.db import connect
from sanctionscreen.matching.embedding import create_embedder
from sanctionscreen.matching.engine import MatchingEngine
from sanctionscreen.review.hosted import HostedReviewNotReady, HostedReviewRuntime
from sanctionscreen.review.schemas import ReviewRequest

DATA_REPO = "zaeemr/sanctionscreen-data"
ROOT = Path(__file__).resolve().parent.parent

EXAMPLES = [
    "Vladimir Putin",
    "Vladimr Putin",
    "Lavrov Sergei",
    "Владимир Путин",
    "Kim Jong Un",
]

st.set_page_config(page_title="SanctionScreen", page_icon="🛡️", layout="wide")


@st.cache_resource(show_spinner=False)
def load_review_runtime() -> HostedReviewRuntime:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
        "SANCTIONSCREEN_ASSISTANT__API_KEY", ""
    )
    daily_limit = os.environ.get("SANCTIONSCREEN_DEMO_DAILY_REVIEWS", "25")
    try:
        key = key or str(st.secrets.get("OPENROUTER_API_KEY", ""))
        daily_limit = str(st.secrets.get("SANCTIONSCREEN_DEMO_DAILY_REVIEWS", daily_limit))
    except StreamlitSecretNotFoundError:
        pass
    return HostedReviewRuntime(ROOT, key, daily_limit=int(daily_limit))


def review_page() -> None:
    st.header("Review assistant")
    st.caption(
        "The model chooses tools to screen a name, retrieve records and draft evidence. "
        "Matching scores stay deterministic. Every draft awaits a human decision."
    )
    st.info(
        "Fictional records for this review demo. Try Ivan Petrovich Testov, "
        "DOB 1965-02-15, nationality Russia; or Abdul Rahim Testman without details. "
        "Enter synthetic customer details only."
    )
    try:
        runtime = load_review_runtime()
    except (ValueError, OSError):
        st.error("Review configuration is unavailable. Name screening remains available.")
        return
    mode = st.radio("Run mode", ["Live free model", "Offline mock"], horizontal=True)
    offline = mode == "Offline mock"
    ready = offline or runtime.live_model is not None
    if offline:
        st.warning("MOCK MODEL — a deterministic scripted policy, not an LLM.")
    else:
        st.caption(f"Live model: `{runtime.config.model}` via OpenRouter")
        st.caption(
            "Synthetic input and public fixture records are sent to OpenRouter. "
            f"This demo allows {runtime.daily_limit} live reviews per UTC day."
        )
        if not ready:
            st.warning("Live review is awaiting setup. Choose Offline mock to explore the demo.")

    with st.form("hosted_review"):
        name = st.text_input("Customer name (synthetic)", placeholder="Ivan Petrovich Testov")
        left, mid, right = st.columns(3)
        dob = left.text_input("Date of birth (optional)", placeholder="1965-02-15 or 1965")
        nationality = mid.text_input("Nationality (optional)", placeholder="Russia")
        entity_type = right.selectbox(
            "Entity type", ["unspecified", "individual", "entity", "vessel", "aircraft"]
        )
        note = st.text_area("Analyst note (optional)", height=68)
        submitted = st.form_submit_button("Draft review case", disabled=not ready)
    if submitted:
        try:
            request = ReviewRequest(
                name=name.strip(),
                date_of_birth=dob.strip() or None,
                nationality=nationality.strip() or None,
                entity_type=None if entity_type == "unspecified" else entity_type,
                analyst_note=note.strip() or None,
            )
        except ValidationError:
            st.error("Enter a customer name and keep the supplied details within the field limits.")
            return
        try:
            with st.spinner("Screening, retrieving evidence and preparing the draft…"):
                result = runtime.run(request, offline=offline)
        except HostedReviewNotReady as exc:
            st.warning(str(exc))
            return
        st.session_state["hosted_review_result"] = result.model_dump(mode="json")

    result = st.session_state.get("hosted_review_result")
    if not result:
        return
    st.divider()
    st.caption(
        f"Saved case `{result['case_id']}` · "
        f"{'MOCK — not an LLM' if result['is_mock'] else 'LIVE'} · `{result['model']}`"
    )
    if result["status"] == "drafted":
        st.success("Draft ready — pending human review. The decision rests with you.")
    elif result["status"] == "needs_information":
        question = result["information_request"]
        st.warning(f"The assistant needs: {', '.join(question['fields'])}. {question['reason']}")
    else:
        st.warning(f"No draft ({result['status']}): {result['message']}")
        if result.get("retry_after_seconds"):
            st.caption(f"Retry after {result['retry_after_seconds']:.0f} seconds.")
    draft = result.get("draft")
    if draft:
        st.subheader("Draft case")
        st.markdown(draft["summary"])
        for candidate in draft["candidates"]:
            with st.expander(
                f"{candidate['record_id']} — {candidate['assessment'].replace('_', ' ')}"
            ):
                st.markdown(candidate["rationale"])
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "evidence": item["kind"],
                                "source": f"{item['record_id']} · {item['field']}",
                                "record value": item["value"] or "—",
                                "statement": item["statement"],
                            }
                            for item in candidate["evidence"]
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
        if draft["missing_information"]:
            st.markdown("**Missing information:** " + ", ".join(draft["missing_information"]))
        st.markdown("**Suggested next steps**")
        for step in draft["suggested_next_steps"]:
            st.markdown(f"- {step}")
        st.caption("Screening audit IDs: " + ", ".join(draft["screening_ids"]))
    usage = result["usage"]
    st.caption(
        f"{usage['model_calls']} model calls · {usage['tool_calls']} tool calls · "
        f"{usage['tool_errors']} tool errors · {usage['rejected_drafts']} rejected drafts · "
        f"{usage['input_tokens'] + usage['output_tokens']} tokens · {usage['elapsed_ms']:.0f} ms"
    )
    st.subheader("Tool trace")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "step": event["step"],
                    "event": event["kind"],
                    "name": event["name"] or "",
                    "arguments": json.dumps(event["arguments"]) if event["arguments"] else "",
                    "result": event["summary"],
                    "ms": event["latency_ms"],
                }
                for event in result["trace"]
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Download case and trace",
        data=json.dumps(result, indent=2),
        file_name=f"review-{result['case_id']}.json",
        mime="application/json",
    )
    st.caption("Download to keep a copy. Hosted storage may reset after a restart or redeployment.")


def ensure_data() -> None:
    """Fetch the databases from the public HF dataset when not shipped locally
    (Streamlit Community Cloud clones only the git repo, which excludes them)."""
    for filename in ("sanctions.db", "embeddings.db"):
        target = Path("data") / filename
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            cached = hf_hub_download(DATA_REPO, filename, repo_type="dataset")
            shutil.copyfile(cached, target)


@st.cache_resource(show_spinner="Downloading sanctions data and loading the model…")
def load_engine() -> tuple[MatchingEngine, str]:
    ensure_data()
    settings = Settings()
    conn = connect(settings.database.path)
    embedder = create_embedder(settings)
    engine = MatchingEngine(conn, settings, embedder=embedder)
    conn.close()
    if embedder is not None:
        embedder.query("warm up")
    return engine, "loaded" if embedder is not None else "unavailable"


@st.cache_data(show_spinner=False)
def entity_detail(source_list: str, reference_number: str) -> dict:
    conn = connect(Settings().database.path)
    row = conn.execute(
        "SELECT raw_record FROM entities WHERE source_list = ? AND reference_number = ?",
        (source_list, reference_number),
    ).fetchone()
    conn.close()
    return json.loads(row["raw_record"]) if row else {}


st.title("🛡️ SanctionScreen")
st.caption(
    "Explainable KYC name screening against the DFAT, UN and OFAC consolidated "
    "lists — exact, phonetic, fuzzy and multilingual-embedding layers combined "
    "into one score. "
    "[Source & benchmarks](https://github.com/zr101/sanctionscreen) · "
    "demonstration only, not legal advice."
)

workspace = st.sidebar.radio(
    "Workspace",
    ["Name screening", "Review assistant"],
    index=1 if st.query_params.get("workspace") == "review" else 0,
    key="workspace",
)
if workspace == "Review assistant":
    review_page()
    st.stop()

engine, embedding_status = load_engine()

with st.sidebar:
    st.header("Parameters")
    threshold = st.slider("Score threshold", 0, 100, 75)
    max_results = st.number_input("Max results", 1, 50, 10)
    entity_type = st.selectbox(
        "Entity type filter", ["any", "individual", "entity", "vessel", "aircraft"]
    )
    st.divider()
    st.caption(f"**{len(engine.entries):,}** searchable names")
    st.caption(f"Embedding layer: **{embedding_status}**")
    for source in ("DFAT", "UN", "OFAC"):
        count = sum(1 for e in engine.entities.values() if e.source_list == source)
        st.caption(f"{source}: {count:,} entities")

cols = st.columns(len(EXAMPLES) + 1)
cols[0].markdown("**Try:**")
for col, example in zip(cols[1:], EXAMPLES, strict=True):
    if col.button(example, width="stretch"):
        st.session_state["query"] = example

name = st.text_input(
    "Name to screen",
    key="query",
    placeholder="any script, any order — e.g. Vladimr Putin",
)

if name and name.strip():
    results = engine.screen(
        name,
        threshold=float(threshold),
        max_results=int(max_results),
        entity_type=None if entity_type == "any" else entity_type,
    )
    if not results:
        st.success(f"No matches at threshold {threshold}.")
        st.stop()

    st.subheader(f"{len(results)} match(es)")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "score": r.score,
                    "matched name": r.matched_name,
                    "name type": r.matched_name_type,
                    "primary name": r.entity.primary_name,
                    "list": r.entity.source_list,
                    "reference": r.entity.reference_number,
                    "type": r.entity.entity_type,
                    "listed": r.entity.listed_date or "—",
                }
                for r in results
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "score": st.column_config.ProgressColumn(
                "score", min_value=0, max_value=100, format="%.1f"
            )
        },
    )

    st.subheader("Why did these match?")
    for r in results:
        label = (
            f"{r.score:.1f} — {r.entity.primary_name} "
            f"({r.entity.source_list} {r.entity.reference_number})"
        )
        with st.expander(label):
            left, right = st.columns([1, 2])
            with left:
                quality = f" ({r.alias_quality})" if r.alias_quality else ""
                st.markdown(
                    f"**Matched name:** {r.matched_name}  \n"
                    f"**Name type:** {r.matched_name_type}{quality}  \n"
                    f"**Layers fired:** {', '.join(r.layers_fired)}"
                )
                st.dataframe(
                    pd.DataFrame(
                        {
                            "layer": ["exact", "phonetic", "fuzzy", "embedding"],
                            "sub-score": [
                                r.layers.exact,
                                r.layers.phonetic,
                                r.layers.fuzzy,
                                r.layers.embedding,
                            ],
                        }
                    ),
                    hide_index=True,
                    column_config={
                        "sub-score": st.column_config.ProgressColumn(
                            "sub-score", min_value=0, max_value=100, format="%.1f"
                        )
                    },
                )
            with right:
                st.markdown("**Raw list record**")
                st.json(
                    entity_detail(r.entity.source_list, r.entity.reference_number),
                    expanded=False,
                )
