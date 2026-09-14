"""Async client for Anthropic Admin Analytics and inventory APIs."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import API_BASE_URL

QueryValue = str | int | float | list[str]

_LOGGER = logging.getLogger(__name__)
ANTHROPIC_VERSION = "2023-06-01"
DAILY_BUCKET_LIMIT = 31
USER_AGENT = "HAAnthropicUsageMonitor/0.1.4 (https://github.com/burren2025/HAAnthropicUsageMonitor)"


class AnthropicUsageError(Exception):
    """Base API error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AnthropicAuthError(AnthropicUsageError):
    """Raised when the API key is invalid or lacks access."""


class AnthropicRateLimitError(AnthropicUsageError):
    """Raised when Anthropic rate limits the request."""


class AnthropicUnavailableError(AnthropicUsageError):
    """Raised for temporary transport/server failures."""


class AnthropicPermissionError(AnthropicUsageError):
    """Raised when a valid credential appears to lack endpoint permission."""


@dataclass(slots=True)
class AnthropicAdminClient:
    """Small aiohttp wrapper around Anthropic Admin APIs."""

    session: ClientSession
    admin_api_key: str
    base_url: str = API_BASE_URL

    async def validate_key(self) -> None:
        """Validate that the credential can reach at least one Admin API endpoint."""
        errors: list[AnthropicUsageError] = []
        probes = (
            self.fetch_workspaces,
            self.fetch_api_keys,
            lambda: self.fetch_usage_report(
                starting_at=date.today(),
                ending_at=date.today() + timedelta(days=1),
                limit=1,
            ),
        )
        for probe in probes:
            try:
                await probe()
                return
            except AnthropicUsageError as err:
                errors.append(err)

        if errors and all(isinstance(err, AnthropicAuthError) for err in errors):
            raise errors[0]
        if errors and all(isinstance(err, AnthropicPermissionError) for err in errors):
            _LOGGER.info(
                "Anthropic Admin API key validation reached Anthropic but all probe endpoints were unauthorized"
            )
            return
        if errors:
            raise errors[-1]
        raise AnthropicUnavailableError("Anthropic Admin API validation failed")

    async def validate_usage_access(self) -> None:
        """Validate Usage and Cost Admin API access specifically."""
        today = date.today()
        await self.fetch_usage_report(starting_at=today, ending_at=today + timedelta(days=1), limit=1)

    async def fetch_usage_report(
        self,
        *,
        starting_at: date,
        ending_at: date,
        limit: int = DAILY_BUCKET_LIMIT,
        group_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages from Anthropic's user usage report endpoint."""
        params: dict[str, Any] = {
            "starting_at": _rfc3339_start(starting_at),
            "ending_at": _rfc3339_start(ending_at),
            "bucket_width": "1d",
            "limit": _daily_bucket_limit(limit),
        }
        if group_by:
            params["group_by[]"] = group_by
        buckets = await self._fetch_list_paginated(
            "/v1/organizations/usage_report/messages", params, cursor_param="page"
        )
        return _flatten_bucket_results(buckets)

    async def fetch_cost_report(
        self,
        *,
        starting_at: date,
        ending_at: date,
        limit: int = DAILY_BUCKET_LIMIT,
        group_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages from Anthropic's user cost report endpoint."""
        params: dict[str, Any] = {
            "starting_at": _rfc3339_start(starting_at),
            "ending_at": _rfc3339_start(ending_at),
            "bucket_width": "1d",
            "limit": _daily_bucket_limit(limit),
        }
        if group_by:
            params["group_by[]"] = group_by
        buckets = await self._fetch_list_paginated(
            "/v1/organizations/cost_report", params, cursor_param="page"
        )
        return _flatten_bucket_results(buckets)

    async def fetch_workspaces(self) -> list[dict[str, Any]]:
        """Fetch organization workspaces if the Admin key can see them."""
        return await self._fetch_list_paginated(
            "/v1/organizations/workspaces",
            {"limit": 100, "include_archived": True},
            cursor_param="after_id",
        )

    async def fetch_api_keys(self) -> list[dict[str, Any]]:
        """Fetch organization API key records if the Admin key can see them."""
        return await self._fetch_list_paginated(
            "/v1/organizations/api_keys", {"limit": 100}, cursor_param="after_id"
        )

    async def _fetch_list_paginated(
        self, endpoint: str, params: dict[str, Any], *, cursor_param: str
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            request_params = dict(params)
            if cursor:
                request_params[cursor_param] = cursor
            payload = await self._request_json(endpoint, request_params)
            data = payload.get("data")
            if isinstance(data, list):
                records.extend(data)
            elif isinstance(data, dict):
                records.append(data)
            elif isinstance(payload, list):
                records.extend(payload)
            cursor = payload.get("last_id") if cursor_param == "after_id" else payload.get("next_page")
            if not payload.get("has_more") or not cursor:
                return records

    async def _request_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {
            "x-api-key": self.admin_api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        for attempt in range(3):
            try:
                async with self.session.get(
                    url, headers=headers, params=_normalize_query_params(params), timeout=30
                ) as response:
                    return await self._handle_response(response)
            except AnthropicRateLimitError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
            except (TimeoutError, ClientError) as err:
                if attempt == 2:
                    raise AnthropicUnavailableError("Anthropic API request failed") from err
                await asyncio.sleep(2**attempt)
        raise AnthropicUnavailableError("Anthropic API request failed")

    async def _handle_response(self, response: ClientResponse) -> dict[str, Any]:
        if response.status == 401:
            detail = _redact_message(await response.text())
            _LOGGER.warning(
                "Anthropic Admin API authentication failed with HTTP %s: %s",
                response.status,
                detail,
            )
            raise AnthropicAuthError(
                f"Anthropic Admin API key is invalid or unauthorized: {detail}",
                response.status,
            )
        if response.status == 403:
            detail = _redact_message(await response.text())
            _LOGGER.debug(
                "Anthropic Admin API request lacks permission for this endpoint: %s",
                detail,
            )
            raise AnthropicPermissionError(
                f"Anthropic Admin API key lacks permission for this endpoint: {detail}",
                response.status,
            )
        if response.status == 429:
            raise AnthropicRateLimitError("Anthropic API rate limit exceeded", response.status)
        if response.status >= 500:
            raise AnthropicUnavailableError(
                "Anthropic API is temporarily unavailable", response.status
            )
        if response.status >= 400:
            detail = _redact_message(await response.text())
            if _is_unsupported_capability(detail):
                _LOGGER.debug("Anthropic Admin API capability unavailable: %s", detail)
            else:
                _LOGGER.warning("Anthropic Admin API returned HTTP %s: %s", response.status, detail)
            raise AnthropicUsageError(
                f"Anthropic Admin API returned HTTP {response.status}: {detail}", response.status
            )
        return await response.json()


def redact_secret(value: str | None) -> str | None:
    """Return a stable redacted representation of a secret."""
    if not value:
        return value
    return f"{value[:4]}...redacted...{value[-4:]}" if len(value) >= 12 else "redacted"


def _redact_message(value: str) -> str:
    """Redact obvious API key material from an API error message."""
    if not value:
        return "No response body"
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        message = value
    else:
        error = parsed.get("error") if isinstance(parsed, dict) else None
        message = error.get("message", value) if isinstance(error, dict) else value
    return message.replace("x-api-key", "x-api-key-redacted")


def _is_unsupported_capability(message: str) -> bool:
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in ("invalid group_by", "not found", "not available", "unsupported")
    )


def _normalize_query_params(params: dict[str, Any]) -> dict[str, QueryValue]:
    normalized: dict[str, QueryValue] = {}
    for key, value in params.items():
        if isinstance(value, bool):
            normalized[key] = str(value).lower()
        elif isinstance(value, list):
            normalized[key] = [str(item) for item in value]
        elif value is not None:
            normalized[key] = value
    return normalized


def _daily_bucket_limit(limit: int) -> int:
    """Keep daily report requests within Anthropic's documented range."""
    return min(max(limit, 1), DAILY_BUCKET_LIMIT)


def _rfc3339_start(value: date) -> str:
    return datetime.combine(value, time.min, timezone.utc).isoformat().replace("+00:00", "Z")


def _flatten_bucket_results(buckets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in buckets:
        results = bucket.get("results")
        if not isinstance(results, list):
            rows.append(bucket)
            continue
        for result in results:
            if isinstance(result, dict):
                rows.append(
                    {
                        "starting_at": bucket.get("starting_at"),
                        "ending_at": bucket.get("ending_at"),
                        **result,
                    }
                )
    return rows
