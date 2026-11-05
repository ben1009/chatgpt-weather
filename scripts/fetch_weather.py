#!/usr/bin/env python3
"""Fetch a compact Caiyun v2.6 forecast for weather/trigger.json."""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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

    request_id = trigger.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", request_id):
        fail("request_id must be 1-128 ASCII letters, digits, dots, underscores, or hyphens.")

    location = trigger.get("location")
    if not isinstance(location, str) or not location.strip() or len(location) > 200:
        fail("location must be a non-empty string of at most 200 characters.")

    query = trigger.get("query")
    if query is not None and (not isinstance(query, str) or len(query) > 1000):
        fail("query must be a string of at most 1000 characters.")

    requested_at = trigger.get("requested_at")
    if requested_at is not None and (not isinstance(requested_at, str) or len(requested_at) > 100):
        fail("requested_at must be a string of at most 100 characters.")

    for name, lower, upper in (("longitude", -180, 180), ("latitude", -90, 90)):
        value = trigger.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not lower <= value <= upper
            or not math.isfinite(value)
        ):
            fail(f"weather/trigger.json must include a valid {name}.")

    return trigger


def timestamp_key(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def is_number(value) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        or isinstance(value, float) and math.isfinite(value)
    )


def index_series(block: dict, name: str, time_field: str) -> dict:
    entries = block.get(name, [])
    if not isinstance(entries, list):
        return {}
    indexed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(time_field), str):
            continue
        try:
            timestamp_key(entry[time_field])
        except (ValueError, OverflowError):
            continue
        indexed[entry[time_field]] = entry
    return indexed


def compact_series(
    block: dict,
    time_field: str,
    series_names: tuple,
    value_only_names: tuple = (),
) -> list:
    indexed = {name: index_series(block, name, time_field) for name in series_names}
    times = sorted(
        {timestamp for name in series_names for timestamp in indexed[name]},
        key=timestamp_key,
    )

    compact = []
    for timestamp in times:
        item = {time_field: timestamp}
        for name in series_names:
            entry = indexed[name].get(timestamp)
            if not entry:
                continue
            if name in value_only_names:
                value = entry.get("value")
                if name.startswith("skycon"):
                    if isinstance(value, str) and value:
                        item[name] = value
                elif is_number(value):
                    item[name] = value
            else:
                values = {
                    key: value for key, value in entry.items()
                    if key != time_field and is_number(value)
                }
                if values:
                    item[name] = values
        if len(item) > 1:
            compact.append(item)
    return compact


def section_source(result: dict, name: str) -> tuple:
    source = result.get(name)
    if not isinstance(source, dict):
        return {}, {"status": "missing", "error": "Provider did not return this section."}
    info = {"status": source.get("status", "missing")}
    if info["status"] != "ok":
        message = str(source.get("error") or "Provider did not return a usable section.")
        for key in ("CAIYUN_APP_KEY", "CAIYUN_APP_SECRET"):
            credential = os.environ.get(key)
            if credential:
                message = message.replace(credential, "[redacted]")
        info["error"] = message[:300]
        return {}, info
    return source, info


