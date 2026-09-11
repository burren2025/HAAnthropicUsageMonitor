"""Sensors for Anthropic Usage Monitor."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_API_KEY_ALIASES, CONF_ORG_NAME, CONF_WORKSPACE_ALIASES, DOMAIN
from .coordinator import AnthropicUsageCoordinator, UsageAggregate

TOKEN_UNIT = "tokens"


@dataclass(frozen=True, kw_only=True)
class AnthropicSensorDescription(SensorEntityDescription):
    """Sensor description with value extraction."""

    value_fn: Callable[[AnthropicUsageCoordinator], Any]
    attrs_fn: Callable[[AnthropicUsageCoordinator], dict[str, Any]] | None = None


TOTAL_DESCRIPTIONS: tuple[AnthropicSensorDescription, ...] = (
    AnthropicSensorDescription(
        key="cost_today",
        translation_key="cost_today",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:cash",
        value_fn=lambda c: round(c.data.today.cost, 6),
        attrs_fn=lambda c: _aggregate_attrs(c.data.today),
    ),
    AnthropicSensorDescription(
        key="cost_month_to_date",
        translation_key="cost_month_to_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:cash-multiple",
        value_fn=lambda c: round(c.data.month.cost, 6),
        attrs_fn=lambda c: _aggregate_attrs(c.data.month),
    ),
    AnthropicSensorDescription(
        key="requests_today",
        translation_key="requests_today",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:counter",
        value_fn=lambda c: c.data.today.requests,
    ),
    AnthropicSensorDescription(
        key="requests_month_to_date",
        translation_key="requests_month_to_date",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:counter",
        value_fn=lambda c: c.data.month.requests,
    ),
    AnthropicSensorDescription(
        key="input_tokens_today",
        translation_key="input_tokens_today",
        native_unit_of_measurement=TOKEN_UNIT,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:text-box-arrow-right",
        value_fn=lambda c: c.data.today.input_tokens,
    ),
    AnthropicSensorDescription(
        key="output_tokens_today",
        translation_key="output_tokens_today",
        native_unit_of_measurement=TOKEN_UNIT,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:text-box-arrow-left",
        value_fn=lambda c: c.data.today.output_tokens,
    ),
    AnthropicSensorDescription(
        key="total_tokens_today",
        translation_key="total_tokens_today",
        native_unit_of_measurement=TOKEN_UNIT,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:text-box-multiple",
        value_fn=lambda c: c.data.today.total_tokens,
    ),
    AnthropicSensorDescription(
        key="input_tokens_month_to_date",
        translation_key="input_tokens_month_to_date",
        native_unit_of_measurement=TOKEN_UNIT,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:text-box-arrow-right",
        value_fn=lambda c: c.data.month.input_tokens,
    ),
    AnthropicSensorDescription(
        key="output_tokens_month_to_date",
        translation_key="output_tokens_month_to_date",
        native_unit_of_measurement=TOKEN_UNIT,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:text-box-arrow-left",
        value_fn=lambda c: c.data.month.output_tokens,
    ),
    AnthropicSensorDescription(
        key="total_tokens_month_to_date",
        translation_key="total_tokens_month_to_date",
        native_unit_of_measurement=TOKEN_UNIT,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:text-box-multiple",
        value_fn=lambda c: c.data.month.total_tokens,
    ),
    AnthropicSensorDescription(
        key="estimated_credit_remaining",
        translation_key="estimated_credit_remaining",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wallet",
        value_fn=lambda c: c.data.budget.get("estimated_remaining"),
        attrs_fn=lambda c: c.data.budget,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors."""
    coordinator: AnthropicUsageCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        AnthropicTotalSensor(coordinator, entry, description)
        for description in TOTAL_DESCRIPTIONS
    ]
    known: set[tuple[str, str]] = set()

    def grouped_entities() -> list[AnthropicGroupedSensor]:
        new_entities: list[AnthropicGroupedSensor] = []
        groups = (
            ("api_key", coordinator.data.api_key_records),
            ("workspace", coordinator.data.workspace_records),
            ("user", coordinator.data.user_records),
            ("model", coordinator.data.models),
        )
        for group_type, values in groups:
            for group_id in values:
                marker = (group_type, group_id)
                if marker in known:
                    continue
                known.add(marker)
                new_entities.append(AnthropicGroupedSensor(coordinator, entry, group_type, group_id))
        return new_entities

    entities.extend(grouped_entities())
    async_add_entities(entities)

    def add_new_grouped_entities() -> None:
        if new_entities := grouped_entities():
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new_grouped_entities))


