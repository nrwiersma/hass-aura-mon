"""Config flow for the AuraMon integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuraMonClient
from .const import CONF_WEBHOOK_ID, CONNECTION_ERRORS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


async def _validate_input(hass: HomeAssistant, host: str) -> str:
    """Validate the host is reachable and return the device's MAC address.

    Raises CannotConnect on failure.
    """
    client = AuraMonClient(host, async_get_clientsession(hass))
    try:
        status = await client.get_status()
    except CONNECTION_ERRORS as err:
        raise CannotConnect from err

    if not status.network or not status.network.mac:
        raise CannotConnect

    return status.network.mac


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AuraMon."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._webhook_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            try:
                mac = await _validate_input(self.hass, host)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                self._host = host
                self._webhook_id = webhook.async_generate_id()
                return await self.async_step_webhook()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_webhook(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the generated webhook URL for the user to paste into the firmware.

        Live readings are pushed to this webhook rather than polled (see
        WEBHOOK_INGESTION.md); the device still needs this URL configured manually,
        out-of-band, in its uploader settings.
        """
        assert self._host is not None
        assert self._webhook_id is not None

        if user_input is not None:
            return self.async_create_entry(
                title=self._host,
                data={CONF_HOST: self._host, CONF_WEBHOOK_ID: self._webhook_id},
            )

        webhook_url = webhook.async_generate_url(self.hass, self._webhook_id)
        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema({}),
            description_placeholders={"webhook_url": webhook_url},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Return the options flow, used to re-display the webhook URL on demand."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow to change the device host and re-display the webhook URL.

    The webhook URL is shown only once during initial setup, so it is repeated here to
    keep it reachable via the integration's "Configure" button.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit the host and show the webhook URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            try:
                mac = await _validate_input(self.hass, host)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if self.config_entry.unique_id not in (None, mac):
                    errors["base"] = "wrong_device"
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        title=host,
                        data={**self.config_entry.data, CONF_HOST: host},
                    )
                    return self.async_create_entry(title="", data={})

        webhook_id = self.config_entry.data.get(CONF_WEBHOOK_ID)
        webhook_url = (
            webhook.async_generate_url(self.hass, webhook_id)
            if webhook_id
            else "Not yet generated - reload the integration to generate one."
        )
        current_host = (user_input or self.config_entry.data).get(CONF_HOST)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=current_host): str}),
            errors=errors,
            description_placeholders={"webhook_url": webhook_url},
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect to the AuraMon device."""
