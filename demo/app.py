"""SanctionScreen — Hugging Face Spaces demo.

Unlike ui/app.py (which talks to the FastAPI service over HTTP), this Space
imports the matching engine directly so the whole demo runs in one free
container. The sanctions database and precomputed embedding vectors ship
with the Space; the engine builds its in-memory index once per process.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st
from huggingface_hub import hf_hub_download

from sanctionscreen.config import Settings
from sanctionscreen.db import connect
from sanctionscreen.matching.embedding import create_embedder
from sanctionscreen.matching.engine import MatchingEngine

DATA_REPO = "zaeemr/sanctionscreen-data"

EXAMPLES = [
    "Usama bin Ladin",
    "Laden Osama bin",
    "Vladimr Putin",
    "Владимир Путин",
    "Haji Abdul Manan",
]

st.set_page_config(page_title="SanctionScreen", page_icon="🛡️", layout="wide")


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


engine, embedding_status = load_engine()

st.title("🛡️ SanctionScreen")
st.caption(
    "Explainable KYC name screening against the DFAT, UN and OFAC consolidated "
    "lists — exact, phonetic, fuzzy and multilingual-embedding layers combined "
    "into one score. "
    "[Source & benchmarks](https://github.com/zr101/sanctionscreen) · "
    "demonstration only, not legal advice."
)

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
    placeholder="any script, any order — e.g. Usama bin Ladin",
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
