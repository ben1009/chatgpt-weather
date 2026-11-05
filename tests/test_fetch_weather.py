import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_weather as weather


DAY = "2026-11-05T00:00+08:00"
T0 = "2026-11-05T18:00+08:00"
T1 = "2026-11-05T19:00+08:00"
T2 = "2026-11-05T20:00+08:00"


def forecast():
    return {"result": {
        "realtime": {"status": "ok", "temperature": 0},
        "daily": {"status": "ok", "precipitation": [{"date": DAY, "max": 0, "probability": 0}]},
        "hourly": {
            "status": "ok",
            "precipitation": [{"datetime": T0, "value": 0, "probability": 0}],
            "temperature": [{"datetime": T0, "value": 0}],
        },
    }}


class ForecastTests(unittest.TestCase):
    def test_temperature_does_not_extend_precipitation_coverage(self):
        payload = forecast()
        payload["result"]["hourly"]["temperature"].append({"datetime": T1, "value": 1})
        result = weather.compact_forecast(payload)
        rain = result["coverage"]["precipitation"]["hourly"]
        self.assertEqual(result["coverage"]["hourly_end"], T1)
        self.assertEqual(rain["end"], T0)
        self.assertEqual(rain["entries"], 1)
        self.assertEqual(rain["missing_times"], [T1])
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["sections"]["hourly"]["precipitation_complete"])

    def test_timestamps_are_sorted_across_different_series(self):
        payload = forecast()
        payload["result"]["hourly"]["precipitation"] = [{"datetime": T1, "value": 2}]
        result = weather.compact_forecast(payload)
        self.assertEqual([item["datetime"] for item in result["hourly"]], [T0, T1])
        self.assertEqual(result["coverage"]["precipitation"]["hourly"]["start"], T1)

    def test_zero_rain_is_preserved(self):
        result = weather.compact_forecast(forecast())
        self.assertEqual(result["hourly"][0]["precipitation"], {"value": 0, "probability": 0})
        self.assertEqual(result["status"], "ok")

    def test_upstream_alert_failure_is_not_reported_as_no_warnings(self):
        payload = forecast()
        payload["result"]["alert"] = {"status": "ok", "content": [], "adcodes": None}
        result = weather.compact_forecast(payload)
        self.assertIsNone(result["alerts"])
        self.assertEqual(result["sections"]["alert"]["status"], "unavailable")
        self.assertEqual(result["sections"]["alert"]["provider_status"], "ok")
        self.assertEqual(result["status"], "partial")

    def test_valid_empty_alert_response_remains_available(self):
        payload = forecast()
        payload["result"]["alert"] = {
            "status": "ok", "content": [], "adcodes": [{"adcode": 110000, "name": "Beijing"}],
        }
        result = weather.compact_forecast(payload)
        self.assertEqual(result["alerts"]["content"], [])
        self.assertEqual(result["sections"]["alert"]["status"], "ok")
        self.assertEqual(result["status"], "ok")

    def test_existing_warnings_survive_missing_alert_coverage_metadata(self):
        payload = forecast()
        warning = {"title": "Wind warning", "code": "0501"}
        payload["result"]["alert"] = {"status": "ok", "content": [warning], "adcodes": None}
        result = weather.compact_forecast(payload)
        self.assertEqual(result["alerts"]["content"], [warning])
        self.assertEqual(result["sections"]["alert"]["status"], "partial")
        self.assertEqual(result["status"], "partial")

    def test_malformed_alert_content_is_marked_unavailable(self):
        payload = forecast()
        payload["result"]["alert"] = {"status": "ok", "content": None, "adcodes": []}
        result = weather.compact_forecast(payload)
        self.assertIsNone(result["alerts"])
        self.assertEqual(result["sections"]["alert"]["status"], "unavailable")

    def test_hour_missing_from_every_series_is_reported(self):
        payload = forecast()
        payload["result"]["hourly"]["precipitation"].append({"datetime": T2, "value": 0})
        result = weather.compact_forecast(payload)
        self.assertEqual(result["coverage"]["precipitation"]["hourly"]["missing_times"], [T1])
        self.assertFalse(result["sections"]["hourly"]["precipitation_complete"])
        self.assertEqual(result["status"], "partial")

    def test_day_missing_from_every_series_is_reported(self):
        payload = forecast()
        payload["result"]["daily"]["precipitation"].append({"date": "2026-11-07T00:00+08:00", "max": 0})
        result = weather.compact_forecast(payload)
        self.assertEqual(result["coverage"]["precipitation"]["daily"]["missing_times"], ["2026-11-06"])
        self.assertFalse(result["sections"]["daily"]["precipitation_complete"])

    def test_hourly_cadence_respects_daylight_saving_offsets(self):
        records = [
            {"datetime": "2026-11-01T01:00-04:00", "precipitation": {"value": 0}},
            {"datetime": "2026-11-01T01:00-05:00", "precipitation": {"value": 0}},
        ]
        rain = weather.precipitation_coverage(records, "datetime", "America/New_York")
        self.assertEqual(rain["missing_times"], [])
        self.assertTrue(rain["cadence_valid"])

    def test_failed_section_does_not_publish_its_values(self):
        payload = forecast()
        payload["result"]["hourly"].update(status="failed", error="upstream unavailable")
        result = weather.compact_forecast(payload)
        self.assertEqual(result["hourly"], [])
        self.assertEqual(result["sections"]["hourly"]["status"], "failed")
        self.assertEqual(result["sections"]["hourly"]["error"], "upstream unavailable")
        self.assertEqual(result["status"], "partial")

    def test_missing_precipitation_is_not_reported_as_zero_rain(self):
        payload = forecast()
        payload["result"]["hourly"]["precipitation"] = [{"datetime": T0, "value": None}]
        result = weather.compact_forecast(payload)
        self.assertNotIn("precipitation", result["hourly"][0])
        self.assertEqual(result["coverage"]["precipitation"]["hourly"]["entries"], 0)
        self.assertEqual(result["status"], "partial")

    def test_unusable_forecast_does_not_replace_result_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weather.json"
            path.write_text("existing result", encoding="utf-8")
            with (
                mock.patch.object(weather, "load_trigger", return_value={
                    "request_id": "A", "location": "test", "longitude": 0, "latitude": 0,
                }),
                mock.patch.object(weather, "fetch_weather", return_value={"result": {}}),
                mock.patch.object(weather, "RESULT_PATH", path),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                weather.main()
            self.assertEqual(path.read_text(encoding="utf-8"), "existing result")

    def test_provider_error_redacts_credentials(self):
        with mock.patch.dict("os.environ", {"CAIYUN_APP_SECRET": "test-secret"}):
            _, info = weather.section_source({"hourly": {"status": "failed", "error": "bad test-secret"}}, "hourly")
        self.assertEqual(info["error"], "bad [redacted]")

    def test_huge_coordinate_fails_validation_without_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trigger.json"
            path.write_text(json.dumps({"request_id": "A", "location": "test", "longitude": 10**1000, "latitude": 0}), encoding="utf-8")
            with mock.patch.object(weather, "TRIGGER_PATH", path), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                weather.load_trigger()


if __name__ == "__main__":
    unittest.main()
