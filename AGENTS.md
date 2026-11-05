# Agent instructions

## Project

This public repository fetches on-demand Caiyun weather forecasts for ChatGPT. The workflow is defined in `.github/workflows/fetch-weather.yml`; forecast fetching and publication are implemented in `scripts/fetch_weather.py` and `scripts/publish_weather.py`.

## Public and runtime data

- Treat all committed content and Actions logs as public.
- `weather/trigger.json` is a single-slot live request and may contain a location, coordinates, query text, and timestamp. Do not change it for tests or replace a user's request unless explicitly asked.
- `weather/weather.json` is generated forecast data. Do not fabricate or overwrite it during code changes. Test with synthetic fixtures instead.
- Keep `CAIYUN_APP_KEY` and `CAIYUN_APP_SECRET` in GitHub Actions Secrets. Never commit, print, or copy their values into logs, tests, or documentation.
- The Apache-2.0 license covers the project code, tests, workflow, and documentation. Request and forecast data under `weather/` is outside that software license.

## Workflow behavior

- The workflow runs on pushes to `main` that change only `weather/trigger.json`, or through manual `workflow_dispatch`. There is no scheduled trigger; do not add one unless explicitly requested.
- Preserve the request identity and stale-request protections in the publisher: compare the full trigger at an immutable branch head and use GitHub's expected-head commit operation.
- Do not treat missing precipitation data as zero rain, or unavailable alert data as confirmation that no warnings exist. Preserve partial-coverage and section-status information.

## Development

- The scripts use the Python standard library. Avoid adding dependencies without a clear need.
- Run the offline test suite with `python3 -m unittest discover -s tests -v`.
- When changing forecast or publishing behavior, add regression coverage using synthetic data. Do not make live Caiyun requests as part of tests.
