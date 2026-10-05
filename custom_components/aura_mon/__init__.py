"""The AuraMon integration."""
from __future__ import annotations

from homeassistant.components import webhook
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuraMonClient
from .const import CONF_WEBHOOK_ID, DOMAIN
from .coordinator import AuraMonConfigEntry, AuraMonDataUpdateCoordinator
from .webhook import handle_webhook

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: AuraMonConfigEntry) -> bool:
    """Set up AuraMon from a config entry."""
    client = AuraMonClient(entry.data[CONF_HOST], async_get_clientsession(hass))
    coordinator = AuraMonDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    webhook.async_register(
        hass, DOMAIN, entry.title, entry.data[CONF_WEBHOOK_ID], handle_webhook
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AuraMonConfigEntry) -> bool:
    """Unload a config entry."""
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

