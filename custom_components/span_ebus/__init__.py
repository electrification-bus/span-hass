"""The SPAN Panel (eBus) integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import resource
import sys

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from datetime import timedelta

from .const import (
    CIRCUIT_NAMES_TIMEOUT,
    CONF_CA_CERT_PEM,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_SERIAL_NUMBER,
    DESCRIPTION_TIMEOUT,
    DEVICE_READY_TIMEOUT,
    DOMAIN,
    PLATFORMS,
)

MEMORY_DIAG_INTERVAL = timedelta(minutes=30)
from .services import async_setup_services
from .util import _SUB_DEVICE_TYPES, panel_device_info, subdevice_info

_LOGGER = logging.getLogger(__name__)


def _log_memory_diagnostics(panels: dict) -> None:
    """Log memory diagnostics for all active SPAN panels."""
    # Peak RSS in bytes (macOS returns bytes, Linux returns KB)
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "linux":
        peak_rss *= 1024  # Linux ru_maxrss is in KB
    peak_mb = peak_rss / (1024 * 1024)

    panel_stats = []
    for entry_id, data in panels.items():
        panel = data.get("panel")
        if panel and panel._controller:
            ctrl = panel._controller
            device_count = len(ctrl.devices)
            sub_count = len(ctrl.mqttc.sub_callbacks) if ctrl.mqttc else 0
            panel_stats.append(
                f"{panel.serial_number}(devices={device_count},subs={sub_count})"
            )

    _LOGGER.info(
        "Memory diagnostics: peak_rss=%.1fMB, panels=[%s]",
        peak_mb,
        ", ".join(panel_stats) if panel_stats else "none",
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SPAN Panel (eBus) from a config entry."""
    # Import here so the config flow can be discovered before ebus-sdk is installed.
    # HA installs manifest requirements between config flow and setup_entry.
    from .node_mappers import entities_from_description  # noqa: PLC0415
    from .span_panel import SpanPanel  # noqa: PLC0415

    # Register services once (first entry only)
    if not hass.services.has_service(DOMAIN, "link_subpanel"):
        await async_setup_services(hass)

    serial_number = entry.data[CONF_SERIAL_NUMBER]

    # Build MQTT config dict for the SDK
    mqtt_cfg = {
        "host": entry.data[CONF_EBUS_BROKER_HOST],
        "port": entry.data[CONF_EBUS_BROKER_PORT],
        "use_tls": True,
        "tls_ca_data": entry.data.get(CONF_CA_CERT_PEM, ""),
        "tls_insecure": not entry.data.get(CONF_CA_CERT_PEM),
        "authentication": {
            "type": "USER_PASS",
            "username": entry.data[CONF_EBUS_BROKER_USERNAME],
            "password": entry.data[CONF_EBUS_BROKER_PASSWORD],
        },
    }

    panel = SpanPanel(hass, serial_number, mqtt_cfg)

    # Start the MQTT Controller
    await panel.async_start()

    # Wait for the $description to arrive so we know what entities to create
    try:
        await asyncio.wait_for(
            panel.description_received.wait(), timeout=DESCRIPTION_TIMEOUT
        )
    except TimeoutError:
        await panel.async_stop()
        raise ConfigEntryNotReady(
            f"Timed out waiting for description from SPAN Panel {serial_number}"
        )

    description = panel.description
    if not description:
        await panel.async_stop()
        raise ConfigEntryNotReady(
            f"No description received from SPAN Panel {serial_number}"
        )

    # Log description node summary for diagnostics
    nodes = description.get("nodes", {})
    node_types = {}
    for node_desc in nodes.values():
        ntype = node_desc.get("type", "unknown")
        node_types[ntype] = node_types.get(ntype, 0) + 1
    _LOGGER.debug(
        "SPAN Panel %s: description has %d nodes: %s",
        serial_number,
        len(nodes),
        ", ".join(f"{v}x {k}" for k, v in sorted(node_types.items())),
    )

    # Wait for the device to reach "ready" — $state=ready arrived via MQTT.
    # Note: this does NOT guarantee all retained property values have been
    # received; see _wait_for_circuit_names below.
    try:
        await asyncio.wait_for(
            panel.device_ready.wait(), timeout=DEVICE_READY_TIMEOUT
        )
    except TimeoutError:
        _LOGGER.warning(
            "SPAN Panel %s: timed out waiting for ready state, "
            "proceeding with available data",
            serial_number,
        )

    # Wait for circuit name properties to arrive via MQTT.
    # The SDK fires device_ready when $state=ready arrives, but retained
    # property values (including circuit names) may still be in flight.
    # Entity IDs are frozen at creation time, so we must have the real
    # circuit names before proceeding.
    circuit_node_ids = [
        node_id
        for node_id, node_desc in nodes.items()
        if node_desc.get("type") == "energy.ebus.device.circuit"
    ]
    if circuit_node_ids:
        names_ok = await _wait_for_circuit_names(
            panel, circuit_node_ids, CIRCUIT_NAMES_TIMEOUT
        )
        if names_ok:
            _LOGGER.debug(
                "SPAN Panel %s: all %d circuit names available",
                serial_number,
                len(circuit_node_ids),
            )
        else:
            available = sum(
                1
                for nid in circuit_node_ids
                if panel.get_property_value(nid, "name") is not None
            )
            _LOGGER.warning(
                "SPAN Panel %s: timed out waiting for circuit names "
                "(%d/%d available), using fallback names for remainder",
                serial_number,
                available,
                len(circuit_node_ids),
            )

    # Map the $description to entity specs (circuit names now available)
    entity_specs = entities_from_description(description, panel=panel)
    _LOGGER.debug(
        "SPAN Panel %s: %d entities from description", serial_number, len(entity_specs)
    )

    # Register the panel device in the device registry
    device_registry = dr.async_get(hass)
    firmware = panel.get_property_value("core", "software-version") or ""
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **panel_device_info(serial_number, firmware),
    )

    # Register sub-devices (circuits, BESS, PV, EVSE) as children of the panel
    _register_subdevices(device_registry, entry.entry_id, serial_number, entity_specs)

    # Reactively update circuit device names when the "name" property arrives
    # via MQTT (retained values may arrive after entity creation).
    # Store unregister functions so we can clean up in async_unload_entry.
    unregister_callbacks: list[Callable[[], None]] = []

    circuit_node_ids = {
        spec.node_id
        for spec in entity_specs
        if spec.node_type == "energy.ebus.device.circuit"
    }
    for node_id in circuit_node_ids:
        _node = node_id  # capture for closure

        def _on_name_update(value: str, nid: str = _node) -> None:
            _LOGGER.debug(
                "Circuit %s name updated to '%s', updating device registry", nid, value
            )
            dev_reg = dr.async_get(hass)
            dev_reg.async_get_or_create(
                config_entry_id=entry.entry_id,
                **subdevice_info(
                    serial_number, nid, "energy.ebus.device.circuit", value
                ),
            )

        unregister_callbacks.append(
            panel.register_property_callback(node_id, "name", _on_name_update)
        )

    # Also refresh all device names on every "ready" transition
    # (covers reconnections, firmware updates, circuit renames, etc.)
    def _on_ready() -> None:
        _LOGGER.info("SPAN Panel %s became ready, refreshing device names", serial_number)
        refreshed = entities_from_description(
            panel.description or {}, panel=panel
        )
        _register_subdevices(
            dr.async_get(hass), entry.entry_id, serial_number, refreshed
        )

    unregister_callbacks.append(panel.register_ready_callback(_on_ready))

    # Store panel, specs, and cleanup functions for platform setup / unload
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "panel": panel,
        "entity_specs": entity_specs,
        "unregister_callbacks": unregister_callbacks,
    }

    # Start periodic memory diagnostics (once, on first panel setup)
    if "_memory_diag_unsub" not in hass.data[DOMAIN]:

        def _diag_callback(_now) -> None:
            panels = {
                eid: data
                for eid, data in hass.data.get(DOMAIN, {}).items()
                if isinstance(data, dict) and "panel" in data
            }
            _log_memory_diagnostics(panels)

        hass.data[DOMAIN]["_memory_diag_unsub"] = async_track_time_interval(
            hass, _diag_callback, MEMORY_DIAG_INTERVAL
        )

    # Forward setup to each platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _wait_for_circuit_names(
    panel: "SpanPanel",
    circuit_node_ids: list[str],
    timeout: float,
) -> bool:
    """Wait for circuit name properties to arrive via MQTT.

    Per the Homie Convention, $description declares the full set of nodes
    and properties that the device has published.  $state=ready means the
    device has sent everything, but MQTT does not guarantee delivery order.
    We know these "name" properties are coming — wait for them.

    Registers a property callback for each missing circuit name and awaits
    the corresponding asyncio.Event.  Returns True if all names arrived
    within the timeout, False otherwise.
    """
    # Check which names are already present
    missing = [
        nid
        for nid in circuit_node_ids
        if panel.get_property_value(nid, "name") is None
    ]
    if not missing:
        return True

    # Create an event per missing name and register callbacks
    events: dict[str, asyncio.Event] = {nid: asyncio.Event() for nid in missing}
    unregs: list[Callable[[], None]] = []

    for nid in missing:
        _nid = nid  # capture for closure

        def _on_name(value: str, n: str = _nid) -> None:
            events[n].set()

        unregs.append(panel.register_property_callback(nid, "name", _on_name))

    # Re-check after registration in case values arrived between the
    # initial check and callback registration (paho-mqtt thread may have
    # updated device.properties in the meantime).
    for nid in missing:
        if panel.get_property_value(nid, "name") is not None:
            events[nid].set()

    try:
        await asyncio.wait_for(
            asyncio.gather(*(ev.wait() for ev in events.values())),
            timeout=timeout,
        )
        return True
    except TimeoutError:
        return False
    finally:
        for unreg in unregs:
            unreg()


