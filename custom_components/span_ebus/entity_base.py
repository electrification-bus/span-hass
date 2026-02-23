"""Push-based entity base for SPAN Panel (eBus) integration."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
import logging

from homeassistant.helpers.entity import Entity

from .node_mappers import EntitySpec
from .span_panel import SpanPanel
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .util import (
    _SUB_DEVICE_TYPES,
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
        self._node_id = spec.node_id
        self._property_id = spec.property_id

        self._attr_unique_id = make_unique_id(
            panel.serial_number, spec.node_id, spec.property_id
        )
        self._attr_name = spec.name

        if spec.node_type in _SUB_DEVICE_TYPES:
            # Set the device name here so HA generates consistent entity_ids
            # across all platforms. The name is also managed reactively by
            # _register_subdevices and _on_name_update callbacks in __init__.py
            # for updates after initial setup.
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{panel.serial_number}_{spec.node_id}")},
                name=spec.device_name,
            )
        else:
            self._attr_device_info = panel_device_info(panel.serial_number)

        self._unregister_property: Callable[[], None] | None = None
        self._unregister_availability: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Return True if the panel is available."""
        return self._panel.available

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added to HA."""
        # Register for property updates
        self._unregister_property = self._panel.register_property_callback(
            self._node_id, self._property_id, self._on_value_update
        )
        # Register for availability updates
        self._unregister_availability = self._panel.register_availability_callback(
            self._on_availability_update
        )

        # Set initial value if already known
        current = self._panel.get_property_value(self._node_id, self._property_id)
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
        """Update entity state from a raw MQTT property value.

        Subclasses must implement this to parse the value string
        into the appropriate native state.
        """
