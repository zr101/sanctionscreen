"""Review assistant page: drafts a source-cited review case via POST /review.

Like ui/app.py this talks to the API over HTTP only. It shows which model
produced the draft (with a MOCK banner for the offline scripted model), the
draft itself, and the full tool trace.
"""

from __future__ import annotations

import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("SANCTIONSCREEN_API_URL", "http://localhost:8000")

st.set_page_config(page_title="Review assistant · SanctionScreen", page_icon="🛡️", layout="wide")
st.title("Review assistant")
st.caption(
    "A model chooses tools to screen the name, open candidate records and draft a "
    "review case. Scores come from the deterministic engine; every claim cites a "
    "record. The draft is never a decision — the reviewer decides."
)
if os.environ.get("SANCTIONSCREEN_DEMO_DATA") == "1":
    st.info("Fictional sample database — use Ivan Testov or Abdul Rahim Testman for this demo.")
try:
    configured = httpx.get(f"{API_URL}/assistant", timeout=5)
    configured.raise_for_status()
    model_info = configured.json()
    st.caption(f"Configured model: {model_info['provider']} / {model_info['model']}")
except httpx.HTTPError:
    st.warning("API unavailable. The local service will restart after a crash; retry shortly.")

with st.form("review"):
    name = st.text_input("Customer name (synthetic)", placeholder="e.g. Ivan Testov")
    left, mid, right = st.columns(3)
    dob = left.text_input("Date of birth (optional)", placeholder="1965-02-15 or 1965")
    nationality = mid.text_input("Nationality (optional)")
    entity_type = right.selectbox(
        "Entity type", ["unspecified", "individual", "entity", "vessel", "aircraft"]
    )
    note = st.text_area("Analyst note (optional, passed to the model as data)", height=68)
    submitted = st.form_submit_button("Draft review case")

if not submitted and "review_result" not in st.session_state:
    st.stop()
if submitted and not name.strip():
    st.stop()

payload: dict = {"name": name}
if dob.strip():
    payload["date_of_birth"] = dob.strip()
if nationality.strip():
    payload["nationality"] = nationality.strip()
if entity_type != "unspecified":
    payload["entity_type"] = entity_type
if note.strip():
    payload["analyst_note"] = note.strip()

if submitted:
    with st.spinner("Running the review loop…"):
        try:
            response = httpx.post(f"{API_URL}/review", json=payload, timeout=180)
        except httpx.HTTPError as exc:
            st.error(f"API request failed: {exc}")
            st.stop()
    if response.status_code != 200:
        st.error(f"API error {response.status_code}: {response.text}")
        st.stop()
    st.session_state["review_result"] = response.json()
result = st.session_state["review_result"]

if result["is_mock"]:
    st.error(
        f"MOCK MODEL — `{result['model']}` is a deterministic scripted policy, not an LLM. "
        "Configure SANCTIONSCREEN_ASSISTANT__PROVIDER / __MODEL for a live model."
    )
else:
    st.info(f"Live model: `{result['model']}` via {result['provider']}")

usage = result["usage"]
st.caption(
    f"case `{result['case_id']}` · {usage['model_calls']} model calls · "
    f"{usage['tool_calls']} tool calls · {usage['tool_errors']} tool errors · "
    f"{usage['rejected_drafts']} rejected drafts · "
    f"{usage['input_tokens'] + usage['output_tokens']} tokens · {usage['elapsed_ms']:.0f} ms"
)

status = result["status"]
if status == "drafted":
    st.success("Draft ready — pending human review. The decision rests with you.")
elif status == "needs_information":
    req = result["information_request"]
    st.warning(f"The assistant needs: {', '.join(req['fields'])}. {req['reason']}")
else:
    st.warning(f"No draft ({status}): {result['message']}")
    if result.get("retry_after_seconds"):
        st.caption(f"Retry this review after {result['retry_after_seconds']:.0f} seconds.")

draft = result.get("draft")
if draft:
    st.subheader("Draft case")
    st.markdown(draft["summary"])
    for cand in draft["candidates"]:
        with st.expander(f"{cand['record_id']} — {cand['assessment'].replace('_', ' ')}"):
            st.markdown(cand["rationale"])
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "evidence": e["kind"],
                            "source": f"{e['record_id']} · {e['field']}",
                            "record value": e["value"] or "—",
                            "statement": e["statement"],
                        }
                        for e in cand["evidence"]
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
    st.caption("Screening audit ids: " + ", ".join(f"`{s}`" for s in draft["screening_ids"]))

st.subheader("Tool trace")
st.dataframe(
    pd.DataFrame(
        [
            {
                "step": e["step"],
                "event": e["kind"],
                "name": e["name"] or "",
                "arguments": "" if e["arguments"] is None else str(e["arguments"])[:200],
                "result": e["summary"],
                "ms": e["latency_ms"],
                "tokens": e["input_tokens"] + e["output_tokens"],
            }
            for e in result["trace"]
        ]
    ),
    hide_index=True,
    width="stretch",
)
