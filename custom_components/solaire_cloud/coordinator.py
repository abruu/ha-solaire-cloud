"""Data update coordinator for Solaire Cloud."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .analysis import build_overview, clear_curve, installed_kwp
from .api import (
    SolaireApiError,
    SolaireClient,
    SolaireConnectionError,
    SolaireCryptoError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_BACKOFF,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REJECTS_BEFORE_REAUTH,
)

_LOGGER = logging.getLogger(__name__)

type SolaireConfigEntry = ConfigEntry[SolaireCoordinator]


class SolaireCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the share page once per interval and backs off after failures."""

    config_entry: SolaireConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SolaireConfigEntry, client: SolaireClient) -> None:
        interval = min(
            max(int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)), MIN_SCAN_INTERVAL),
            MAX_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._client = client
        self._failures = 0
        self._rejects = 0
        self._model: tuple[tuple[date, float], list[int]] | None = None

    def _clear_sky(self, now: datetime, kwp: float | None) -> list[int] | None:
        """Ideal output curve for today, cached per day; None without a location or array size."""
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        if not kwp or (lat == 0 and lon == 0):
            return None
        key = (now.date(), kwp)
        if self._model is None or self._model[0] != key:
            tz_h = (now.utcoffset() or timedelta()).total_seconds() / 3600
            day0 = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
            self._model = (key, clear_curve(day0, lat, lon, tz_h, kwp))
        return self._model[1]

    def _next_backoff(self) -> float:
        self._failures += 1
        return min(60 * 2 ** (self._failures - 1), MAX_BACKOFF)

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        try:
            res = await self._client.fetch(now)
        except SolaireConnectionError as err:
            raise UpdateFailed(str(err), retry_after=self._next_backoff()) from err
        except (SolaireApiError, SolaireCryptoError) as err:
            self._rejects += 1
            if self._rejects >= REJECTS_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err), retry_after=self._next_backoff()) from err
        self._failures = 0
        self._rejects = 0
        return build_overview(res, now, self._clear_sky(now, installed_kwp(res)))
