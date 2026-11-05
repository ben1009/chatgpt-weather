# ChatGPT Weather

This private repository fetches Caiyun forecasts when `weather/trigger.json` changes. There is no scheduled workflow. A manual `workflow_dispatch` entry is also available in GitHub Actions.

## Configure secrets

The repository uses these Actions secrets:

- `CAIYUN_APP_KEY`
- `CAIYUN_APP_SECRET`

The fetch script signs Caiyun v2.6 API requests with HMAC-SHA256. Secret values are not stored in repository files or printed by the script.

## Request a forecast

For each ChatGPT weather request, replace `weather/trigger.json` with a new, unique `request_id`, a location label, and coordinates. Mainland China coordinates must use GCJ-02. For example:

```json
{
  "request_id": "chatgpt-2026-11-05-001",
  "query": "Check weekend rain in Beijing Chaoyang",
  "location": "北京朝阳区",
  "longitude": 116.4436,
  "latitude": 39.9219,
  "requested_at": "2026-11-05T12:00:00+08:00"
}
```

The push starts `.github/workflows/fetch-weather.yml`. After the run completes, read `weather/weather.json` and confirm its `request_id` matches the request. The result contains a compact current, daily, and hourly forecast, precipitation and sky conditions, alerts, and coverage metadata; the raw Caiyun response is not saved.

Check `status` and `sections` before interpreting the forecast. A `partial` result identifies missing or failed sections. For rain questions, use `coverage.precipitation.daily` and `coverage.precipitation.hourly`, including their `missing_times` and `cadence_valid`; temperature or sky-condition data may extend beyond the available precipitation forecast. Missing hours or days inside the returned interval are reported. `precipitation_complete` describes that interval; compare its start/end against the requested dates as well. General `daily_end` and `hourly_end` describe the available weather records, not guaranteed rain coverage. A response with no usable hourly or daily forecast fails the run and leaves the previous result in place. Missing alerts are marked as unavailable and must not be interpreted as no active warnings.

`trigger.json` is a single latest-request slot, not a durable queue. Wait for the matching result before submitting another request. Publishing reads the full trigger at an immutable branch commit, then uses GitHub's expected-head commit operation. If the branch changes between the check and commit, publishing retries from its new head and skips a replaced request. The latest queued request is processed.

The API request asks for up to 15 daily forecast entries and 72 hourly entries; Caiyun may cap results according to the account's plan.

An unavailable alert feed is reported as `sections.alert.status = "unavailable"` with `alerts = null`; it does not establish that no warnings are active. In particular, Caiyun documents an upstream failure as empty alert content with `adcodes = null`. Existing warning content is retained if only its coverage metadata is unavailable. See the [Caiyun alert response documentation](https://docs.caiyunapp.com/weather-api/v2/v2.6/5-alert.html).

If the final commit response is lost, publishing re-reads the branch once before declaring failure. An already committed matching result succeeds without creating a duplicate commit.
