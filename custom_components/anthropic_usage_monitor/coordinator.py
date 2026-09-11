"""Coordinator and normalization for Anthropic usage data."""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AnthropicAdminClient, AnthropicAuthError, AnthropicUsageError
from .const import (
    CONF_ADMIN_API_KEY,
    CONF_MONTHLY_BUDGET,
    CONF_POLL_INTERVAL_MINUTES,
    CONF_TOP_N_MODELS,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DEFAULT_TOP_N_MODELS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class UsageAggregate:
    """Aggregated counters."""

    cost: float = 0.0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_tokens: int = 0
    currency: str = "USD"
    model_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    last_updated: str | None = None

    def add_usage(self, result: dict[str, Any], category: str = "usage") -> None:
        input_tokens = _int_any(
            result, "uncached_input_tokens", "input_tokens", "input", "input_token_count"
        )
        output_tokens = _int_any(result, "output_tokens", "output", "output_token_count")
        cache_create = _cache_creation_tokens(result) or _int_any(
            result, "cache_creation_input_tokens", "cache_write_input_tokens"
        )
        cache_read = _int_any(result, "cache_read_input_tokens", "cached_input_tokens")
        total_tokens = _int_any(result, "total_tokens", "tokens") or (
            input_tokens + output_tokens + cache_create + cache_read
        )
        requests = _int_any(result, "requests", "request_count", "num_requests", "count")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_creation_input_tokens += cache_create
        self.cache_read_input_tokens += cache_read
        self.total_tokens += total_tokens
        self.requests += requests
        bucket = self.categories.setdefault(
            category,
            {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_tokens": 0,
            },
        )
        bucket["requests"] += requests
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cache_creation_input_tokens"] += cache_create
        bucket["cache_read_input_tokens"] += cache_read
        bucket["total_tokens"] += total_tokens
        if len(self.records) < 50:
            self.records.append(_compact_record(result))
        if model := _first_str(result, "model", "model_id", "claude_model"):
            model_bucket = self.model_breakdown.setdefault(
                model,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0.0,
                },
            )
            model_bucket["requests"] += requests
            model_bucket["input_tokens"] += input_tokens
            model_bucket["output_tokens"] += output_tokens
            model_bucket["cache_creation_input_tokens"] += cache_create
            model_bucket["cache_read_input_tokens"] += cache_read
            model_bucket["total_tokens"] += total_tokens

    def add_cost(self, result: dict[str, Any]) -> None:
        value = _cost_value(result)
        self.cost += value
        if currency := _currency(result):
            self.currency = currency
        if len(self.records) < 50:
            self.records.append(_compact_record(result))
        if model := _first_str(result, "model", "model_id", "claude_model"):
            model_bucket = self.model_breakdown.setdefault(
                model,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0.0,
                },
            )
            model_bucket["cost"] += value


@dataclass(slots=True)
class APIKeyRecord:
    """API key inventory metadata merged with monthly usage."""

    id: str
    name: str | None = None
    redacted_value: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    last_used_at: str | None = None
    created_by: dict[str, Any] | None = None
    principal: dict[str, Any] | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    status: str | None = None
    source: str = "usage"


@dataclass(slots=True)
class WorkspaceRecord:
    """Workspace inventory metadata merged with monthly usage."""

    id: str
    name: str | None = None
    status: str | None = None
    created_at: str | None = None
    archived_at: str | None = None
    api_keys: list[dict[str, Any]] = field(default_factory=list)
    source: str = "usage"


@dataclass(slots=True)
class UserRecord:
    """User/activity record merged with monthly usage."""

    id: str
    email: str | None = None
    name: str | None = None
    role: str | None = None
    status: str | None = None
    source: str = "usage"


@dataclass(slots=True)
class AnthropicUsageData:
    """Normalized data exposed to entities."""

    today: UsageAggregate
    month: UsageAggregate
    api_keys: dict[str, UsageAggregate]
    workspaces: dict[str, UsageAggregate]
    users: dict[str, UsageAggregate]
    models: dict[str, UsageAggregate]
    api_key_records: dict[str, APIKeyRecord]
    workspace_records: dict[str, WorkspaceRecord]
    user_records: dict[str, UserRecord]
    unavailable_categories: dict[str, str]
    unknown_api_keys: list[str]
    budget: dict[str, Any]
    last_updated: str


