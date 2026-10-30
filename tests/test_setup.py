"""Setup, sensors, failures and diagnostics."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.solaire_cloud.api import SolaireApiError, SolaireConnectionError
from custom_components.solaire_cloud.const import DOMAIN

from .conftest import DUMMY_CODE, DUMMY_IV, DUMMY_KEY


def _eid(hass: HomeAssistant, entry, key: str) -> str:
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert eid, key
    return eid


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors_have_expected_values(hass: HomeAssistant, mock_fetch, entry):
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    st = hass.states.get(_eid(hass, entry, "pv_power_w"))
    assert st.state == "1234.0" and st.attributes["unit_of_measurement"] == "W"
    assert st.attributes["device_class"] == "power" and st.attributes["state_class"] == "measurement"
    assert len(st.attributes["power_curve_w"]) == 288 and len(st.attributes["clear_curve_w"]) == 288
    total = hass.states.get(_eid(hass, entry, "yield_total_kwh"))
    assert total.state == "5000.5" and total.attributes["state_class"] == "total_increasing"
    assert total.attributes["device_class"] == "energy"
    assert hass.states.get(_eid(hass, entry, "yield_today_kwh")).state == "7.5"
    assert hass.states.get(_eid(hass, entry, "stop_count")).state == "1"
    assert hass.states.get(_eid(hass, entry, "first_light_time")).state.startswith(dt_util.now().strftime("%Y-%m-%d"))
    # the entity id of the power sensor follows the device name
    assert _eid(hass, entry, "pv_power_w") == "sensor.solaire_cloud_pv_power"


async def test_missing_value_makes_sensor_unavailable(hass: HomeAssistant, mock_fetch, entry):
    result = mock_fetch.return_value
    result["energyList"]["load"] = []  # no load point today
    await _setup(hass, entry)
    assert hass.states.get(_eid(hass, entry, "load_today_kwh")).state == "unavailable"
    assert hass.states.get(_eid(hass, entry, "yield_today_kwh")).state == "7.5"


async def test_first_refresh_failure_retries(hass: HomeAssistant, mock_fetch, entry):
    mock_fetch.side_effect = SolaireConnectionError("down")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_poll_failure_backs_off_then_recovers(hass: HomeAssistant, mock_fetch, entry, freezer):
    await _setup(hass, entry)
    pv = _eid(hass, entry, "pv_power_w")

    async def tick(seconds: int) -> None:
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    mock_fetch.side_effect = SolaireConnectionError("down")
    await tick(61)  # regular poll fails; next try in 60 s
    assert hass.states.get(pv).state == "unavailable"
    calls = mock_fetch.call_count
    await tick(30)  # inside the back-off window: no request
    assert mock_fetch.call_count == calls
    await tick(31)  # second failure; next try in 120 s
    assert mock_fetch.call_count == calls + 1
    await tick(61)
    assert mock_fetch.call_count == calls + 1  # still backing off
    mock_fetch.side_effect = None
    await tick(61)  # back-off over, the site is reachable again
    assert hass.states.get(pv).state == "1234.0"


async def test_repeated_rejection_starts_reauth(hass: HomeAssistant, mock_fetch, entry, freezer):
    await _setup(hass, entry)
    mock_fetch.side_effect = SolaireApiError("API error code=401")
    for _ in range(4):
        freezer.tick(timedelta(seconds=700))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_unload(hass: HomeAssistant, mock_fetch, entry):
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redacts_secrets(hass: HomeAssistant, mock_fetch, entry):
    from custom_components.solaire_cloud.diagnostics import async_get_config_entry_diagnostics

    await _setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert DUMMY_CODE not in text and DUMMY_KEY not in text and DUMMY_IV not in text
    assert "power_curve_w" not in diag["data"] and diag["data"]["pv_power_w"] == 1234.0
