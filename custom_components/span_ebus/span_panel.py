"""SpanPanel — wraps ebus_sdk.Controller for Home Assistant integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from ebus_sdk import HOMIE_EFFECTIVE_STATE_TABLE
from ebus_sdk.homie import Controller, DeviceState, DiscoveredDevice
from homeassistant.core import HomeAssistant, callback

from .const import EBUS_HOMIE_DOMAIN, MQTT_QOS

_LOGGER = logging.getLogger(__name__)

# Callback type for entity property updates: (value: str) -> None
PropertyCallback = Callable[[str], None]

# Callback type for availability changes: (available: bool) -> None
AvailabilityCallback = Callable[[bool], None]

# Callback type for device removal: (device_id: str) -> None
DeviceRemovedCallback = Callable[[str], None]


class SpanPanel:
    """Bridge between ebus_sdk.Controller and Home Assistant for one SPAN panel tree.

    Uses ``Controller(root_device_id=<panel-serial>)`` so the SDK subscribes to
    the panel root and auto-walks its descendants — lugs, BESS, MID, PV, EVSE,
    and every circuit publish under their own Homie device IDs and the SDK
    reconciles them via ``$description.children``, gated on the parent root's
    ``$state`` init→ready edge (per SDK-o1h).

    Routes property updates from the paho-mqtt thread to entity callbacks
    keyed on ``(device_id, capability, property_id)`` triples.
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

        # Event set when the root's $description is received (used for setup synchronization)
        self.description_received = asyncio.Event()

        # Event set when the root reaches "ready" state (effective-state propagates to descendants)
        self.device_ready = asyncio.Event()

        # Entity callback registrations:
        #   {(device_id, capability, property_id): [callback, ...]}
        self._property_callbacks: dict[tuple[str, str, str], list[PropertyCallback]] = {}

        # Availability callbacks keyed by device-id (or "*" for any-device).
        self._availability_callbacks: dict[str, list[AvailabilityCallback]] = {}

        # Ready callbacks — fired on every root ready transition (not just first).
        self._ready_callbacks: list[Callable[[], None]] = []

        # Tree-state callbacks — fired whenever ANY device's $state transitions
        # to ready (root or descendant), or a descendant is discovered already
        # in ready. Per Homie 5, init→ready is the consumer's "trust me now"
        # signal; use it as the trigger for setup-time tree-discovery waits and
        # for post-setup descendant reassessment (so late-arriving children
        # get HA devices/entities without a reload).
        self._tree_state_callbacks: list[Callable[[], None]] = []

        # Device-removed callbacks — fired by the SDK when a descendant drops.
        self._device_removed_callbacks: list[DeviceRemovedCallback] = []

    @property
    def controller(self) -> Controller | None:
        """Return the live Controller (or None before async_start / after async_stop)."""
        return self._controller

    @property
    def root_device(self) -> DiscoveredDevice | None:
        """Return the panel-root DiscoveredDevice."""
        if self._controller is None:
            return None
        return self._controller.devices.get(self.serial_number)

    @property
    def description(self) -> dict[str, Any] | None:
        """Return the panel-root $description (parsed JSON)."""
        device = self.root_device
        return device.description if device else None

    @property
    def available(self) -> bool:
        """Whether the panel root is effectively ready (i.e. usable)."""
        return self.is_device_available(self.serial_number)

    def is_device_available(self, device_id: str) -> bool:
        """Whether the given device is effectively available per Homie 5.

        Uses ``Controller.get_effective_state``: when the root is in
        init/disconnected/lost/sleeping, every descendant inherits that state
        and is therefore not-available. Only when the root is ``ready`` does
        the descendant's own state stand.
        """
        if self._controller is None:
            return False
        return bool(
            self._controller.get_effective_state(device_id) == DeviceState.READY.value
        )

    def get_property_value(
        self, device_id: str, capability: str, property_id: str
    ) -> str | None:
        """Read the current value of a property on the named descendant."""
        if self._controller is None:
            return None
        device = self._controller.devices.get(device_id)
        if device is None:
            return None
        value = device.get_property(capability, property_id)
        return value if value is None else str(value)

    def set_property(
        self, device_id: str, capability: str, property_id: str, value: str
    ) -> bool:
        """Send a settable-property write to the named descendant."""
        if self._controller is None:
            return False
        return bool(
            self._controller.set_property(device_id, capability, property_id, value)
        )

    def register_property_callback(
        self,
        device_id: str,
        capability: str,
        property_id: str,
        cb: PropertyCallback,
    ) -> Callable[[], None]:
        """Register a callback for ``(device_id, capability, property_id)`` updates.

        Returns an unregister function.
        """
        key = (device_id, capability, property_id)
        self._property_callbacks.setdefault(key, []).append(cb)

        def unregister() -> None:
            cbs = self._property_callbacks.get(key)
            if cbs and cb in cbs:
                cbs.remove(cb)

        return unregister

    def register_availability_callback(
        self, device_id: str, cb: AvailabilityCallback
    ) -> Callable[[], None]:
        """Register a callback for availability changes on a specific device-id.

        The callback receives the new effective-available state on each
        root-state transition (descendants' effective availability rises and
        falls with the root per Homie 5).
        """
        self._availability_callbacks.setdefault(device_id, []).append(cb)

        def unregister() -> None:
            cbs = self._availability_callbacks.get(device_id)
            if cbs and cb in cbs:
                cbs.remove(cb)

        return unregister

    def register_ready_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for root "ready" transitions."""
        self._ready_callbacks.append(cb)

        def unregister() -> None:
            if cb in self._ready_callbacks:
                self._ready_callbacks.remove(cb)

        return unregister

    def register_tree_state_callback(
        self, cb: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback for any device's init→ready transition.

        Wraps the SDK's ``on_device_state_changed`` (filtered to ready-edge
        transitions) plus ``on_device_discovered`` for devices that arrive
        already in ready. Per Homie 5 this is the consumer's authoritative
        "trust me now" signal — use it to drive setup-time tree-discovery
        waits and post-setup reassessment so late-arriving descendants get
        HA devices without a reload.
        """
        self._tree_state_callbacks.append(cb)

        def unregister() -> None:
            if cb in self._tree_state_callbacks:
                self._tree_state_callbacks.remove(cb)

        return unregister

    def register_device_removed_callback(
        self, cb: DeviceRemovedCallback
    ) -> Callable[[], None]:
        """Register a callback for SDK-side descendant removal (leaves-first)."""
        self._device_removed_callbacks.append(cb)

        def unregister() -> None:
            if cb in self._device_removed_callbacks:
                self._device_removed_callbacks.remove(cb)

        return unregister

    async def async_start(self) -> None:
        """Create and start the Controller in tree-rooted mode."""
        self._controller = Controller(
            mqtt_cfg=self._mqtt_cfg,
            homie_domain=EBUS_HOMIE_DOMAIN,
            auto_start=False,
            root_device_id=self.serial_number,
            qos=MQTT_QOS,
        )

        self._controller.set_on_device_discovered_callback(self._on_device_discovered)
        self._controller.set_on_description_received_callback(self._on_description_received)
        self._controller.set_on_property_changed_callback(self._on_property_changed)
        self._controller.set_on_device_state_changed_callback(self._on_device_state_changed)
        self._controller.set_on_device_removed_callback(self._on_device_removed)

        self._controller.start_discovery()

    async def async_stop(self) -> None:
        """Stop the Controller and clean up callback registrations."""
        if self._controller:
            await self.hass.async_add_executor_job(self._controller.stop)
            self._controller = None
        self._property_callbacks.clear()
        self._availability_callbacks.clear()
        self._ready_callbacks.clear()
        self._tree_state_callbacks.clear()
        self._device_removed_callbacks.clear()

    # ── SDK callbacks (called from paho-mqtt thread) ──────────────────────

    def _on_device_discovered(self, device: DiscoveredDevice) -> None:
        """Handle a new device discovery on the tree (paho-mqtt thread)."""
        _LOGGER.debug(
            "SPAN tree discovered device %s (state=%s)", device.device_id, device.state
        )
        if device.device_id == self.serial_number and device.state == "ready":
            self.hass.loop.call_soon_threadsafe(self.device_ready.set)
            self.hass.loop.call_soon_threadsafe(self._dispatch_ready)
        # A device discovered already in ``ready`` is a ready-edge from the
        # consumer's perspective (None → ready) — fire the tree-state signal.
        if device.state == "ready":
            self.hass.loop.call_soon_threadsafe(self._dispatch_tree_state)

    def _on_description_received(self, device: DiscoveredDevice) -> None:
        """Handle a device's $description message (paho-mqtt thread)."""
        _LOGGER.debug("Description received for %s", device.device_id)
        if device.device_id == self.serial_number:
            self.hass.loop.call_soon_threadsafe(self.description_received.set)

    def _on_property_changed(
        self,
        device_id: str,
        node_id: str,
        property_id: str,
        value: str,
        old_value: str | None,
    ) -> None:
        """Handle a property-value change on any descendant (paho-mqtt thread).

        node_id here is the Homie node — i.e. the capability node-id (``info``,
        ``meter``, ``shed-forecast``…), in our tree-data-model terms.
        """
        key = (device_id, node_id, property_id)
        if not self._property_callbacks.get(key):
            return
        self.hass.loop.call_soon_threadsafe(
            self._dispatch_property_update, key, value
        )

    def _on_device_state_changed(
        self,
        device: DiscoveredDevice,
        old_state: str,
        new_state: str,
    ) -> None:
        """Handle a device's $state transition (paho-mqtt thread).

        For the root device, ``ready`` transitions drive the integration's
        ``device_ready`` event and trigger any registered ready callbacks. For
        any device (root or descendant), an effective-state transition that
        crosses the ready boundary flips the entity availability — the SDK's
        effective-state rule cascades root non-ready into descendants, so a
        root transition can flip every descendant's availability in one go.
        """
        _LOGGER.debug(
            "Device %s state: %s → %s", device.device_id, old_state, new_state
        )

        # Effective availability MAY have changed for every device in the
        # tree (a root transition cascades). Dispatch to all registered
        # callbacks; each receives its own device's current effective state.
        if device.device_id == self.serial_number or self._availability_callbacks:
            self.hass.loop.call_soon_threadsafe(self._dispatch_availability_for_all)

        if device.device_id == self.serial_number and new_state == "ready":
            self.hass.loop.call_soon_threadsafe(self.device_ready.set)
            self.hass.loop.call_soon_threadsafe(self._dispatch_ready)

        # Any device's init→ready edge is the Homie 5 "consume me now" signal;
        # fire the tree-state hook so subscribers (the setup-time discovery
        # waiter and the post-setup descendant-reassessment hook) can react
        # without polling.
        if new_state == "ready" and old_state != "ready":
            self.hass.loop.call_soon_threadsafe(self._dispatch_tree_state)

    def _on_device_removed(self, device: DiscoveredDevice) -> None:
        """Handle a descendant dropping out of the tree (paho-mqtt thread, leaves-first).

        Per SDK-o1h, the SDK fires this when a parent's ``$description.children``
        no longer lists the device after an init→ready transition. Bridge to
        the HA event loop so consumers (``__init__.py``) can retire the
        corresponding HA device + entities.
        """
        _LOGGER.debug("Device %s removed from tree", device.device_id)
        self.hass.loop.call_soon_threadsafe(
            self._dispatch_device_removed, device.device_id
        )

    # ── HA event loop dispatchers ─────────────────────────────────────────

    @callback
    def _dispatch_property_update(
        self, key: tuple[str, str, str], value: str
    ) -> None:
        """Dispatch property update to registered entity callbacks (HA event loop)."""
        for cb in list(self._property_callbacks.get(key, [])):
            try:
                cb(value)
            except Exception:
                _LOGGER.exception("Error in property callback for %s", key)

    @callback
    def _dispatch_availability_for_all(self) -> None:
        """Recompute effective availability for every registered device + notify."""
        for device_id, cbs in list(self._availability_callbacks.items()):
            available = self.is_device_available(device_id)
            for cb in list(cbs):
                try:
                    cb(available)
                except Exception:
                    _LOGGER.exception(
                        "Error in availability callback for %s", device_id
                    )

    @callback
    def _dispatch_ready(self) -> None:
        """Dispatch root-ready notification to registered callbacks (HA event loop)."""
        for cb in list(self._ready_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in ready callback")

    @callback
    def _dispatch_tree_state(self) -> None:
        """Dispatch tree-state-change notification to registered callbacks."""
        for cb in list(self._tree_state_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in tree_state callback")

    @callback
    def _dispatch_device_removed(self, device_id: str) -> None:
        """Dispatch device-removed notification to registered callbacks."""
        for cb in list(self._device_removed_callbacks):
            try:
                cb(device_id)
            except Exception:
                _LOGGER.exception(
                    "Error in device-removed callback for %s", device_id
                )


# Re-export for callers that want the same precedence table the SDK uses.
__all__ = [
    "AvailabilityCallback",
    "DeviceRemovedCallback",
    "HOMIE_EFFECTIVE_STATE_TABLE",
    "PropertyCallback",
    "SpanPanel",
]
