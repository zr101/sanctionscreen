"""Manage the local-only portfolio demo with macOS launchd crash recovery.

prepare: validate config and seed the separate fictional demo DB.
install: register API + UI as login services with KeepAlive; never publish.
status: inspect launchd jobs and HTTP health.
stop: unload this repository's jobs without deleting data or credentials.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from sanctionscreen.config import Settings  # noqa: E402
from sanctionscreen.review.demo import build_demo_db  # noqa: E402
from sanctionscreen.review.models import build_model  # noqa: E402

LABEL = "local.sanctionscreen"
SERVICES = ("api", "ui")


def definitions(config: Path) -> dict[str, dict]:
    runtime = ROOT / ".runtime"
    env = {
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONUNBUFFERED": "1",
        "SANCTIONSCREEN_CONFIG": str(config),
        "SANCTIONSCREEN_DEMO_DATA": "1",
        "SANCTIONSCREEN_API_URL": "http://127.0.0.1:8000",
    }
    commands = {
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "sanctionscreen.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        "ui": [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "ui/app.py"),
            "--server.address=127.0.0.1",
            "--server.port=8501",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ],
    }
    return {
        name: {
            "Label": f"{LABEL}.{name}",
            "ProgramArguments": commands[name],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "ExitTimeOut": 15,
            "StandardOutPath": str(runtime / f"{name}.log"),
            "StandardErrorPath": str(runtime / f"{name}.log"),
        }
        for name in SERVICES
    }


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "install", "status", "stop"])
    parser.add_argument("--config", default="config/openrouter-demo.toml")
    args = parser.parse_args()
    os.chdir(ROOT)
    domain = f"gui/{os.getuid()}"
    config = (ROOT / args.config).resolve()
    if args.command in {"prepare", "install"}:
        if not config.is_file():
            parser.error(f"config not found: {config}")
        os.environ["SANCTIONSCREEN_CONFIG"] = str(config)
        settings = Settings()
        model = build_model(settings.assistant)
        # This manager is deliberately for the fictional local demo only.
        if settings.database.path.resolve() != ROOT / "data/review-demo.db":
            parser.error("demo service requires database.path = data/review-demo.db")
        if not settings.database.path.exists():
            build_demo_db(settings.database.path)
        (ROOT / ".runtime").mkdir(exist_ok=True)
        print(f"Fictional demo ready: {model.provider} / {model.model}")
        if args.command == "prepare":
            return 0
        if sys.platform != "darwin":
            parser.error("install uses macOS launchd; use Docker Compose on Linux")
        agents = Path.home() / "Library/LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        for name, definition in definitions(config).items():
            path = agents / f"{LABEL}.{name}.plist"
            old = None
            if path.exists():
                old = plistlib.loads(path.read_bytes())
                if old.get("WorkingDirectory") != str(ROOT):
                    parser.error(f"existing service belongs to another directory: {path}")
            path.write_bytes(plistlib.dumps(definition))
            path.chmod(0o600)
            job = f"{domain}/{LABEL}.{name}"
            if launchctl("print", job).returncode == 0:
                if old == definition:
                    restarted = launchctl("kickstart", "-k", job)
                    if restarted.returncode:
                        print(restarted.stderr.strip(), file=sys.stderr)
                        return 1
                    print(f"Restarted {name}; existing login service retained")
                    continue
                stopped = launchctl("bootout", job)
                if stopped.returncode:
                    print(stopped.stderr.strip(), file=sys.stderr)
                    return 1
            started = launchctl("bootstrap", domain, str(path))
            # launchd may keep a just-unloaded label briefly while its process
            # exits. Retry that specific registration; never start another job.
            for _ in range(5):
                if started.returncode == 0:
                    break
                time.sleep(1)
                started = launchctl("bootstrap", domain, str(path))
            if started.returncode:
                print(started.stderr.strip(), file=sys.stderr)
                return 1
            print(f"Registered {name}; logs: {ROOT / '.runtime' / (name + '.log')}")
        print("Local UI: http://127.0.0.1:8501/Review_assistant")
        return 0
    for name in SERVICES:
        job = f"{domain}/{LABEL}.{name}"
        result = launchctl("bootout" if args.command == "stop" else "print", job)
        if args.command == "status":
            lines = [
                line.strip()
                for line in result.stdout.splitlines()
                if any(tag in line for tag in ("state =", "pid =", "last exit code ="))
            ]
            print(f"{name}: " + (", ".join(lines) if result.returncode == 0 else "not registered"))
        else:
            print(f"{name}: {'stopped' if result.returncode == 0 else 'not running'}")
    if args.command == "status":
        healthy = True
        for name, url in (
            ("api", "http://127.0.0.1:8000/health"),
            ("ui", "http://127.0.0.1:8501/_stcore/health"),
        ):
            try:
                response = httpx.get(url, timeout=5)
                response.raise_for_status()
                print(f"{name} HTTP: {response.status_code}")
            except httpx.HTTPError:
                print(f"{name} HTTP: unavailable")
                healthy = False
        return 0 if healthy else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
