"""Solaire Cloud: solar data from a public share link."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolaireClient
from .const import CONF_AES_IV, CONF_AES_KEY, CONF_SHARE_CODE
from .coordinator import SolaireConfigEntry, SolaireCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SolaireConfigEntry) -> bool:
    """Set up Solaire Cloud from a config entry."""
    try:
        client = SolaireClient(
            async_get_clientsession(hass),
            entry.data[CONF_SHARE_CODE],
            entry.data[CONF_AES_KEY],
            entry.data[CONF_AES_IV],
        )
    except ValueError as err:
        raise ConfigEntryError("AES key and IV must be 16 characters; reconfigure the integration") from err

    coordinator = SolaireCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolaireConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
