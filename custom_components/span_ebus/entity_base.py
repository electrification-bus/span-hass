"""Push-based entity base for SPAN Panel (eBus) integration."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .node_mappers import EntitySpec
from .span_panel import SpanPanel
from .util import (
    descendant_device_info,
    make_unique_id,
    panel_device_info,
)

_LOGGER = logging.getLogger(__name__)


class SpanEbusEntity(Entity):
    """Base entity for SPAN Panel (eBus) — push-based, no polling."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, panel: SpanPanel, spec: EntitySpec) -> None:
        """Initialize the entity."""
        self._panel = panel
        self._device_id = spec.device_id
        self._capability = spec.capability
        self._property_id = spec.property_id
        self._source_property_id = spec.source_property_id or spec.property_id

        self._attr_unique_id = make_unique_id(
            panel.serial_number, spec.device_id, spec.capability, spec.property_id
        )
        self._attr_name = spec.name

        self._attr_device_info = _device_info_for_spec(panel, spec)

        self._unregister_property: Callable[[], None] | None = None
        self._unregister_availability: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Per Homie 5 effective-state, propagate the root's non-ready state down.

        A child device's ``available`` flips false whenever the root is init /
        disconnected / lost / sleeping, without each descendant having to
        republish its own state.
        """
        return self._panel.is_device_available(self._device_id)

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added to HA."""
        self._unregister_property = self._panel.register_property_callback(
            self._device_id,
            self._capability,
            self._source_property_id,
            self._on_value_update,
        )
        self._unregister_availability = self._panel.register_availability_callback(
            self._device_id, self._on_availability_update
        )

        current = self._panel.get_property_value(
            self._device_id, self._capability, self._source_property_id
        )
        if current is not None:
            self._update_from_value(current)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister callbacks when entity is removed."""
        if self._unregister_property:
            self._unregister_property()
        if self._unregister_availability:
            self._unregister_availability()

    def _on_value_update(self, value: str) -> None:
        """Handle a property value update from MQTT (HA event loop)."""
        self._update_from_value(value)
        self.async_write_ha_state()

    def _on_availability_update(self, available: bool) -> None:
        """Handle availability change (HA event loop)."""
        self.async_write_ha_state()

    @abstractmethod
    def _update_from_value(self, value: str) -> None:
        """Update entity state from a raw MQTT property value."""


def _device_info_for_spec(panel: SpanPanel, spec: EntitySpec) -> DeviceInfo:
    """Pick the right DeviceInfo for the entity's owning device.

    Panel-root entities go on the panel device; descendant entities go on
    per-descendant child devices that the integration registers in
    ``__init__.py``.
    """
    if spec.device_id == panel.serial_number:
        return panel_device_info(panel.serial_number)
    return descendant_device_info(
        panel_serial=panel.serial_number,
        device_id=spec.device_id,
        device_type=spec.device_type,
        device_name=spec.device_name,
        parent_device_id=spec.via_device_id or None,
    )
