"""Sensors for Solaire Cloud."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolaireConfigEntry, SolaireCoordinator

PARALLEL_UPDATES = 0

_KWH = UnitOfEnergy.KILO_WATT_HOUR
_ENERGY = SensorDeviceClass.ENERGY
_TOTAL = SensorStateClass.TOTAL_INCREASING


def _energy(key: str, *, total: bool = False, enabled: bool = True) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=_ENERGY,
        native_unit_of_measurement=_KWH,
        state_class=_TOTAL if total else None,
        suggested_display_precision=2,
        entity_registry_enabled_default=enabled,
    )


SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="pv_power_w",
        translation_key="pv_power_w",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _energy("yield_today_kwh", total=True),
    _energy("yield_month_kwh", total=True),
    _energy("yield_year_kwh", total=True),
    _energy("yield_total_kwh", total=True),
    _energy("load_today_kwh", total=True),
    _energy("grid_import_today_kwh", total=True),
    _energy("grid_export_today_kwh", total=True),
    SensorEntityDescription(
        key="self_use_today_pct",
        translation_key="self_use_today_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="pv_peak_today_w",
        translation_key="pv_peak_today_w",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _energy("yield_yesterday_kwh"),
    _energy("yield_7d_kwh"),
    _energy("best_day_kwh"),
    _energy("avg_day_kwh"),
    _energy("month_projection_kwh"),
    _energy("clear_energy_kwh"),
    _energy("lost_kwh_est"),
    _energy("energy_morning_kwh", enabled=False),
    _energy("energy_midday_kwh", enabled=False),
    _energy("energy_afternoon_kwh", enabled=False),
    _energy("load_month_kwh", total=True, enabled=False),
    _energy("grid_import_month_kwh", total=True, enabled=False),
    _energy("grid_export_month_kwh", total=True, enabled=False),
    SensorEntityDescription(
        key="self_sufficiency_month_pct",
        translation_key="self_sufficiency_month_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="production_hours",
        translation_key="production_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="equivalent_hours",
        translation_key="equivalent_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="stopped_minutes",
        translation_key="stopped_minutes",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="stop_count",
        translation_key="stop_count",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="first_light_time",
        translation_key="first_light_time",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="last_light_time",
        translation_key="last_light_time",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="pv_peak_time",
        translation_key="pv_peak_time",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key="installed_kwp",
        translation_key="installed_kwp",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolaireConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SolaireSensor(coordinator, entry, description) for description in SENSORS)


class SolaireSensor(CoordinatorEntity[SolaireCoordinator], SensorEntity):
    """One value of the Solaire overview."""

    _attr_has_entity_name = True
    # The two 288-point curves would be written to the database on every poll.
    _unrecorded_attributes = frozenset({"power_curve_w", "clear_curve_w"})

    def __init__(
        self,
        coordinator: SolaireCoordinator,
        entry: SolaireConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solaire Cloud",
            manufacturer="Solaire",
            model="Public share page",
        )

    @property
    def native_value(self) -> float | int | datetime | None:
        """Current value."""
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        """Unavailable while polling fails or the site reports no value (for example no data today)."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """The 5-minute curves hang off the power sensor."""
        if self.entity_description.key != "pv_power_w":
            return None
        data = self.coordinator.data
        return {"power_curve_w": data.get("power_curve_w"), "clear_curve_w": data.get("clear_curve_w")}
