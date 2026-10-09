"""Single-process review runtime for the existing hosted Streamlit demo.

Uses fictional fixtures separately from the official-list screening engine.
One cached runtime shares the live client's quota cooldown across visitors.
Cloud disk persistence lasts only as long as the hosting instance.
"""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path

from pydantic import SecretStr

from sanctionscreen.config import AssistantConfig, DatabaseConfig, EmbeddingConfig, Settings
from sanctionscreen.db import connect
from sanctionscreen.matching.engine import MatchingEngine
from sanctionscreen.review.agent import ReviewAgent
from sanctionscreen.review.demo import build_demo_db
from sanctionscreen.review.models import MockReviewModel, build_model
from sanctionscreen.review.schemas import ReviewRequest, ReviewResult


class HostedReviewNotReady(RuntimeError):
    """No inference started: missing configuration, occupied slot or daily cap."""


class HostedReviewRuntime:
    def __init__(self, root: Path, api_key: str, *, daily_limit: int = 25) -> None:
        if not 1 <= daily_limit <= 1000:
            raise ValueError("daily_limit must be between 1 and 1000")
        config = tomllib.loads((root / "config/openrouter-demo.toml").read_text())["assistant"]
        self.config = AssistantConfig(**config, api_key=SecretStr(api_key))
        if self.config.provider != "openrouter" or not self.config.model.endswith(":free"):
            raise ValueError("The hosted demo requires an explicit free OpenRouter model")
        self.live_model = build_model(self.config) if api_key.strip() else None
        self.daily_limit = daily_limit
        self.path = root / "data/hosted-review.db"
        if not self.path.exists():
            build_demo_db(self.path, samples=root / "data/cache/samples")
        settings = Settings(
            database=DatabaseConfig(path=self.path), embedding=EmbeddingConfig(enabled=False)
        )
        conn = connect(self.path)
        try:
            self.engine = MatchingEngine(conn, settings)
        finally:
            conn.close()
        self._slot = threading.BoundedSemaphore(1)

    def run(self, request: ReviewRequest, *, offline: bool = False) -> ReviewResult:
        if not offline and self.live_model is None:
            raise HostedReviewNotReady("The live model is not configured. Try Offline mock.")
        if not self._slot.acquire(blocking=False):
            raise HostedReviewNotReady("Another review is running. Retry shortly.")
        try:
            if not offline:
                conn = connect(self.path)
                try:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM review_cases "
                        "WHERE is_mock = 0 AND created_at >= date('now')"
                    ).fetchone()[0]
                finally:
                    conn.close()
                if count >= self.daily_limit:
                    raise HostedReviewNotReady(
                        "The demo's daily live-review allowance is used. "
                        "Try Offline mock or return after midnight UTC."
                    )
            model = MockReviewModel() if offline else self.live_model
            assert model is not None
            return ReviewAgent(self.engine, lambda: connect(self.path), model, self.config).run(
                request
            )
        finally:
            self._slot.release()
