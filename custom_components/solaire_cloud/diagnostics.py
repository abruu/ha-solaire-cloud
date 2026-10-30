"""Diagnostics for Solaire Cloud (share code, key and IV are redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_AES_IV, CONF_AES_KEY, CONF_SHARE_CODE
from .coordinator import SolaireConfigEntry

TO_REDACT = {CONF_SHARE_CODE, CONF_AES_KEY, CONF_AES_IV}
CURVES = {"power_curve_w", "clear_curve_w"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SolaireConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = {k: v for k, v in (entry.runtime_data.data or {}).items() if k not in CURVES}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "data": {k: str(v) if not isinstance(v, (int, float, type(None))) else v for k, v in data.items()},
    }