class AnthropicUsageCoordinator(DataUpdateCoordinator[AnthropicUsageData]):
    """Fetch Anthropic organization usage periodically."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        minutes = int(
            entry.options.get(
                CONF_POLL_INTERVAL_MINUTES,
                entry.data.get(CONF_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
            config_entry=entry,
        )
        self.client = AnthropicAdminClient(
            async_get_clientsession(hass), entry.data[CONF_ADMIN_API_KEY]
        )

    async def _async_update_data(self) -> AnthropicUsageData:
        now = dt_util.now().date()
        try:
            return await self._collect(date(now.year, now.month, 1), now + timedelta(days=1), now)
        except AnthropicAuthError as err:
            raise UpdateFailed("Anthropic Admin API key is invalid or unauthorized") from err
        except AnthropicUsageError as err:
            raise UpdateFailed(str(err)) from err

    async def _collect(
        self, month_start: date, ending_at: date, current_day: date
    ) -> AnthropicUsageData:
        month = UsageAggregate()
        today_agg = UsageAggregate()
        by_key: dict[str, UsageAggregate] = defaultdict(UsageAggregate)
        by_workspace: dict[str, UsageAggregate] = defaultdict(UsageAggregate)
        by_user: dict[str, UsageAggregate] = defaultdict(UsageAggregate)
        by_model: dict[str, UsageAggregate] = defaultdict(UsageAggregate)
        unavailable: dict[str, str] = {}

        usage_records = await self._optional_report(
            unavailable,
            "usage_report",
            self.client.fetch_usage_report(
                starting_at=month_start,
                ending_at=ending_at,
                group_by=["account_id", "api_key_id", "workspace_id", "model", "service_tier"],
            ),
        )
        for result in usage_records:
            month.add_usage(result)
            if _record_is_today(result, current_day):
                today_agg.add_usage(result)
            _add_dimension_usage(result, by_key, by_workspace, by_user, by_model)

        cost_records = await self._optional_report(
            unavailable,
            "cost_report",
            self.client.fetch_cost_report(
                starting_at=month_start,
                ending_at=ending_at,
                group_by=["workspace_id", "description"],
            ),
        )
        for result in cost_records:
            month.add_cost(result)
            if _record_is_today(result, current_day):
                today_agg.add_cost(result)
            _add_dimension_cost(result, by_key, by_workspace, by_user, by_model)

        api_key_records, workspace_records, user_records = await self._fetch_inventory(
            unavailable, by_key, by_workspace, by_user
        )

        last_updated = dt_util.utcnow().isoformat()
        for agg in [
            month,
            today_agg,
            *by_key.values(),
            *by_workspace.values(),
            *by_user.values(),
            *by_model.values(),
        ]:
            agg.last_updated = last_updated

        top_n = int(self.config_entry.options.get(CONF_TOP_N_MODELS, DEFAULT_TOP_N_MODELS))
        model_map = dict(
            sorted(
                by_model.items(), key=lambda item: (item[1].cost, item[1].total_tokens), reverse=True
            )[:top_n]
        )
        return AnthropicUsageData(
            today=today_agg,
            month=month,
            api_keys=dict(by_key),
            workspaces=dict(by_workspace),
            users=dict(by_user),
            models=model_map,
            api_key_records=api_key_records,
            workspace_records=workspace_records,
            user_records=user_records,
            unavailable_categories=unavailable,
            unknown_api_keys=sorted(k for k in by_key if k),
            budget=_budget_attrs(
                self.config_entry.options.get(
                    CONF_MONTHLY_BUDGET, self.config_entry.data.get(CONF_MONTHLY_BUDGET)
                ),
                month.cost,
                current_day,
            ),
            last_updated=last_updated,
        )

    async def _optional_report(
        self, unavailable: dict[str, str], name: str, awaitable
    ) -> list[dict[str, Any]]:
        try:
            return await awaitable
        except AnthropicUsageError as err:
            unavailable[name] = str(err)
            return []

    async def _fetch_inventory(
        self,
        unavailable: dict[str, str],
        by_key: dict[str, UsageAggregate],
        by_workspace: dict[str, UsageAggregate],
        by_user: dict[str, UsageAggregate],
    ) -> tuple[dict[str, APIKeyRecord], dict[str, WorkspaceRecord], dict[str, UserRecord]]:
        api_key_records: dict[str, APIKeyRecord] = {}
        workspace_records: dict[str, WorkspaceRecord] = {}
        user_records: dict[str, UserRecord] = {}

        for key in await self._optional_report(unavailable, "api_key_inventory", self.client.fetch_api_keys()):
            record = _api_key_record_from_api(key)
            if record.id:
                api_key_records[record.id] = record

        for workspace in await self._optional_report(
            unavailable, "workspace_inventory", self.client.fetch_workspaces()
        ):
            record = _workspace_record_from_api(workspace)
            if record.id:
                workspace_records[record.id] = record

        for key_id in by_key:
            api_key_records.setdefault(key_id, APIKeyRecord(id=key_id, source="usage"))
        for workspace_id in by_workspace:
            workspace_records.setdefault(workspace_id, WorkspaceRecord(id=workspace_id, source="usage"))
        for user_id in by_user:
            user_records.setdefault(user_id, UserRecord(id=user_id, source="usage"))
        for key_record in api_key_records.values():
            if key_record.workspace_id and key_record.workspace_id in workspace_records:
                _append_workspace_key(workspace_records[key_record.workspace_id], key_record)
        return api_key_records, workspace_records, user_records


def _add_dimension_usage(
    result: dict[str, Any],
    by_key: dict[str, UsageAggregate],
    by_workspace: dict[str, UsageAggregate],
    by_user: dict[str, UsageAggregate],
    by_model: dict[str, UsageAggregate],
) -> None:
    if key_id := _first_str(result, "api_key_id", "api_key"):
        by_key[key_id].add_usage(result)
    if workspace_id := _first_str(result, "workspace_id", "workspace"):
        by_workspace[workspace_id].add_usage(result)
    if user_id := _first_str(result, "account_id", "user_id", "actor_id", "email"):
        by_user[user_id].add_usage(result)
    if model := _first_str(result, "model", "model_id", "claude_model"):
        by_model[model].add_usage(result)


def _add_dimension_cost(
    result: dict[str, Any],
    by_key: dict[str, UsageAggregate],
    by_workspace: dict[str, UsageAggregate],
    by_user: dict[str, UsageAggregate],
    by_model: dict[str, UsageAggregate],
) -> None:
    if key_id := _first_str(result, "api_key_id", "api_key"):
        by_key[key_id].add_cost(result)
    if workspace_id := _first_str(result, "workspace_id", "workspace"):
        by_workspace[workspace_id].add_cost(result)
    if user_id := _first_str(result, "account_id", "user_id", "actor_id", "email"):
        by_user[user_id].add_cost(result)
    if model := _first_str(result, "model", "model_id", "claude_model"):
        by_model[model].add_cost(result)


def _api_key_record_from_api(payload: dict[str, Any]) -> APIKeyRecord:
    created_by = payload.get("created_by") if isinstance(payload.get("created_by"), dict) else None
    principal = payload.get("principal") if isinstance(payload.get("principal"), dict) else None
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    return APIKeyRecord(
        id=str(payload.get("id") or ""),
        name=payload.get("name"),
        redacted_value=payload.get("redacted_value") or payload.get("partial_key_hint"),
        created_at=_timestamp_attr(payload.get("created_at")),
        expires_at=_timestamp_attr(payload.get("expires_at")),
        last_used_at=_timestamp_attr(payload.get("last_used_at")),
        created_by=created_by,
        principal=principal,
        workspace_id=_first_str(payload, "workspace_id") or scope.get("workspace_id"),
        status=payload.get("status"),
        source="api_keys",
    )


def _workspace_record_from_api(payload: dict[str, Any]) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=str(payload.get("id") or ""),
        name=payload.get("name"),
        status=payload.get("status") or ("archived" if payload.get("archived_at") else "active"),
        created_at=_timestamp_attr(payload.get("created_at")),
        archived_at=_timestamp_attr(payload.get("archived_at")),
        source="workspaces",
    )


def _user_record_from_api(payload: dict[str, Any]) -> UserRecord:
    user_id = _first_str(payload, "id", "account_id", "user_id", "actor_id", "email")
    return UserRecord(
        id=user_id or "",
        email=payload.get("email"),
        name=payload.get("name"),
        role=payload.get("role"),
        status=payload.get("status"),
        source="activity_users",
    )


def _append_workspace_key(workspace_record: WorkspaceRecord, key_record: APIKeyRecord) -> None:
    summary = {
        "id": key_record.id,
        "name": key_record.name,
        "status": key_record.status or _api_key_status(key_record),
        "created_at": key_record.created_at,
        "last_used_at": key_record.last_used_at,
        "expires_at": key_record.expires_at,
        "created_by": key_record.created_by,
        "principal": key_record.principal,
        "source": key_record.source,
    }
    if summary not in workspace_record.api_keys:
        workspace_record.api_keys.append(summary)


def _api_key_status(record: APIKeyRecord) -> str:
    if record.status:
        return record.status
    if record.expires_at:
        try:
            expires = datetime.fromisoformat(record.expires_at)
        except ValueError:
            return "unknown"
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return "expired" if expires <= datetime.now(timezone.utc) else "active"
    return "active"


def _record_is_today(result: dict[str, Any], today: date) -> bool:
    raw = _first_str(result, "date", "starting_at", "start_time", "timestamp")
    if not raw:
        return True
    parsed = _parse_date(raw)
    return parsed == today if parsed else True


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return dt_util.utc_from_timestamp(int(raw)).date()
        except (TypeError, ValueError, OSError):
            return None


def _cost_value(result: dict[str, Any]) -> float:
    amount = result.get("amount")
    if isinstance(amount, dict):
        return float(amount.get("value") or amount.get("amount") or 0) / 100
    if amount is not None:
        return float(amount or 0) / 100
    return float(result.get("cost") or result.get("cost_usd") or result.get("amount_usd") or 0)


def _currency(result: dict[str, Any]) -> str | None:
    amount = result.get("amount")
    if isinstance(amount, dict) and amount.get("currency"):
        return str(amount["currency"]).upper()
    if result.get("currency"):
        return str(result["currency"]).upper()
    return "USD" if any(key in result for key in ("cost_usd", "amount_usd")) else None


def _int_any(result: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            return int(result.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _cache_creation_tokens(result: dict[str, Any]) -> int:
    cache_creation = result.get("cache_creation")
    if not isinstance(cache_creation, dict):
        return 0
    return _int_any(cache_creation, "ephemeral_1h_input_tokens") + _int_any(
        cache_creation, "ephemeral_5m_input_tokens"
    )


def _first_str(result: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = result.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _compact_record(result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "date",
        "starting_at",
        "ending_at",
        "model",
        "model_id",
        "api_key_id",
        "workspace_id",
        "account_id",
        "user_id",
        "actor_id",
        "email",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "total_tokens",
        "requests",
        "request_count",
        "cost",
        "cost_usd",
        "amount_usd",
        "currency",
    }
    return {key: value for key, value in result.items() if key in allowed}


def _timestamp_attr(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    try:
        return dt_util.utc_from_timestamp(int(value)).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _budget_attrs(configured_budget: Any, month_cost: float, today: date) -> dict[str, Any]:
    budget = float(configured_budget or 0)
    days_elapsed = today.day
    _, days_in_month = calendar.monthrange(today.year, today.month)
    average_daily = month_cost / max(days_elapsed, 1)
    projected = average_daily * days_in_month
    remaining = budget - month_cost if budget else None
    percent = (month_cost / budget * 100) if budget else None
    return {
        "configured_budget": budget or None,
        "month_to_date_cost": round(month_cost, 6),
        "estimated_remaining": round(remaining, 6) if remaining is not None else None,
        "percent_used": round(percent, 2) if percent is not None else None,
        "days_elapsed": days_elapsed,
        "projected_month_end_cost": round(projected, 6),
        "average_daily_cost": round(average_daily, 6),
    }
