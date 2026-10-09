"""Hosted demo exercises the existing agent without a separate API process."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sanctionscreen.db import connect
from sanctionscreen.review import store
from sanctionscreen.review.hosted import HostedReviewNotReady, HostedReviewRuntime
from sanctionscreen.review.models import MockReviewModel
from sanctionscreen.review.schemas import ReviewRequest

ROOT = Path(__file__).resolve().parents[1]
REQUEST = ReviewRequest(
    name="Ivan Petrovich Testov", date_of_birth="1965-02-15", nationality="Russia"
)


class FakeLiveModel(MockReviewModel):
    provider = "openrouter"
    model = "nvidia/nemotron-3-super-120b-a12b:free"
    is_mock = False


@pytest.fixture
def runtime(tmp_path: Path) -> HostedReviewRuntime:
    (tmp_path / "config").mkdir()
    shutil.copyfile(ROOT / "config/openrouter-demo.toml", tmp_path / "config/openrouter-demo.toml")
    shutil.copytree(ROOT / "data/cache/samples", tmp_path / "data/cache/samples")
    return HostedReviewRuntime(tmp_path, "", daily_limit=1)


def test_live_without_key_never_falls_back(runtime):
    with pytest.raises(HostedReviewNotReady, match="not configured"):
        runtime.run(REQUEST)
    assert not runtime.path.with_name("sanctions.db").exists()


def test_explicit_offline_run_is_labelled_and_persisted(runtime):
    result = runtime.run(REQUEST, offline=True)
    assert result.is_mock
    assert result.status == "drafted"
    assert result.draft.status == "pending_human_review"
    conn = connect(runtime.path)
    try:
        assert store.get_case(conn, result.case_id) == result
    finally:
        conn.close()


def test_daily_live_cap_survives_runtime_recreation_and_mock_still_works(runtime):
    runtime.live_model = FakeLiveModel()
    result = runtime.run(REQUEST)
    assert not result.is_mock
    rebuilt = HostedReviewRuntime(runtime.path.parents[1], "", daily_limit=1)
    rebuilt.live_model = FakeLiveModel()
    with pytest.raises(HostedReviewNotReady, match="daily live-review allowance"):
        rebuilt.run(REQUEST)
    assert rebuilt.run(REQUEST, offline=True).is_mock


def test_busy_slot_preserves_screening_and_recovers(runtime):
    runtime._slot.acquire()
    try:
        with pytest.raises(HostedReviewNotReady, match="Another review"):
            runtime.run(REQUEST, offline=True)
        assert runtime.engine.screen(REQUEST.name)
    finally:
        runtime._slot.release()
    assert runtime.run(REQUEST, offline=True).status == "drafted"


def test_slot_is_released_after_unexpected_failure(runtime, monkeypatch):
    from sanctionscreen.review.agent import ReviewAgent

    original = ReviewAgent.run

    def fail(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(ReviewAgent, "run", fail)
    with pytest.raises(RuntimeError, match="injected"):
        runtime.run(REQUEST, offline=True)
    monkeypatch.setattr(ReviewAgent, "run", original)
    assert runtime.run(REQUEST, offline=True).status == "drafted"


@pytest.fixture
def hosted_ui(runtime, monkeypatch):
    st = pytest.importorskip("streamlit")
    pytest.importorskip("huggingface_hub")
    from streamlit.testing.v1 import AppTest

    from sanctionscreen.review import hosted

    st.cache_resource.clear()
    monkeypatch.setattr(st, "secrets", {})
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SANCTIONSCREEN_ASSISTANT__API_KEY", raising=False)
    monkeypatch.setattr(hosted, "HostedReviewRuntime", lambda *args, **kwargs: runtime)
    app = AppTest.from_file(str(ROOT / "demo/app.py"))
    app.query_params["workspace"] = "review"
    yield app
    st.cache_resource.clear()


def test_hosted_review_opens_without_loading_official_data(hosted_ui):
    hosted_ui.run(timeout=15)
    assert not hosted_ui.exception
    assert any(item.value == "Review assistant" for item in hosted_ui.header)
    button = next(item for item in hosted_ui.button if item.label == "Draft review case")
    assert button.disabled
    assert any("awaiting setup" in item.value for item in hosted_ui.warning)


def test_hosted_ui_explicit_mock_and_rerun_retains_case(hosted_ui):
    hosted_ui.run(timeout=15)
    mode = next(item for item in hosted_ui.radio if item.label == "Run mode")
    mode.set_value("Offline mock").run(timeout=15)
    hosted_ui.text_input[0].input(REQUEST.name)
    hosted_ui.text_input[1].input(REQUEST.date_of_birth)
    hosted_ui.text_input[2].input(REQUEST.nationality)
    next(item for item in hosted_ui.button if item.label == "Draft review case").click().run()
    assert not hosted_ui.exception
    result = hosted_ui.session_state["hosted_review_result"]
    assert result["is_mock"]
    assert result["status"] == "drafted"
    assert any("pending human review" in item.value for item in hosted_ui.success)
    assert any("MOCK" in item.value for item in hosted_ui.warning)
    hosted_ui.run(timeout=15)
    assert hosted_ui.session_state["hosted_review_result"] == result


def test_hosted_ui_live_path_is_labelled_and_has_tool_trace(hosted_ui, runtime):
    runtime.live_model = FakeLiveModel()
    hosted_ui.run(timeout=15)
    hosted_ui.text_input[0].input(REQUEST.name)
    hosted_ui.text_input[1].input(REQUEST.date_of_birth)
    hosted_ui.text_input[2].input(REQUEST.nationality)
    next(item for item in hosted_ui.button if item.label == "Draft review case").click().run()
    assert not hosted_ui.exception
    result = hosted_ui.session_state["hosted_review_result"]
    assert not result["is_mock"]
    tools = {event["name"] for event in result["trace"] if event["kind"] == "tool_call"}
    assert {"screen_name", "get_record", "draft_case"} <= tools
    assert not any("awaiting setup" in item.value for item in hosted_ui.warning)


def test_original_screening_view_still_works(hosted_ui, runtime, monkeypatch):
    import huggingface_hub

    from sanctionscreen import config
    from sanctionscreen.matching import embedding

    settings = config.Settings()
    settings.database.path = runtime.path
    monkeypatch.chdir(runtime.path.parents[1])
    monkeypatch.setattr(config, "Settings", lambda: settings)
    monkeypatch.setattr(embedding, "create_embedder", lambda settings: None)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda *args, **kwargs: runtime.path)
    hosted_ui.query_params.clear()
    hosted_ui.run(timeout=15)
    assert not hosted_ui.exception
    hosted_ui.text_input[0].input(REQUEST.name).run(timeout=15)
    assert not hosted_ui.exception
    assert hosted_ui.dataframe
    assert any("match(es)" in item.value for item in hosted_ui.subheader)
    assert any("TESTOV" in item.label for item in hosted_ui.expander)
