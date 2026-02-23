"""SpanPanel — wraps ebus_sdk.Controller for Home Assistant integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from ebus_sdk.homie import Controller, DiscoveredDevice
from homeassistant.core import HomeAssistant, callback

from .const import EBUS_HOMIE_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Callback type for entity property updates: (value: str) -> None
PropertyCallback = Callable[[str], None]

# Callback type for availability changes: (available: bool) -> None
AvailabilityCallback = Callable[[bool], None]


class SpanPanel:
    """Bridge between ebus_sdk.Controller and Home Assistant.

    Manages one SPAN Panel's MQTT connection and routes property updates
    to registered HA entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        serial_number: str,
        mqtt_cfg: dict[str, Any],
    ) -> None:
        """Initialize the SpanPanel wrapper."""
        self.hass = hass
        self.serial_number = serial_number
        self._mqtt_cfg = mqtt_cfg

        self._controller: Controller | None = None
        self._device: DiscoveredDevice | None = None
        self._available = False

        # Event set when $description is received (used for setup synchronization)
        self.description_received = asyncio.Event()

        # Event set when device reaches "ready" state (all property values available)
        self.device_ready = asyncio.Event()

        # Entity callback registrations: {(node_id, property_id): [callback, ...]}
        self._property_callbacks: dict[tuple[str, str], list[PropertyCallback]] = {}

        # Availability callbacks from entities
        self._availability_callbacks: list[AvailabilityCallback] = []

        # Ready callbacks — fired on every ready transition (not just first)
        self._ready_callbacks: list[Callable[[], None]] = []

    @property
    def available(self) -> bool:
        """Return whether the panel is available."""
        return self._available

    @property
    def device(self) -> DiscoveredDevice | None:
        """Return the discovered device."""
        return self._device

    @property
    def description(self) -> dict | None:
        """Return the device description (parsed JSON from $description)."""
        if self._device is None:
            return None
        return self._device.description

    def register_property_callback(
        self, node_id: str, property_id: str, cb: PropertyCallback
    ) -> Callable[[], None]:
        """Register a callback for a specific node/property update.

        Returns an unregister function.
        """
        key = (node_id, property_id)
        self._property_callbacks.setdefault(key, []).append(cb)

        def unregister() -> None:
            cbs = self._property_callbacks.get(key)
            if cbs and cb in cbs:
                cbs.remove(cb)

        return unregister

    def register_availability_callback(
        self, cb: AvailabilityCallback
    ) -> Callable[[], None]:
        """Register a callback for availability changes.

        Returns an unregister function.
        """
        self._availability_callbacks.append(cb)

        def unregister() -> None:
            if cb in self._availability_callbacks:
                self._availability_callbacks.remove(cb)

        return unregister

    def register_ready_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for device "ready" transitions.

        Fires on every transition to "ready" (not just the first).
        Used by __init__ to refresh device names in the registry.

        Returns an unregister function.
        """
        self._ready_callbacks.append(cb)

        def unregister() -> None:
            if cb in self._ready_callbacks:
                self._ready_callbacks.remove(cb)

        return unregister

    def get_property_value(self, node_id: str, property_id: str) -> str | None:
        """Get the current value of a property."""
        if self._device is None:
            return None
        return self._device.get_property(node_id, property_id)

    def set_property(self, node_id: str, property_id: str, value: str) -> bool:
        """Send a command to set a property value on the panel."""
        if self._controller is None or self._device is None:
            return False
        return self._controller.set_property(
            self._device.device_id, node_id, property_id, value
        )

    async def async_start(self) -> None:
        """Create and start the Controller."""
        self._controller = Controller(
            mqtt_cfg=self._mqtt_cfg,
            homie_domain=EBUS_HOMIE_DOMAIN,
            auto_start=False,
            device_id=self.serial_number,
        )

        self._controller.set_on_device_discovered_callback(self._on_device_discovered)
        self._controller.set_on_description_received_callback(self._on_description_received)
        self._controller.set_on_property_changed_callback(self._on_property_changed)
        self._controller.set_on_device_state_changed_callback(self._on_device_state_changed)

        # Start discovery (runs paho-mqtt loop in background thread)
        self._controller.start_discovery()

    async def async_stop(self) -> None:
        """Stop the Controller and clean up."""
        if self._controller:
            # Controller.stop() is synchronous; run in executor to avoid blocking
            await self.hass.async_add_executor_job(self._controller.stop)
            self._controller = None
        self._device = None
        self._available = False
        # Release callback registrations to break reference cycles
        self._property_callbacks.clear()
        self._availability_callbacks.clear()
        self._ready_callbacks.clear()

    # ── SDK callbacks (called from paho-mqtt thread) ──────────────────────

    def _on_device_discovered(self, device: DiscoveredDevice) -> None:
        """Handle new device discovery (paho-mqtt thread)."""
        if device.device_id != self.serial_number:
            return
        _LOGGER.info("Discovered SPAN Panel %s (state=%s)", device.device_id, device.state)
        self._device = device
        if device.state == "ready":
            self._available = True
            self.hass.loop.call_soon_threadsafe(self.device_ready.set)
            self.hass.loop.call_soon_threadsafe(self._dispatch_ready)

    def _on_description_received(self, device: DiscoveredDevice) -> None:
        """Handle $description received (paho-mqtt thread)."""
        if device.device_id != self.serial_number:
            return
        _LOGGER.info("Received description for %s", device.device_id)
        self._device = device
        # Signal the HA event loop that description is ready
        self.hass.loop.call_soon_threadsafe(self.description_received.set)

    def _on_property_changed(
        self,
        device_id: str,
        node_id: str,
        property_id: str,
        value: str,
        old_value: str | None,
    ) -> None:
        """Handle property value change (paho-mqtt thread)."""
        if device_id != self.serial_number:
            return

        key = (node_id, property_id)
        callbacks = self._property_callbacks.get(key, [])
        if callbacks:
            # Bridge to HA event loop
            self.hass.loop.call_soon_threadsafe(
                self._dispatch_property_update, key, value
            )

    def _on_device_state_changed(
        self,
        device: DiscoveredDevice,
        old_state: str,
        new_state: str,
    ) -> None:
        """Handle device state change (paho-mqtt thread)."""
        if device.device_id != self.serial_number:
            return

        _LOGGER.info(
            "SPAN Panel %s state: %s → %s", device.device_id, old_state, new_state
        )
        available = new_state == "ready"
        if available != self._available:
            self._available = available
            self.hass.loop.call_soon_threadsafe(
                self._dispatch_availability, available
            )

        if new_state == "ready":
            self.hass.loop.call_soon_threadsafe(self.device_ready.set)
            self.hass.loop.call_soon_threadsafe(self._dispatch_ready)

    # ── HA event loop dispatchers ─────────────────────────────────────────

    @callback
    def _dispatch_property_update(
        self, key: tuple[str, str], value: str
    ) -> None:
        """Dispatch property update to registered entity callbacks (HA event loop)."""
        # Snapshot the list to avoid issues if callbacks are added/removed during iteration
        for cb in list(self._property_callbacks.get(key, [])):
            try:
                cb(value)
            except Exception:
                _LOGGER.exception("Error in property callback for %s", key)

    @callback
    def _dispatch_availability(self, available: bool) -> None:
        """Dispatch availability change to registered entity callbacks (HA event loop)."""
        for cb in list(self._availability_callbacks):
            try:
                cb(available)
            except Exception:
                _LOGGER.exception("Error in availability callback")

    @callback
    def _dispatch_ready(self) -> None:
        """Dispatch ready notification to registered callbacks (HA event loop)."""
        for cb in list(self._ready_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in ready callback")