def _register_subdevices(
    device_registry: dr.DeviceRegistry,
    config_entry_id: str,
    serial_number: str,
    entity_specs: list,
) -> None:
    """Register or update sub-devices (circuits, BESS, PV, EVSE) in the device registry."""
    from .node_mappers import EntitySpec  # noqa: PLC0415

    seen: set[str] = set()
    spec: EntitySpec
    for spec in entity_specs:
        if spec.node_type in _SUB_DEVICE_TYPES and spec.node_id not in seen:
            seen.add(spec.node_id)
            device_registry.async_get_or_create(
                config_entry_id=config_entry_id,
                **subdevice_info(
                    serial_number, spec.node_id, spec.node_type, spec.device_name
                ),
            )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a SPAN Panel (eBus) config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            # Unregister name-update and ready callbacks before stopping
            for unreg in data.get("unregister_callbacks", []):
                unreg()
            panel: SpanPanel = data["panel"]
            await panel.async_stop()

        # If no more panels, cancel the memory diagnostics timer
        remaining = {
            k for k in hass.data.get(DOMAIN, {})
            if k != "_memory_diag_unsub"
        }
        if not remaining:
            unsub = hass.data[DOMAIN].pop("_memory_diag_unsub", None)
            if unsub:
                unsub()

    return unload_ok
