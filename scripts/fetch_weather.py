#!/usr/bin/env python3
"""Fetch a Caiyun v2.6 forecast for the request in weather/trigger.json."""

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRIGGER_PATH = ROOT / "weather" / "trigger.json"
RESULT_PATH = ROOT / "weather" / "weather.json"


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_trigger() -> dict:
    try:
        with TRIGGER_PATH.open(encoding="utf-8") as trigger_file:
            trigger = json.load(trigger_file)
    except (OSError, json.JSONDecodeError):
        fail("weather/trigger.json is missing or is not valid JSON.")

    if not isinstance(trigger, dict):
        fail("weather/trigger.json must contain a JSON object.")

    if not isinstance(trigger.get("request_id"), str) or not trigger["request_id"].strip():
        fail("weather/trigger.json must include a non-empty request_id.")

    if not isinstance(trigger.get("location"), str) or not trigger["location"].strip():
        fail("weather/trigger.json must include a non-empty location.")

    for name, lower, upper in (("longitude", -180, 180), ("latitude", -90, 90)):
        value = trigger.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper:
            fail(f"weather/trigger.json must include a valid {name}.")

    return trigger


def fetch_weather(trigger: dict) -> dict:
    app_key = os.environ.get("CAIYUN_APP_KEY", "")
    app_secret = os.environ.get("CAIYUN_APP_SECRET", "")
    if not app_key or not app_secret:
        fail("Set the CAIYUN_APP_KEY and CAIYUN_APP_SECRET repository secrets.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", app_key):
        fail("CAIYUN_APP_KEY contains unsupported characters.")

    longitude = f"{float(trigger['longitude']):.4f}"
    latitude = f"{float(trigger['latitude']):.4f}"
    path = f"/v2.6/{app_key}/{longitude},{latitude}/weather"
    params = {
        "alert": "true",
        "dailysteps": "15",
        "hourlysteps": "72",
    }
    query = urllib.parse.urlencode(sorted(params.items()))
    nonce = str(uuid.uuid4())
    timestamp = str(int(time.time()))
    string_to_sign = ":".join(("GET", path, query, app_key, nonce, timestamp))
    signature = base64.urlsafe_b64encode(
        hmac.new(app_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")

    request = urllib.request.Request(
        f"https://api.caiyunapp.com{path}?{query}",
        headers={
            "User-Agent": "chatgpt-weather/1.0",
            "x-cy-nonce": nonce,
            "x-cy-timestamp": timestamp,
            "x-cy-signature": signature,
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        fail(f"Caiyun Weather API returned HTTP {error.code}.")
    except urllib.error.URLError:
        fail("Could not connect to the Caiyun Weather API.")
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("Caiyun Weather API returned an invalid JSON response.")

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        fail("Caiyun Weather API returned a failed response.")

    return payload


def main() -> None:
    trigger = load_trigger()
    forecast = fetch_weather(trigger)
    result = {
        "request_id": trigger["request_id"],
        "query": trigger.get("query"),
        "location": trigger["location"],
        "coordinates": {
            "longitude": trigger["longitude"],
            "latitude": trigger["latitude"],
        },
        "requested_at": trigger.get("requested_at"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "Caiyun Weather API v2.6",
        "data": forecast,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated weather/weather.json for request {trigger['request_id']}.")


if __name__ == "__main__":
    main()
