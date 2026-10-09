"""List live free tool-calling models, or inspect OpenRouter quota without printing keys."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sanctionscreen.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quota", action="store_true")
    args = parser.parse_args()
    headers = {}
    if args.quota:
        key = Settings().assistant.api_key.get_secret_value() or os.getenv("OPENROUTER_API_KEY")
        if not key:
            parser.error("set OPENROUTER_API_KEY or the assistant API key in .env.local")
        headers["Authorization"] = f"Bearer {key}"
    url = "https://openrouter.ai/api/v1/" + ("key" if args.quota else "models")
    try:
        response = httpx.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()["data"]
    except (httpx.HTTPError, ValueError, KeyError):
        print("OpenRouter catalog/quota request failed", file=sys.stderr)
        return 1
    if args.quota:
        print(
            json.dumps(
                {
                    k: data.get(k)
                    for k in ("is_free_tier", "free_model_daily_requests", "usage_daily")
                },
                indent=2,
            )
        )
    else:
        for model in data:
            if (
                model["id"].endswith(":free")
                and "tools" in model.get("supported_parameters", [])
                and float(model["pricing"]["prompt"]) == 0
                and float(model["pricing"]["completion"]) == 0
            ):
                print(f"{model['id']}  (context {model['context_length']:,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
