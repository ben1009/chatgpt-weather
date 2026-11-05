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

`trigger.json` is a single latest-request slot, not a durable queue. Wait for the matching result before submitting another request. If another update arrives while a run is active, the older result is skipped and the latest queued request is processed.

The API request asks for up to 15 daily forecast entries and 72 hourly entries; Caiyun may cap results according to the account's plan.