def precipitation_coverage(records: list, time_field: str, timezone_name: str | None = None) -> dict:
    times = [item[time_field] for item in records if item.get("precipitation")]
    if time_field == "date":
        key = lambda value: datetime.fromisoformat(value).date().toordinal()
        format_missing = lambda value: date.fromordinal(value).isoformat()
        step = 1
    else:
        key = timestamp_key
        local_timezone = (
            datetime.fromisoformat(records[0][time_field]).tzinfo if records else None
        ) or timezone.utc
        if timezone_name:
            try:
                local_timezone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                pass
        format_missing = lambda value: datetime.fromtimestamp(value, local_timezone).isoformat(timespec="minutes")
        step = 3600
    labels = {key(item[time_field]): item[time_field] for item in records}
    rain_keys = {key(value) for value in times}
    expected = set(labels)
    cadence_valid = True
    if labels:
        first, last = min(labels), max(labels)
        slots = int((last - first) // step) + 1
        # A malformed upstream date must not create an unbounded gap list.
        cadence_valid = slots <= 1000 and all(
            abs((value - first) / step - round((value - first) / step)) < 1e-9
            for value in labels
        )
        if slots <= 1000:
            expected.update(first + step * offset for offset in range(slots))
    return {
        "start": times[0] if times else None,
        "end": times[-1] if times else None,
        "entries": len(times),
        "missing_times": [labels.get(value) or format_missing(value) for value in sorted(expected - rain_keys)],
        "cadence_valid": cadence_valid,
    }


def compact_forecast(payload: dict) -> dict:
    result = payload["result"]
    daily_source, daily_info = section_source(result, "daily")
    hourly_source, hourly_info = section_source(result, "hourly")

    daily = compact_series(
        daily_source,
        "date",
        (
            "precipitation",
            "precipitation_08h_20h",
            "precipitation_20h_32h",
            "temperature",
            "temperature_08h_20h",
            "temperature_20h_32h",
            "skycon",
            "skycon_08h_20h",
            "skycon_20h_32h",
        ),
        ("skycon", "skycon_08h_20h", "skycon_20h_32h"),
    )
    hourly = compact_series(
        hourly_source,
        "datetime",
        ("precipitation", "temperature", "apparent_temperature", "skycon"),
        ("temperature", "apparent_temperature", "skycon"),
    )

    if not daily and not hourly:
        fail("Caiyun returned no usable daily or hourly forecast.")

    realtime_source, realtime_info = section_source(result, "realtime")
    alert_source, alert_info = section_source(result, "alert")
    current = {
        name: realtime_source[name]
        for name in ("temperature", "apparent_temperature", "humidity", "precipitation", "skycon", "wind")
        if name in realtime_source
    }

    daily_dates = [item["date"] for item in daily]
    hourly_times = [item["datetime"] for item in hourly]
    daily_rain = precipitation_coverage(daily, "date", payload.get("timezone"))
    hourly_rain = precipitation_coverage(hourly, "datetime", payload.get("timezone"))
    daily_info["precipitation_complete"] = bool(daily) and daily_rain["cadence_valid"] and not daily_rain["missing_times"]
    hourly_info["precipitation_complete"] = bool(hourly) and hourly_rain["cadence_valid"] and not hourly_rain["missing_times"]
    for records, info in ((daily, daily_info), (hourly, hourly_info)):
        if not records and info["status"] == "ok":
            info.update(status="empty", error="Provider returned no usable forecast values.")
    partial = (
        any(info["status"] != "ok" for info in (daily_info, hourly_info, realtime_info))
        or not daily_info["precipitation_complete"]
        or not hourly_info["precipitation_complete"]
    )
    return {
        "status": "partial" if partial else "ok",
        "sections": {
            "daily": daily_info,
            "hourly": hourly_info,
            "realtime": realtime_info,
            "alert": alert_info,
        },
        "api_version": payload.get("api_version"),
        "timezone": payload.get("timezone"),
        "unit": payload.get("unit"),
        "coverage": {
            "daily_start": daily_dates[0] if daily_dates else None,
            "daily_end": daily_dates[-1] if daily_dates else None,
            "daily_entries": len(daily),
            "hourly_start": hourly_times[0] if hourly_times else None,
            "hourly_end": hourly_times[-1] if hourly_times else None,
            "hourly_entries": len(hourly),
            "precipitation": {"daily": daily_rain, "hourly": hourly_rain},
        },
        "summary": result.get("forecast_keypoint"),
        "hourly_summary": hourly_source.get("description"),
        "current": current,
        "alerts": alert_source if alert_info["status"] == "ok" else None,
        "daily": daily,
        "hourly": hourly,
    }


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
    if not isinstance(payload.get("result"), dict):
        fail("Caiyun Weather API returned an invalid forecast response.")

    return payload


def main() -> None:
    trigger = load_trigger()
    response = fetch_weather(trigger)
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
        "source": "Caiyun Weather API",
        **compact_forecast(response),
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated weather/weather.json for request {trigger['request_id']}.")


if __name__ == "__main__":
    main()
