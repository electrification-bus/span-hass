"""Sensor platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CAPABILITY_CONNECTION,
    COUNTER_DECREASE_DEADBAND_WH,
    DEVICE_TYPE_PV,
)
from .entity_base import SpanEbusEntity, async_setup_platform_entities
from .node_mappers import EntitySpec, device_type_short

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SPAN sensor entities from a config entry."""
    async_setup_platform_entities(
        hass, entry, Platform.SENSOR, async_add_entities, SpanEbusSensor
    )


class SpanEbusSensor(SpanEbusEntity, SensorEntity):
    """A sensor entity for a SPAN Panel Homie property."""

    _NUMERIC_DEVICE_CLASSES = {
        SensorDeviceClass.POWER,
        SensorDeviceClass.ENERGY,
        SensorDeviceClass.ENERGY_STORAGE,
        SensorDeviceClass.BATTERY,
        SensorDeviceClass.CURRENT,
        SensorDeviceClass.VOLTAGE,
        SensorDeviceClass.DURATION,
    }

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the sensor."""
        super().__init__(panel=panel, spec=spec)
        self._spec = spec

        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_native_unit_of_measurement = spec.native_unit
        self._attr_entity_category = spec.entity_category
        if spec.icon:
            self._attr_icon = spec.icon

        # Per AN-001, suppress monotonicity-violating decreases on
        # TOTAL_INCREASING energy counters — SPAN firmware occasionally
        # recalibrates and would otherwise create MWh-scale false spikes in
        # the HA recorder. We track the last numeric value separately from
        # ``_attr_native_value`` because HA's StateType union is wider than
        # float and mypy can't narrow it back for arithmetic.
        self._counter_decrease_suppressed = False
        # Whether the current suppression episode was large enough to report.
        # Keeps the recovery notice at the same volume as the notice that
        # opened the episode, so a sub-deadband blip stays entirely quiet.
        self._counter_decrease_notable = False
        self._last_numeric: float | None = None

        # Sticky: once we observe this circuit feeds a PV device, suppress the
        # active-power sign flip permanently (see ``_should_negate``).
        self._feeds_pv = False

    def _should_negate(self) -> bool:
        """Whether to flip the sign of this update's value.

        ``negate`` is the static default (load circuits report consumption as
        negative → flip to positive). For a ``pv_sign_aware`` sensor (circuit
        active-power), a PV-feed circuit is the exception: raw eBus already
        reports generation as positive, so the flip must be suppressed. The
        PV determination reads the live ``connection/feeds-device-type`` — a
        retained sibling property that may not have arrived when the entity was
        built, so re-check until detected, then cache (it does not change at
        runtime).
        """
        if not self._spec.negate:
            return False
        if not self._spec.pv_sign_aware:
            return True
        if not self._feeds_pv:
            feeds = self._panel.get_property_value(
                self._device_id, CAPABILITY_CONNECTION, "feeds-device-type"
            )
            if device_type_short(feeds or "") == DEVICE_TYPE_PV:
                self._feeds_pv = True
        return not self._feeds_pv

    def _decrease_deadband(self) -> float:
        """Smallest counter decrease worth reporting, in this sensor's own unit.

        The threshold is defined in Wh and converted, so a counter published in
        kWh does not inherit a deadband a thousand times too permissive. An
        unrecognized unit falls back to the raw figure, which is the
        conservative direction: it can only make the guard noisier, never
        quieter.
        """
        unit = self._attr_native_unit_of_measurement
        if unit == UnitOfEnergy.WATT_HOUR or unit is None:
            return COUNTER_DECREASE_DEADBAND_WH
        try:
            return EnergyConverter.convert(
                COUNTER_DECREASE_DEADBAND_WH, UnitOfEnergy.WATT_HOUR, unit
            )
        except (HomeAssistantError, ValueError):
            return COUNTER_DECREASE_DEADBAND_WH

    def _update_from_value(self, value: str) -> None:
        """Update sensor state from a raw MQTT value."""
        if self.device_class in self._NUMERIC_DEVICE_CLASSES:
            try:
                numeric = float(value)
            except (ValueError, TypeError):
                self._attr_native_value = None
                self._last_numeric = None
                return
            if self._should_negate():
                numeric = -numeric
            prev = self._last_numeric
            if (
                self._attr_state_class == SensorStateClass.TOTAL_INCREASING
                and prev is not None
                and numeric < prev
            ):
                if not self._counter_decrease_suppressed:
                    self._counter_decrease_suppressed = True
                    self._counter_decrease_notable = (
                        prev - numeric > self._decrease_deadband()
                    )
                    _LOGGER.log(
                        logging.WARNING if self._counter_decrease_notable else logging.DEBUG,
                        "Energy counter decrease suppressed for %s: "
                        "%.1f → %.1f (Δ%.1f %s); holding previous value",
                        self.entity_id,
                        prev,
                        numeric,
                        prev - numeric,
                        self._attr_native_unit_of_measurement or "",
                    )
                return
            if (
                self._counter_decrease_suppressed
                and prev is not None
                and numeric >= prev
            ):
                _LOGGER.log(
                    logging.INFO if self._counter_decrease_notable else logging.DEBUG,
                    "Energy counter for %s caught up (%.1f); resuming normal tracking",
                    self.entity_id,
                    numeric,
                )
                self._counter_decrease_suppressed = False
                self._counter_decrease_notable = False
            self._attr_native_value = numeric
            self._last_numeric = numeric
        else:
            self._attr_native_value = value
