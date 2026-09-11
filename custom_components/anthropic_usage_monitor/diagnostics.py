"""Diagnostics for Anthropic Usage Monitor."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ADMIN_API_KEY, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = dict(entry.data)
    if CONF_ADMIN_API_KEY in data:
        data[CONF_ADMIN_API_KEY] = "redacted"
    return {
        "entry": {"title": entry.title, "data": data, "options": dict(entry.options)},
        "last_update_success": coordinator.last_update_success,
        "unavailable_categories": getattr(coordinator.data, "unavailable_categories", {}),
        "last_updated": getattr(coordinator.data, "last_updated", None),
    }
