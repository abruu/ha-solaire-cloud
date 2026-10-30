"""Offline tests for the analysis helpers and the API client (dummy key, mocked HTTP)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.solaire_cloud import analysis
from custom_components.solaire_cloud.api import (
    SolaireApiError,
    SolaireClient,
    SolaireConnectionError,
    SolaireCryptoError,
    parse_share,
    valid_key,
)

from .conftest import DUMMY_CODE, DUMMY_IV, DUMMY_KEY, sample_result

URL = re.compile(r"https://www\.solairecloud\.com/mesh/web/v1/public/share/display/houseHold/screen/getUserOverview.*")
IST = timezone(timedelta(hours=5, minutes=30))


def test_parse_share_and_key_validation():
    assert parse_share(f"https://x/#/public/share/data-screen/?shareCode={DUMMY_CODE}&domain=y") == DUMMY_CODE
    assert parse_share(DUMMY_CODE) == DUMMY_CODE
    assert parse_share("nope!") is None
    assert parse_share(None) is None
    assert valid_key(DUMMY_KEY) and not valid_key("short") and not valid_key(None)


def test_stop_analysis():
    assert analysis.stop_analysis([None, 0, 0, 100, 200, 0, 0, 300, 400, 0, 500, 0, 0])[:2] == (15, 2)
    assert analysis.stop_analysis([0, 0, None]) == (0, 0, 0.0)


def test_clear_curve_shape():
    curve = analysis.clear_curve(datetime(2026, 6, 21), 12.0, 77.0, 5.5, 3.3)
    assert len(curve) == 288 and curve[0] == 0
    assert 1500 < max(curve) <= 3300
    assert curve.index(max(curve)) // 12 in range(10, 15)


def test_build_overview():
    now = datetime(2026, 10, 30, 13, 30, tzinfo=IST)
    out = analysis.build_overview(sample_result(), now, [100] * 288)
    assert out["pv_power_w"] == 1234.0
    assert out["yield_today_kwh"] == 7.5 and out["yield_total_kwh"] == 5000.5
    assert out["load_today_kwh"] == 9.1 and out["grid_import_today_kwh"] == 0.0
    assert out["installed_kwp"] == 3.3
    assert out["stopped_minutes"] == 15 and out["stop_count"] == 1
    assert out["first_light_time"] == datetime(2026, 10, 30, 6, 0, tzinfo=IST)
    assert out["last_light_time"] == datetime(2026, 10, 30, 11, 55, tzinfo=IST)
    assert out["pv_peak_time"].hour == 11
    assert out["yield_yesterday_kwh"] == pytest.approx(12.0 + 2.9)
    assert out["avg_day_kwh"] is not None and out["month_projection_kwh"] is not None
    assert out["clear_energy_kwh"] == round(100 * 288 * 5 / 60 / 1000, 2)
    assert analysis.build_overview({}, now, None)["pv_power_w"] is None  # empty payload does not crash


async def test_fetch_decrypts_response(hass: HomeAssistant, aioclient_mock):
    client = SolaireClient(async_get_clientsession(hass), DUMMY_CODE, DUMMY_KEY, DUMMY_IV)
    inner = {"success": True, "code": 200, "result": {"hello": "world"}}
    aioclient_mock.post(URL, text=json.dumps({"data": client._encrypt(json.dumps(inner))}))
    assert await client.fetch(datetime(2026, 10, 30, 12, tzinfo=IST)) == {"hello": "world"}
    sent = aioclient_mock.mock_calls[0]
    assert sent[3]["shareCode"] == DUMMY_CODE  # plain header
    assert client._decrypt(json.loads(sent[2])["data"]).startswith('{"curDate":"2026-10-30"')


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"text": json.dumps({"success": False, "code": 401})}, SolaireApiError),
        ({"text": "not json"}, SolaireApiError),
        ({"text": json.dumps({"data": "AAAAAAAAAAAAAAAAAAAAAA=="})}, SolaireCryptoError),
        ({"status": 500}, SolaireConnectionError),
        ({"exc": TimeoutError()}, SolaireConnectionError),
    ],
)
async def test_fetch_errors(hass: HomeAssistant, aioclient_mock, kwargs, error):
    client = SolaireClient(async_get_clientsession(hass), DUMMY_CODE, DUMMY_KEY, DUMMY_IV)
    aioclient_mock.post(URL, **kwargs)
    with pytest.raises(error) as err:
        await client.fetch(datetime(2026, 10, 30, 12, tzinfo=IST))
    assert DUMMY_CODE not in str(err.value) and DUMMY_KEY not in str(err.value)


def test_client_rejects_bad_key(hass: HomeAssistant):
    with pytest.raises(ValueError):
        SolaireClient(None, DUMMY_CODE, "short", DUMMY_IV)