class AnthropicTotalSensor(CoordinatorEntity[AnthropicUsageCoordinator], SensorEntity):
    """A fixed organization-level sensor."""

    entity_description: AnthropicSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AnthropicUsageCoordinator,
        entry: ConfigEntry,
        description: AnthropicSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.device_class == SensorDeviceClass.MONETARY:
            return self.coordinator.data.month.currency
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {"last_updated": self.coordinator.data.last_updated}
        if self.entity_description.attrs_fn:
            attrs.update(self.entity_description.attrs_fn(self.coordinator))
        if self.coordinator.data.unavailable_categories:
            attrs["unavailable_categories"] = self.coordinator.data.unavailable_categories
        return attrs


class AnthropicGroupedSensor(CoordinatorEntity[AnthropicUsageCoordinator], SensorEntity):
    """Dynamic grouped sensor for API key, workspace, user, or model."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cash-fast"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self,
        coordinator: AnthropicUsageCoordinator,
        entry: ConfigEntry,
        group_type: str,
        group_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.group_type = group_type
        self.group_id = group_id
        self._attr_unique_id = f"{entry.entry_id}_{group_type}_{group_id}"
        self._attr_device_info = _device_info(entry)

    @property
    def translation_key(self) -> str:
        return f"{self.group_type}_usage"

    @property
    def translation_placeholders(self) -> dict[str, str]:
        return {"name": self._display_name}

    @property
    def native_value(self) -> float | int | None:
        aggregate = self._aggregate
        if self.group_type in ("api_key", "model", "user"):
            return aggregate.total_tokens if aggregate else None
        return round(aggregate.cost, 6) if aggregate else 0

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return None if self.group_type in ("api_key", "model", "user") else SensorDeviceClass.MONETARY

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.group_type in ("api_key", "model", "user"):
            return TOKEN_UNIT
        return self.coordinator.data.month.currency

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        aggregate = self._aggregate or UsageAggregate()
        attrs = _aggregate_attrs(aggregate)
        attrs["id"] = self.group_id
        attrs["friendly_alias"] = self._display_name
        if self.group_type == "api_key":
            attrs["cost"] = None
            attrs["cost_availability"] = "Anthropic Usage and Cost Admin API does not officially group costs by API key."
            attrs.update(_api_key_record_attrs(self.coordinator, self.group_id))
        elif self.group_type == "workspace":
            attrs.update(_workspace_record_attrs(self.coordinator, self.group_id))
        elif self.group_type == "user":
            attrs.update(_user_record_attrs(self.coordinator, self.group_id))
        return attrs

    @property
    def _aggregate(self) -> UsageAggregate | None:
        if self.group_type == "api_key":
            return self.coordinator.data.api_keys.get(self.group_id)
        if self.group_type == "workspace":
            return self.coordinator.data.workspaces.get(self.group_id)
        if self.group_type == "user":
            return self.coordinator.data.users.get(self.group_id)
        return self.coordinator.data.models.get(self.group_id)

    @property
    def _display_name(self) -> str:
        if self.group_type == "api_key":
            record = self.coordinator.data.api_key_records.get(self.group_id)
            return _aliases(self.entry, CONF_API_KEY_ALIASES).get(
                self.group_id, (record.name if record and record.name else self.group_id)
            )
        if self.group_type == "workspace":
            record = self.coordinator.data.workspace_records.get(self.group_id)
            return _aliases(self.entry, CONF_WORKSPACE_ALIASES).get(
                self.group_id, (record.name if record and record.name else self.group_id)
            )
        if self.group_type == "user":
            record = self.coordinator.data.user_records.get(self.group_id)
            return (record.name or record.email) if record else self.group_id
        return self.group_id


def _aggregate_attrs(aggregate: UsageAggregate) -> dict[str, Any]:
    return {
        "cost": round(aggregate.cost, 6),
        "requests": aggregate.requests,
        "input_tokens": aggregate.input_tokens,
        "output_tokens": aggregate.output_tokens,
        "cache_creation_input_tokens": aggregate.cache_creation_input_tokens,
        "cache_read_input_tokens": aggregate.cache_read_input_tokens,
        "total_tokens": aggregate.total_tokens,
        "currency": aggregate.currency,
        "model_breakdown": aggregate.model_breakdown,
        "usage_categories": aggregate.categories,
        "sample_records": aggregate.records,
        "last_updated": aggregate.last_updated,
    }


def _api_key_record_attrs(coordinator: AnthropicUsageCoordinator, key_id: str) -> dict[str, Any]:
    record = coordinator.data.api_key_records.get(key_id)
    if not record:
        return {"tracking_id": key_id, "record_source": "usage"}
    return {
        "name": record.name,
        "status": _api_key_status(record.expires_at, record.status),
        "tracking_id": record.id,
        "redacted_value": record.redacted_value,
        "created_at": record.created_at,
        "last_used_at": record.last_used_at,
        "expires_at": record.expires_at,
        "workspace_access": {
            "workspace_id": record.workspace_id,
            "workspace_name": record.workspace_name,
        },
        "created_by": _actor_name(record.created_by),
        "principal": record.principal,
        "record_source": record.source,
        "monthly_spend": None,
        "monthly_spend_availability": "unavailable_from_official_cost_api",
    }


def _workspace_record_attrs(coordinator: AnthropicUsageCoordinator, workspace_id: str) -> dict[str, Any]:
    record = coordinator.data.workspace_records.get(workspace_id)
    if not record:
        return {"tracking_id": workspace_id, "record_source": "usage"}
    return {
        "name": record.name,
        "status": record.status,
        "tracking_id": record.id,
        "created_at": record.created_at,
        "archived_at": record.archived_at,
        "api_keys": record.api_keys,
        "record_source": record.source,
        "monthly_spend": round((coordinator.data.workspaces.get(workspace_id) or UsageAggregate()).cost, 6),
    }


def _user_record_attrs(coordinator: AnthropicUsageCoordinator, user_id: str) -> dict[str, Any]:
    record = coordinator.data.user_records.get(user_id)
    if not record:
        return {"tracking_id": user_id, "record_source": "usage"}
    return {
        "name": record.name,
        "email": record.email,
        "role": record.role,
        "status": record.status,
        "tracking_id": record.id,
        "record_source": record.source,
        "monthly_spend": None,
        "monthly_spend_availability": "unavailable_from_official_cost_api",
    }


def _api_key_status(expires_at: str | None, explicit_status: str | None) -> str:
    if explicit_status:
        return explicit_status
    if not expires_at:
        return "active"
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return "unknown"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return "expired" if expires <= datetime.now(timezone.utc) else "active"


def _actor_name(actor: dict[str, Any] | None) -> str | None:
    if not actor:
        return None
    return actor.get("name") or actor.get("email") or actor.get("id")


def _aliases(entry: ConfigEntry, key: str) -> dict[str, str]:
    try:
        parsed = json.loads(entry.options.get(key) or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.data.get(CONF_ORG_NAME, "Anthropic Usage"),
        "manufacturer": "Anthropic",
    }
