"""Verify local API/UI health; --live drives the actual review UI and persistence.

The live check sends only fictional fixture data to the configured provider.
It requires the optional ui dependencies and a running local demo service.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--restart-check",
        action="store_true",
        help="terminate this demo API and verify launchd recovery",
    )
    args = parser.parse_args()
    api = "http://127.0.0.1:8000"
    with httpx.Client(timeout=120) as client:
        for url in (api + "/health", "http://127.0.0.1:8501/_stcore/health"):
            client.get(url).raise_for_status()
        model = client.get(api + "/assistant").json()
        print(f"Healthy: {model['provider']} / {model['model']}")
        if args.restart_check:
            job = f"gui/{os.getuid()}/local.sanctionscreen.api"
            saved_check = ROOT / ".runtime/live-check.json"
            previous_case = None
            if saved_check.is_file():
                saved_id = json.loads(saved_check.read_text())["case_id"]
                saved_response = client.get(api + "/review/" + saved_id)
                saved_response.raise_for_status()
                previous_case = saved_response.json()

            def pid() -> int | None:
                status = subprocess.run(["launchctl", "print", job], capture_output=True, text=True)
                found = re.search(r"\bpid = (\d+)", status.stdout)
                return int(found.group(1)) if found else None

            old_pid = pid()
            assert old_pid, "demo API launchd job is not running"
            print(f"Checking crash recovery for demo API PID {old_pid}", flush=True)
            subprocess.run(["launchctl", "kill", "SIGKILL", job], check=True, capture_output=True)
            started = time.monotonic()
            recovered = None
            while time.monotonic() - started < 45:
                time.sleep(1)
                new_pid = pid()
                try:
                    response = client.get(api + "/health", timeout=2)
                    if new_pid and new_pid != old_pid and response.status_code == 200:
                        recovered = {
                            "old_pid": old_pid,
                            "new_pid": new_pid,
                            "recovery_seconds": round(time.monotonic() - started, 2),
                        }
                        break
                except httpx.HTTPError:
                    pass
            assert recovered, "launchd did not restore API health within 45 seconds"
            if previous_case:
                restored = client.get(api + "/review/" + previous_case["case_id"])
                restored.raise_for_status()
                assert restored.json() == previous_case, "stored case changed across restart"
                recovered["case_preserved"] = True
            (ROOT / ".runtime" / "restart-check.json").write_text(
                json.dumps(recovered, indent=2) + "\n"
            )
            print(json.dumps(recovered, indent=2), flush=True)
        if not args.live:
            return 0
        from streamlit.testing.v1 import AppTest

        os.environ["SANCTIONSCREEN_API_URL"] = api
        os.environ["SANCTIONSCREEN_DEMO_DATA"] = "1"
        app = AppTest.from_file(str(ROOT / "ui/pages/2_Review_assistant.py"))
        app.run(timeout=15)
        assert not app.exception, app.exception
        app.text_input[0].input("Ivan Petrovich Testov")
        app.text_input[1].input("1965-02-15")
        app.text_input[2].input("Russia")
        before = client.post(api + "/screen", json={"name": "Ivan Petrovich Testov"})
        before.raise_for_status()
        app.button[0].click().run(timeout=120)
        assert not app.exception, app.exception
        result = app.session_state["review_result"]
        assert result["status"] == "drafted", result["message"]
        assert not result["is_mock"], "live check requires a real model"
        assert result["draft"]["status"] == "pending_human_review"
        assert any("Draft ready" in element.value for element in app.success)
        names = [event["name"] for event in result["trace"] if event["kind"] == "tool_call"]
        assert {"screen_name", "get_record", "draft_case"} <= set(names), names
        stored = client.get(api + "/review/" + result["case_id"])
        stored.raise_for_status()
        assert stored.json() == result, "stored draft/trace differs from UI result"
        after = client.post(api + "/screen", json={"name": "Ivan Petrovich Testov"})
        after.raise_for_status()
        assert before.json()["matches"] == after.json()["matches"], "screening behavior changed"
        app.run(timeout=15)
        assert app.session_state["review_result"] == result, "draft lost on UI rerun"
        evidence = {
            "date": dt.datetime.now(dt.UTC).isoformat(),
            "model": model,
            "case_id": result["case_id"],
            "status": result["status"],
            "tool_calls": names,
            "persisted": True,
            "ui_rerun_preserved": True,
            "screening_unchanged": True,
            "usage": result["usage"],
        }
        (ROOT / ".runtime" / "live-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
