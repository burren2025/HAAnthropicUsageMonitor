# Anthropic Usage Monitor for Home Assistant

Home Assistant custom integration for monitoring Anthropic/Claude organization usage and cost with the Anthropic Usage and Cost Admin API.

## What It Uses

This integration uses documented Anthropic Admin endpoints where available to the supplied Admin API key:

- `/v1/organizations/usage_report/messages`
- `/v1/organizations/cost_report`
- `/v1/organizations/workspaces`
- `/v1/organizations/api_keys`

Anthropic Admin API availability and field detail can vary by plan, workspace, and key scope. The integration records unavailable endpoints in sensor attributes and keeps cumulative totals updating when possible.

## Security

An Anthropic Admin API key can be powerful. Use this only on a trusted Home Assistant instance, restrict access to Home Assistant backups, and rotate the key if you suspect exposure. The integration stores the key in Home Assistant config entry storage and redacts it from diagnostics and logs.

## Installation

### HACS Custom Repository

Add this repository in HACS:

1. Open HACS.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/burren2025/HAAnthropicUsageMonitor`.
4. Select repository type **Integration**.
5. Download **Anthropic Usage Monitor**.
6. Restart Home Assistant.
7. Add **Anthropic Usage Monitor** from **Settings > Devices & services > Add integration**.

For updates, install new GitHub releases through HACS and restart Home Assistant when prompted.

### Manual Installation

Copy `custom_components/anthropic_usage_monitor` into:

```text
config/custom_components/anthropic_usage_monitor
```

Restart Home Assistant, then add **Anthropic Usage Monitor** from **Settings > Devices & services > Add integration**.

## Configuration

The UI setup asks for:

- Anthropic Admin API key
- Friendly organization/account name
- Optional manual monthly credit or budget
- Optional warning thresholds
- Polling interval, default 60 minutes, minimum 30 minutes

Options allow updating the polling interval, budget, thresholds, top-N model sensor count, and local alias maps for API key IDs and workspace IDs. The stored Admin API key is not displayed or changed in Options. If the key becomes invalid or is revoked, Home Assistant starts a separate reauthentication flow for entering a replacement.

Alias maps are JSON objects:

```json
{"key_abc123": "Production app", "wrkspc_abc123": "Backend workspace"}
```

## Entities

Organization totals:

- `sensor.anthropic_cost_today`
- `sensor.anthropic_cost_month_to_date`
- `sensor.anthropic_requests_today`
- `sensor.anthropic_requests_month_to_date`
- `sensor.anthropic_input_tokens_today`
- `sensor.anthropic_output_tokens_today`
- `sensor.anthropic_total_tokens_today`
- `sensor.anthropic_input_tokens_month_to_date`
- `sensor.anthropic_output_tokens_month_to_date`
- `sensor.anthropic_total_tokens_month_to_date`
- `sensor.anthropic_estimated_credit_remaining`

Dynamic sensors are created when Anthropic returns inventory or report dimensions:

- One usage record sensor per API key ID
- One monthly-spend record sensor per workspace ID
- One usage record sensor per account/user ID when returned by the usage report
- Top-N model token sensors

API key record attributes include name, status, tracking ID, redacted key value, created time, expiration time, workspace access, principal/created-by metadata when returned by Anthropic, requests, token totals, category breakdowns, model breakdowns, and sample report records. Official Anthropic costs cannot currently be grouped by API key, so API key sensors use token counts as their state and mark key-level monthly spend as unavailable.

Workspace record attributes include name, status, tracking ID, created time, archived time, associated API key summaries where available, monthly spend, requests, token totals, category breakdowns, model breakdowns, and sample report records.

Account/user record attributes include tracking ID, requests, token totals, category/model breakdowns, and sample report records. Official Anthropic costs cannot currently be grouped by account ID in the Cost API, so account/user sensors use token counts as their state and mark monthly spend as unavailable.

## Credit and Budget

No official Anthropic remaining credit/balance endpoint is used. The remaining credit sensor is locally estimated:

```text
manual_monthly_credit_or_budget - month_to_date_cost
```

The sensor attributes include configured budget, month-to-date cost, estimated remaining, percent used, days elapsed, projected month-end cost, and average daily cost.

## Automations

See [examples/automations.yaml](examples/automations.yaml) for alerts covering:

- Today's spend exceeds a threshold
- Estimated remaining credit drops below a threshold
- Projected month-end cost exceeds configured budget
- Any single API key exceeds a token threshold
- A new unknown API key ID appears

## Development

Install test dependencies in a virtual environment, then run:

```bash
pytest
python scripts/dev_fetch.py --start 2026-01-01 --end 2026-01-31
```

The helper reads `ANTHROPIC_ADMIN_KEY` from the environment and never prints the key or request headers.

## Limitations

- Anthropic Usage and Cost Admin API availability can depend on plan and key permissions. It is not available for individual accounts.
- Detailed report fields can vary; the integration normalizes common fields and exposes sample records for debugging.
- Anthropic's official Cost API groups by workspace and description, not API key. API key sensors expose usage, requests, and tokens, but not official key-level spend.
- Remaining credit is estimated locally unless Anthropic adds a documented balance endpoint.
- This integration does not scrape the Anthropic Console.
