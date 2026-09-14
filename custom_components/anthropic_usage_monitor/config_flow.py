"""Config flow for Anthropic Usage Monitor."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnthropicAdminClient, AnthropicAuthError, AnthropicUsageError
from .const import (
    CONF_ADMIN_API_KEY,
    CONF_API_KEY_ALIASES,
    CONF_DAILY_SPEND_WARNING,
    CONF_MONTHLY_BUDGET,
    CONF_ORG_NAME,
    CONF_POLL_INTERVAL_MINUTES,
    CONF_REMAINING_CREDIT_WARNING,
    CONF_TOP_N_MODELS,
    CONF_WORKSPACE_ALIASES,
    DEFAULT_ORG_NAME,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DEFAULT_TOP_N_MODELS,
    DOMAIN,
    MIN_POLL_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class AnthropicUsageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create a config entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ORG_NAME].strip().lower())
            self._abort_if_unique_id_configured()
            try:
                await _validate_key(self.hass, user_input[CONF_ADMIN_API_KEY])
            except AnthropicAuthError as err:
                _LOGGER.warning("Anthropic Usage Monitor setup authentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except AnthropicUsageError as err:
                _LOGGER.warning("Anthropic Usage Monitor setup validation failed: %s", err)
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_ORG_NAME],
                    data=user_input,
                    options={
                        CONF_POLL_INTERVAL_MINUTES: user_input[CONF_POLL_INTERVAL_MINUTES],
                        CONF_MONTHLY_BUDGET: user_input.get(CONF_MONTHLY_BUDGET),
                        CONF_DAILY_SPEND_WARNING: user_input.get(CONF_DAILY_SPEND_WARNING),
                        CONF_REMAINING_CREDIT_WARNING: user_input.get(
                            CONF_REMAINING_CREDIT_WARNING
                        ),
                        CONF_API_KEY_ALIASES: "{}",
                        CONF_WORKSPACE_ALIASES: "{}",
                        CONF_TOP_N_MODELS: DEFAULT_TOP_N_MODELS,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_setup_schema(),
            errors=errors,
        )


    async def async_step_reauth(
        self, _entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate_key(self.hass, user_input[CONF_ADMIN_API_KEY])
            except AnthropicAuthError as err:
                _LOGGER.warning("Anthropic Usage Monitor reauth failed: %s", err)
                errors["base"] = "invalid_auth"
            except AnthropicUsageError as err:
                _LOGGER.warning("Anthropic Usage Monitor reauth validation failed: %s", err)
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_update_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_ADMIN_API_KEY: user_input[CONF_ADMIN_API_KEY]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_ADMIN_API_KEY): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return AnthropicUsageOptionsFlow()


class AnthropicUsageOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in (CONF_API_KEY_ALIASES, CONF_WORKSPACE_ALIASES):
                try:
                    json.loads(user_input.get(key) or "{}")
                except json.JSONDecodeError:
                    errors[key] = "invalid_json"
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry),
            errors=errors,
        )


async def _validate_key(hass, api_key: str) -> None:
    client = AnthropicAdminClient(async_get_clientsession(hass), api_key)
    await client.validate_key()


def _setup_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ADMIN_API_KEY): str,
            vol.Required(CONF_ORG_NAME, default=DEFAULT_ORG_NAME): str,
            vol.Optional(CONF_MONTHLY_BUDGET): vol.Coerce(float),
            vol.Optional(CONF_DAILY_SPEND_WARNING): vol.Coerce(float),
            vol.Optional(CONF_REMAINING_CREDIT_WARNING): vol.Coerce(float),
            vol.Required(
                CONF_POLL_INTERVAL_MINUTES, default=DEFAULT_POLL_INTERVAL_MINUTES
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL_MINUTES)),
        }
    )


def _options_schema(entry: config_entries.ConfigEntry) -> vol.Schema:
    options = entry.options
    return vol.Schema(
        {
            vol.Required(
                CONF_POLL_INTERVAL_MINUTES,
                default=options.get(CONF_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL_MINUTES)),
            vol.Optional(
                CONF_MONTHLY_BUDGET, default=options.get(CONF_MONTHLY_BUDGET)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_DAILY_SPEND_WARNING,
                default=options.get(CONF_DAILY_SPEND_WARNING),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_REMAINING_CREDIT_WARNING,
                default=options.get(CONF_REMAINING_CREDIT_WARNING),
            ): vol.Coerce(float),
            vol.Required(
                CONF_API_KEY_ALIASES,
                default=options.get(CONF_API_KEY_ALIASES, "{}"),
            ): str,
            vol.Required(
                CONF_WORKSPACE_ALIASES,
                default=options.get(CONF_WORKSPACE_ALIASES, "{}"),
            ): str,
            vol.Required(
                CONF_TOP_N_MODELS,
                default=options.get(CONF_TOP_N_MODELS, DEFAULT_TOP_N_MODELS),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=25)),
        }
    )
