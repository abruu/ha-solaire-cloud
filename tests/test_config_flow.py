"""Config, reauth, reconfigure and options flows."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.solaire_cloud.api import SolaireApiError, SolaireConnectionError, SolaireCryptoError
from custom_components.solaire_cloud.const import (
    CONF_AES_IV,
    CONF_AES_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SHARE_CODE,
    DOMAIN,
)

from .conftest import DUMMY_CODE, DUMMY_IV, DUMMY_KEY

LINK = f"https://www.solairecloud.com/#/public/share/data-screen/?shareCode={DUMMY_CODE}&domain=https://www.solairecloud.com"
FORM = {"share_link": LINK, CONF_AES_KEY: DUMMY_KEY, CONF_AES_IV: DUMMY_IV}


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_fetch):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FORM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_SHARE_CODE: DUMMY_CODE, CONF_AES_KEY: DUMMY_KEY, CONF_AES_IV: DUMMY_IV}


@pytest.mark.parametrize(
    ("form", "side_effect", "error"),
    [
        ({**FORM, "share_link": "garbage!"}, None, "invalid_link"),
        ({**FORM, CONF_AES_KEY: "short"}, None, "invalid_key"),
        (FORM, SolaireCryptoError("x"), "invalid_key"),
        (FORM, SolaireApiError("x"), "invalid_auth"),
        (FORM, SolaireConnectionError("x"), "cannot_connect"),
        (FORM, RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(hass: HomeAssistant, mock_fetch, form, side_effect, error):
    mock_fetch.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}
    mock_fetch.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FORM)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_share_code_aborts(hass: HomeAssistant, mock_fetch, entry):
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], FORM)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_updates_share_code(hass: HomeAssistant, mock_fetch, entry):
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reauth_confirm"
    new_code = "f" * 32
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"share_link": f"?shareCode={new_code}&x"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_SHARE_CODE] == new_code
    assert entry.data[CONF_AES_KEY] == DUMMY_KEY  # key and IV are kept


async def test_reauth_rejects_bad_link(hass: HomeAssistant, mock_fetch, entry):
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    mock_fetch.side_effect = SolaireApiError("x")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"share_link": "e" * 32})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_SHARE_CODE] == DUMMY_CODE


async def test_reconfigure(hass: HomeAssistant, mock_fetch, entry):
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"share_link": "d" * 32, CONF_AES_KEY: "k" * 16, CONF_AES_IV: "i" * 16}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert (entry.data[CONF_SHARE_CODE], entry.data[CONF_AES_KEY]) == ("d" * 32, "k" * 16)


async def test_options_flow_sets_interval_and_reloads(hass: HomeAssistant, mock_fetch, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval.total_seconds() == 60
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 120})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SCAN_INTERVAL] == 120
    assert entry.runtime_data.update_interval.total_seconds() == 120
