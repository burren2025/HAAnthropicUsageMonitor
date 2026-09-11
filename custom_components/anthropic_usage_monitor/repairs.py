"""Repairs for Anthropic Usage Monitor."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


async def async_create_issue(hass: HomeAssistant, issue_id: str, translation_key: str) -> None:
    """Create a repair issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
    )
