# ChatGPT Weather

This private repository fetches Caiyun forecasts only when `weather/trigger.json` is updated. It has no scheduled workflow. A manual `workflow_dispatch` entry is also available in GitHub Actions.

## Configure secrets

Add these repository Actions secrets:

- `CAIYUN_APP_KEY`
- `CAIYUN_APP_SECRET`

The fetch script signs Caiyun v2.6 API requests with HMAC-SHA256 and keeps both values out of repository files and logs.

## Request a forecast

For each ChatGPT weather request, replace `weather/trigger.json` with a new `request_id`, the location label, and coordinates. Mainland China coordinates must use GCJ-02. For example:

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

The push starts `.github/workflows/fetch-weather.yml`. The workflow writes the response and request metadata to `weather/weather.json`, then commits that file. Its push does not match the trigger path, so it does not start another run. Read `weather/weather.json` after the run completes.

The combined API request asks for up to 15 daily forecast entries and 72 hourly entries; Caiyun may cap results according to the account's plan.
