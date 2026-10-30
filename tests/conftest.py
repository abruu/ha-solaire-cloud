"""Shared fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaire_cloud.const import CONF_AES_IV, CONF_AES_KEY, CONF_SHARE_CODE, DOMAIN

DUMMY_KEY = "0123456789abcdef"
DUMMY_IV = "fedcba9876543210"
DUMMY_CODE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _curve(values: dict[int, int]) -> list[dict]:
    """288 five-minute points keyed by HH:MM; slots not listed are 0 (night) or None (no report)."""
    out = []
    for i in range(288):
        out.append({"xais": f"{i * 5 // 60:02d}:{i * 5 % 60:02d}", "yais": values.get(i)})
    return out


def sample_result(day_key: str = "10-30") -> dict:
    """A trimmed getUserOverview result: 06:00-12:00 production with one stop, no report after 12:00."""
    slots = {i: 0 for i in range(0, 120)}
    for i in range(72, 144):
        slots[i] = 400 + (i - 72) * 20
    for i in (100, 101, 102):  # a 15-minute stop in the middle of the day
        slots[i] = 0
    month = [{"xais": f"10-{d:02d}", "yais": 12.0 + d / 10} for d in range(1, 30)]
    month.append({"xais": day_key, "yais": 7.5})
    return {
        "statisticsInfo": {
            "screenOverview": {
                "todayYield": 7.5,
                "monthYield": 360.0,
                "yearYield": 3000.0,
                "totalYield": 5000.5,
                "selfUseTodayPercent": 100,
                "equivalentHour": 2.27,
            },
            "userScreenInfo": {"realTimePower": 1234.0, "sitePvCapacity": 3.3},
        },
        "powerCurve": {"acPower": _curve(slots)},
        "energyList": {
            "acYield": month,
            "load": [{"xais": day_key, "yais": 9.1}],
            "consumedEnergy": [{"xais": day_key, "yais": 0.0}],
            "feedInEnergy": [{"xais": day_key, "yais": 0.0}],
        },
    }


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components from this repository."""
    yield


@pytest.fixture
def mock_fetch():
    """Patch the network call with a good sample result."""
    with patch(
        "custom_components.solaire_cloud.api.SolaireClient.fetch",
        new_callable=AsyncMock,
        return_value=sample_result(),
    ) as fetch:
        yield fetch


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Solaire Cloud",
        data={CONF_SHARE_CODE: DUMMY_CODE, CONF_AES_KEY: DUMMY_KEY, CONF_AES_IV: DUMMY_IV},
    )
