"""Streamlit tester UI for SanctionScreen.

Talks to the FastAPI service over HTTP only (no imports of service
internals), so it exercises exactly what an integrating system would see.

Run:  uv run streamlit run ui/app.py
Env:  SANCTIONSCREEN_API_URL (default http://localhost:8000)
"""

from __future__ import annotations

import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("SANCTIONSCREEN_API_URL", "http://localhost:8000")

st.set_page_config(page_title="SanctionScreen", page_icon="🛡️", layout="wide")
st.title("🛡️ SanctionScreen")
st.caption(
    "KYC name screening against the DFAT, UN and OFAC consolidated lists — "
    "layered, explainable matching. Demonstration tool, not legal advice."
)


@st.cache_data(ttl=60)
def get_health() -> dict | None:
    try:
        return httpx.get(f"{API_URL}/health", timeout=10).json()
    except httpx.HTTPError:
        return None


with st.sidebar:
    st.header("Parameters")
    threshold = st.slider(
        "Score threshold",
        min_value=0,
        max_value=100,
        value=75,
        help="Minimum combined score for a match to be reported. Lower = more "
        "recall (more review work); higher = fewer false positives.",
    )
    max_results = st.number_input("Max results", min_value=1, max_value=100, value=10)
    entity_type = st.selectbox(
        "Entity type filter",
        options=["any", "individual", "entity", "vessel", "aircraft"],
    )

    st.divider()
    health = get_health()
    if health is None:
        st.error(f"API unreachable at {API_URL}")
    else:
        st.success(f"API ok — embeddings: {health['embedding_layer']}")
        for info in health["lists"]:
            st.caption(
                f"**{info['source_list']}** · {info['entity_count']:,} entities · "
                f"refreshed {info['last_refreshed'] or 'never'}"
            )

name = st.text_input(
    "Name to screen",
    placeholder="e.g. Usama bin Ladin — any script, any order",
)

if name.strip():
    payload: dict = {
        "name": name,
        "threshold": threshold,
        "max_results": int(max_results),
    }
    if entity_type != "any":
        payload["entity_type"] = entity_type
    try:
        response = httpx.post(f"{API_URL}/screen", json=payload, timeout=60)
    except httpx.HTTPError as exc:
        st.error(f"API request failed: {exc}")
        st.stop()
    if response.status_code != 200:
        st.error(f"API error {response.status_code}: {response.text}")
        st.stop()
    body = response.json()

    st.caption(f"screening_id: `{body['screening_id']}` (persisted to the audit trail)")
    if body["match_count"] == 0:
        st.success(f"No matches at threshold {threshold:.0f}.")
        st.stop()

    st.subheader(f"{body['match_count']} match(es)")
    matches = body["matches"]
    table = pd.DataFrame(
        [
            {
                "score": m["score"],
                "matched name": m["matched_name"],
                "name type": m["matched_name_type"],
                "primary name": m["entity"]["primary_name"],
                "list": m["entity"]["source_list"],
                "reference": m["entity"]["reference_number"],
                "type": m["entity"]["entity_type"],
                "listed": m["entity"]["listed_date"] or "—",
            }
            for m in matches
        ]
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "score": st.column_config.ProgressColumn(
                "score", min_value=0, max_value=100, format="%.1f"
            )
        },
    )

    st.subheader("Why did these match?")
    for m in matches:
        entity = m["entity"]
        label = (
            f"{m['score']:.1f} — {entity['primary_name']} "
            f"({entity['source_list']} {entity['reference_number']})"
        )
        with st.expander(label):
            left, right = st.columns([1, 2])
            with left:
                st.markdown(
                    f"**Matched name:** {m['matched_name']}  \n"
                    f"**Name type:** {m['matched_name_type']}"
                    + (f" ({m['alias_quality']})" if m["alias_quality"] else "")
                    + f"  \n**Layers fired:** {', '.join(m['layers_fired'])}"
                )
                layer_df = pd.DataFrame(
                    {
                        "layer": list(m["layers"].keys()),
                        "sub-score": list(m["layers"].values()),
                    }
                )
                st.dataframe(
                    layer_df,
                    hide_index=True,
                    column_config={
                        "sub-score": st.column_config.ProgressColumn(
                            "sub-score", min_value=0, max_value=100, format="%.1f"
                        )
                    },
                )
            with right:
                st.markdown("**Raw list record**")
                try:
                    detail = httpx.get(
                        f"{API_URL}/entity/{entity['source_list']}/{entity['reference_number']}",
                        timeout=10,
                    ).json()
                    st.json(detail, expanded=False)
                except httpx.HTTPError as exc:
                    st.warning(f"could not load record: {exc}")
