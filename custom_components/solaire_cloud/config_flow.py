"""Config flow for Solaire Cloud."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import (
    SolaireApiError,
    SolaireClient,
    SolaireConnectionError,
    SolaireCryptoError,
    parse_share,
    valid_key,
)
from .const import (
    CONF_AES_IV,
    CONF_AES_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SHARE_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import SolaireConfigEntry

_LOGGER = logging.getLogger(__name__)

CONF_LINK = "share_link"

_TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _async_check(hass: HomeAssistant, link: str, key: str, iv: str) -> tuple[str | None, str | None]:
    """Validate the inputs against the live endpoint; returns (share_code, error_key)."""
    code = parse_share(link)
    if code is None:
        return None, "invalid_link"
    if not valid_key(key) or not valid_key(iv):
        return code, "invalid_key"
    try:
        await SolaireClient(async_get_clientsession(hass), code, key, iv).fetch(dt_util.now())
    except SolaireConnectionError:
        return code, "cannot_connect"
    except SolaireCryptoError:
        return code, "invalid_key"
    except SolaireApiError:
        return code, "invalid_auth"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error while checking the Solaire Cloud share link")
        return code, "unknown"
    return code, None


class SolaireConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solaire Cloud."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the share link and the AES key/IV."""
        errors: dict[str, str] = {}
        if user_input is not None:
            code, error = await _async_check(
                self.hass, user_input[CONF_LINK], user_input[CONF_AES_KEY], user_input[CONF_AES_IV]
            )
            if error is None and code is not None:
                self._async_abort_entries_match({CONF_SHARE_CODE: code})
                return self.async_create_entry(
                    title="Solaire Cloud",
                    data={
                        CONF_SHARE_CODE: code,
                        CONF_AES_KEY: user_input[CONF_AES_KEY],
                        CONF_AES_IV: user_input[CONF_AES_IV],
                    },
                )
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINK): _TEXT,
                    vol.Required(CONF_AES_KEY): _SECRET,
                    vol.Required(CONF_AES_IV): _SECRET,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The share link was rejected: ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect a new share link; the key and IV stay as they are."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            code, error = await _async_check(
                self.hass, user_input[CONF_LINK], entry.data[CONF_AES_KEY], entry.data[CONF_AES_IV]
            )
            if error is None and code is not None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_SHARE_CODE: code})
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_LINK): _TEXT}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the share link and/or the AES key and IV."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            code, error = await _async_check(
                self.hass, user_input[CONF_LINK], user_input[CONF_AES_KEY], user_input[CONF_AES_IV]
            )
            if error is None and code is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_SHARE_CODE: code,
                        CONF_AES_KEY: user_input[CONF_AES_KEY],
                        CONF_AES_IV: user_input[CONF_AES_IV],
                    },
                )
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINK): _TEXT,
                    vol.Required(CONF_AES_KEY, default=entry.data[CONF_AES_KEY]): _SECRET,
                    vol.Required(CONF_AES_IV, default=entry.data[CONF_AES_IV]): _SECRET,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SolaireConfigEntry) -> SolaireOptionsFlow:
        """Create the options flow."""
        return SolaireOptionsFlow()


class SolaireOptionsFlow(OptionsFlowWithReload):
    """Polling interval; the entry reloads when it changes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])})
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=10,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    )
                }
            ),
        )
