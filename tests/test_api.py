"""Tests for the Anthropic Admin API client."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.anthropic_usage_monitor.api import (
    AnthropicAdminClient,
    AnthropicAuthError,
    AnthropicPermissionError,
    AnthropicRateLimitError,
    AnthropicUsageError,
)


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = []

    def get(self, url, headers, params, timeout):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_fetch_usage_report_handles_pagination():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": [
                        {
                            "starting_at": "2026-01-01T00:00:00Z",
                            "ending_at": "2026-01-02T00:00:00Z",
                            "results": [{"model": "claude-sonnet-5"}],
                        }
                    ],
                    "has_more": True,
                    "next_page": "next",
                },
            ),
            FakeResponse(
                200,
                {
                    "data": [
                        {
                            "starting_at": "2026-01-02T00:00:00Z",
                            "ending_at": "2026-01-03T00:00:00Z",
                            "results": [{"model": "claude-opus-5"}],
                        }
                    ],
                    "has_more": False,
                },
            ),
        ]
    )
    client = AnthropicAdminClient(session, "admin-key")

    rows = await client.fetch_usage_report(
        starting_at=date(2026, 1, 1), ending_at=date(2026, 1, 31)
    )

    assert [row["model"] for row in rows] == ["claude-sonnet-5", "claude-opus-5"]
    assert rows[0]["starting_at"] == "2026-01-01T00:00:00Z"
    assert session.calls[1]["params"]["page"] == "next"
    assert session.calls[0]["params"]["starting_at"] == "2026-01-01T00:00:00Z"
    assert session.calls[0]["params"]["limit"] == 31
    assert session.calls[0]["headers"]["x-api-key"] == "admin-key"
    assert session.calls[0]["headers"]["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_daily_report_limits_are_clamped_to_anthropic_maximum():
    session = FakeSession(
        [
            FakeResponse(200, {"data": [], "has_more": False}),
            FakeResponse(200, {"data": [], "has_more": False}),
        ]
    )
    client = AnthropicAdminClient(session, "admin-key")

    await client.fetch_usage_report(
        starting_at=date(2026, 1, 1), ending_at=date(2026, 2, 1), limit=100
    )
    await client.fetch_cost_report(
        starting_at=date(2026, 1, 1), ending_at=date(2026, 2, 1), limit=100
    )

    assert session.calls[0]["params"]["limit"] == 31
    assert session.calls[1]["params"]["limit"] == 31


@pytest.mark.asyncio
async def test_invalid_key_maps_to_auth_error():
    client = AnthropicAdminClient(FakeSession([FakeResponse(401)]), "admin-key")

    with pytest.raises(AnthropicAuthError):
        await client.fetch_usage_report(starting_at=date(2026, 1, 1), ending_at=date(2026, 1, 2))


@pytest.mark.asyncio
async def test_validation_accepts_admin_inventory_access_without_usage_access():
    session = FakeSession(
        [
            FakeResponse(200, {"data": [], "has_more": False}),
        ]
    )
    client = AnthropicAdminClient(session, "admin-key")

    await client.validate_key()

    assert session.calls[0]["url"].endswith("/v1/organizations/workspaces")


@pytest.mark.asyncio
async def test_validation_allows_permission_only_failures():
    session = FakeSession(
        [
            FakeResponse(403, text="missing billing data permission"),
            FakeResponse(403, text="missing billing data permission"),
            FakeResponse(403, text="missing billing data permission"),
        ]
    )
    client = AnthropicAdminClient(session, "admin-key")

    await client.validate_key()

    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_forbidden_endpoint_maps_to_permission_error():
    client = AnthropicAdminClient(
        FakeSession([FakeResponse(403, text="missing billing data permission")]), "admin-key"
    )

    with pytest.raises(AnthropicPermissionError):
        await client.fetch_cost_report(starting_at=date(2026, 1, 1), ending_at=date(2026, 1, 2))


@pytest.mark.asyncio
async def test_rate_limit_maps_to_rate_limit_error():
    client = AnthropicAdminClient(
        FakeSession([FakeResponse(429), FakeResponse(429), FakeResponse(429)]), "admin-key"
    )

    with pytest.raises(AnthropicRateLimitError):
        await client.fetch_cost_report(starting_at=date(2026, 1, 1), ending_at=date(2026, 1, 2))


@pytest.mark.asyncio
async def test_api_error_does_not_include_secret():
    client = AnthropicAdminClient(FakeSession([FakeResponse(400, text="bad request")]), "secret")

    with pytest.raises(AnthropicUsageError) as err:
        await client.fetch_cost_report(starting_at=date(2026, 1, 1), ending_at=date(2026, 1, 2))

    assert "secret" not in str(err.value)
