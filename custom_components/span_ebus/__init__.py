"""The SPAN Panel (eBus) integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
import resource
import sys
import tracemalloc
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

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
    DEVICE_TYPE_CIRCUIT,
    DEVICE_TYPE_LUGS,
    DOMAIN,
    PLATFORMS,
    TREE_DISCOVERY_TIMEOUT,
)
from .services import async_setup_services
from .util import (
    DEVICE_TYPE_LABELS,
    descendant_device_info,
    panel_device_info,
)

_LOGGER = logging.getLogger(__name__)

MEMORY_DIAG_INTERVAL = timedelta(minutes=30)

_prev_snapshot: tracemalloc.Snapshot | None = None


def _log_memory_diagnostics(panels: dict[str, dict[str, Any]]) -> None:
    """Log memory diagnostics for all active SPAN panels."""
    global _prev_snapshot  # noqa: PLW0603

    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "linux":
        peak_rss *= 1024
    peak_mb = peak_rss / (1024 * 1024)

    if tracemalloc.is_tracing():
        traced_current, _ = tracemalloc.get_traced_memory()
        traced_mb = traced_current / (1024 * 1024)
    else:
        traced_mb = 0.0

    panel_stats: list[str] = []
    for data in panels.values():
        panel = data.get("panel")
        if panel is None:
            continue
        ctrl = getattr(panel, "controller", None)
        if ctrl is None:
            continue
        device_count = len(ctrl.devices)
        sub_count = len(ctrl.mqttc.sub_callbacks) if ctrl.mqttc else 0
        paho_in = 0
        paho_out = 0
        if ctrl.mqttc and hasattr(ctrl.mqttc, "mqttc"):
            paho = ctrl.mqttc.mqttc
            if hasattr(paho, "_in_messages"):
                paho_in = len(paho._in_messages)
            if hasattr(paho, "_out_messages"):
                paho_out = len(paho._out_messages)
        panel_stats.append(
            f"{panel.serial_number}(devices={device_count},subs={sub_count},"
            f"paho_in={paho_in},paho_out={paho_out})"
        )

    _LOGGER.info(
        "Memory diagnostics: peak_rss=%.1fMB, traced=%.1fMB, panels=[%s]",
        peak_mb,
        traced_mb,
        ", ".join(panel_stats) if panel_stats else "none",
    )

    if tracemalloc.is_tracing():
        try:
            snapshot = tracemalloc.take_snapshot()
            for i, stat in enumerate(snapshot.statistics("filename")[:5], 1):
                _LOGGER.info("tracemalloc top %d: %s", i, stat)
            _prev_snapshot = snapshot
        except Exception:
            _LOGGER.exception("tracemalloc snapshot failed")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SPAN Panel (eBus) from a config entry."""
    # Import here so the config flow can be discovered before ebus-sdk is installed.
    from .node_mappers import entities_from_tree  # noqa: PLC0415
    from .span_panel import SpanPanel  # noqa: PLC0415

    if not hass.services.has_service(DOMAIN, "link_subpanel"):
        await async_setup_services(hass)

    serial_number = entry.data[CONF_SERIAL_NUMBER]

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
    await panel.async_start()

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

    try:
        await asyncio.wait_for(
            panel.device_ready.wait(), timeout=DEVICE_READY_TIMEOUT
        )
    except TimeoutError:
        _LOGGER.warning(
            "SPAN Panel %s: timed out waiting for root ready state; "
            "proceeding with available descendants",
            serial_number,
        )

    # Tree-rooted SDK mode subscribes to descendants only after the parent's
    # init→ready edge, so wait for the transitive closure of children to land
    # before invoking the mapper layer. Without this we'd snapshot
    # ``controller.devices`` while only the panel root is present and silently
    # drop every descendant (lugs / BESS / MID / PV / EVSE / every circuit).
    tree_complete = await _wait_for_tree_discovery(
        panel, serial_number, TREE_DISCOVERY_TIMEOUT
    )
    if not tree_complete:
        _LOGGER.warning(
            "SPAN Panel %s: descendant discovery did not settle within %ds; "
            "proceeding with partial tree",
            serial_number,
            TREE_DISCOVERY_TIMEOUT,
        )

    controller = panel.controller
    assert controller is not None  # async_start was awaited successfully

    circuit_device_ids = [
        device_id
        for device_id, dev in controller.devices.items()
        if (dev.description or {}).get("type") == f"energy.ebus.device.{DEVICE_TYPE_CIRCUIT}"
    ]
    if circuit_device_ids:
        names_ok = await _wait_for_circuit_names(
            panel, circuit_device_ids, CIRCUIT_NAMES_TIMEOUT
        )
        if not names_ok:
            available = sum(
                1
                for cid in circuit_device_ids
                if panel.get_property_value(cid, "info", "name") is not None
            )
            _LOGGER.warning(
                "SPAN Panel %s: timed out waiting for circuit names "
                "(%d/%d available), using fallback names for remainder",
                serial_number,
                available,
                len(circuit_device_ids),
            )

    # Devices are passed by reference to the mapper layer; node_mappers' walker
    # reads description + properties from each entry. Pass a snapshot so we
    # have a stable view during this setup pass.
    tree_snapshot = _controller_devices_to_snapshot(controller.devices)
    entity_specs = entities_from_tree(tree_snapshot)
    _LOGGER.debug(
        "SPAN Panel %s: %d entity specs from tree walk (%d devices)",
        serial_number,
        len(entity_specs),
        len(controller.devices),
    )

    # Stamp per-spec device-presentation fields that depend on runtime state
    # (e.g. circuit info/name → HA device name) before passing to platforms.
    _stamp_device_presentation(panel, controller, entity_specs)

    device_registry = dr.async_get(hass)
    firmware = panel.get_property_value(serial_number, "info", "firmware-version") or (
        panel.get_property_value(serial_number, "info", "software-version") or ""
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **panel_device_info(serial_number, firmware),
    )

    _register_descendants(
        device_registry, entry.entry_id, panel, controller, entity_specs
    )

    unregister_callbacks: list[Callable[[], None]] = []

    # Reactively update circuit device names when info/name arrives via MQTT.
    for circuit_device_id in circuit_device_ids:
        _cid = circuit_device_id

        def _on_name_update(value: str, cid: str = _cid) -> None:
            _LOGGER.debug(
                "Circuit %s name updated to '%s'; refreshing device registry", cid, value
            )
            dev_reg = dr.async_get(hass)
            dev_reg.async_get_or_create(
                config_entry_id=entry.entry_id,
                **descendant_device_info(
                    panel_serial=panel.serial_number,
                    device_id=cid,
                    device_type=DEVICE_TYPE_CIRCUIT,
                    device_name=value,
                ),
            )

        unregister_callbacks.append(
            panel.register_property_callback(
                circuit_device_id, "info", "name", _on_name_update
            )
        )

    def _on_ready() -> None:
        _LOGGER.info(
            "SPAN Panel %s ready transition — refreshing descendant device-registry names",
            serial_number,
        )
        if panel.controller is None:
            return
        refreshed_snapshot = _controller_devices_to_snapshot(panel.controller.devices)
        refreshed_specs = entities_from_tree(refreshed_snapshot)
        _stamp_device_presentation(panel, panel.controller, refreshed_specs)
        _register_descendants(
            dr.async_get(hass), entry.entry_id, panel, panel.controller, refreshed_specs
        )

    unregister_callbacks.append(panel.register_ready_callback(_on_ready))

    def _on_device_removed(device_id: str) -> None:
        _LOGGER.info(
            "SPAN Panel %s: descendant %s dropped; removing from HA device registry",
            serial_number,
            device_id,
        )
        dev_reg = dr.async_get(hass)
        ha_device = dev_reg.async_get_device(
            identifiers={(DOMAIN, f"{panel.serial_number}_{device_id}")}
        )
        if ha_device is not None:
            dev_reg.async_remove_device(ha_device.id)

    unregister_callbacks.append(panel.register_device_removed_callback(_on_device_removed))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "panel": panel,
        "entity_specs": entity_specs,
        "unregister_callbacks": unregister_callbacks,
    }

    if "_memory_diag_unsub" not in hass.data[DOMAIN]:
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            _LOGGER.info("tracemalloc started for memory leak diagnostics")

        def _diag_callback(_now: Any) -> None:
            panels = {
                eid: data
                for eid, data in hass.data.get(DOMAIN, {}).items()
                if isinstance(data, dict) and "panel" in data
            }
            _log_memory_diagnostics(panels)

        hass.data[DOMAIN]["_memory_diag_unsub"] = async_track_time_interval(
            hass, _diag_callback, MEMORY_DIAG_INTERVAL
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _wait_for_tree_discovery(
    panel: Any,
    root_device_id: str,
    timeout: float,
) -> bool:
    """Wait for the SDK to discover the full tree under the root device.

    In tree-rooted mode (SDK 0.3.0+ ``Controller(root_device_id=...)``), the
    Controller only starts subscribing to a parent's children after that
    parent's own ``$state=ready`` arrives — descendants populate
    progressively. Repeatedly walk the transitive closure of
    ``$description.children`` starting from the root, waiting until every
    expected device-id has appeared in ``controller.devices`` with a
    description. Each iteration may grow the expected set as deeper children
    (e.g. MID under BESS) come into view.

    Returns True when the tree has settled (no new children added in the
    last iteration AND every expected device has a description), False on
    timeout.
    """
    controller = panel.controller
    if controller is None:
        return False

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_logged_count = 0

    while True:
        # Walk the closure of root → children → grandchildren given current state.
        expected: set[str] = {root_device_id}
        added = True
        while added:
            added = False
            for device_id in list(expected):
                dev = controller.devices.get(device_id)
                if dev is None or dev.description is None:
                    continue
                for child_id in dev.description.get("children", []) or []:
                    if child_id not in expected:
                        expected.add(child_id)
                        added = True

        # Workaround for an SDK race on initial connect: retained $state=ready
        # often arrives microseconds before retained $description, and the SDK's
        # reconcile fires on the state-edge with an empty description's
        # children list — never re-firing when the description lands. Force a
        # reconcile from our side for every device whose description we've
        # already observed; the SDK's _reconcile_descendants is idempotent (no
        # changes if the children are already subscribed) so it's safe to call
        # repeatedly. Filed as a tracked SDK bug; remove this hook when fixed.
        for device_id in list(expected):
            dev = controller.devices.get(device_id)
            if dev is None or dev.description is None:
                continue
            if dev.description.get("children"):
                try:
                    controller._reconcile_descendants(device_id)
                except Exception:  # pragma: no cover — defensive against SDK internals
                    _LOGGER.exception(
                        "force-reconcile failed for %s; SDK may have changed shape",
                        device_id,
                    )

        missing = {
            d for d in expected
            if d not in controller.devices
            or controller.devices[d].description is None
        }

        if not missing:
            _LOGGER.debug(
                "SPAN Panel %s: tree discovery settled (%d devices)",
                root_device_id,
                len(expected),
            )
            return True

        if len(controller.devices) > last_logged_count:
            _LOGGER.debug(
                "SPAN Panel %s: tree discovery in progress (%d/%d devices, "
                "%d missing)",
                root_device_id,
                len(expected) - len(missing),
                len(expected),
                len(missing),
            )
            last_logged_count = len(controller.devices)

        if loop.time() >= deadline:
            _LOGGER.warning(
                "SPAN Panel %s: tree discovery timeout — %d/%d expected devices "
                "missing descriptions; first few: %s",
                root_device_id,
                len(missing),
                len(expected),
                ", ".join(sorted(missing)[:5]),
            )
            return False

        await asyncio.sleep(0.5)


async def _wait_for_circuit_names(
    panel: Any,
    circuit_device_ids: list[str],
    timeout: float,
) -> bool:
    """Wait for every circuit's ``info/name`` property to arrive via MQTT.

    Circuit user-labels are retained MQTT topics — they normally arrive
    shortly after ``$state=ready``, but the integration freezes entity_id at
    creation time so it's worth a brief wait.
    """
    missing = [
        cid for cid in circuit_device_ids
        if panel.get_property_value(cid, "info", "name") is None
    ]
    if not missing:
        return True

    events: dict[str, asyncio.Event] = {cid: asyncio.Event() for cid in missing}
    unregs: list[Callable[[], None]] = []

    for cid in missing:
        _cid = cid

        def _on_name(value: str, c: str = _cid) -> None:
            events[c].set()

        unregs.append(panel.register_property_callback(cid, "info", "name", _on_name))

    # Re-check after registration in case values arrived between the initial
    # poll and the callback hookup.
    for cid in missing:
        if panel.get_property_value(cid, "info", "name") is not None:
            events[cid].set()

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


def _controller_devices_to_snapshot(
    devices: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Adapt the live Controller.devices dict to the snapshot shape the walker expects.

    ``DiscoveredDevice`` carries the same fields the fixture snapshots do —
    description, properties, parent_id, children_ids, is_root, root_id —
    just as attributes rather than dict keys. Materialise a dict-of-dicts so
    ``entities_from_tree`` doesn't need to know the runtime type.
    """
    out: dict[str, dict[str, Any]] = {}
    for device_id, dev in devices.items():
        out[device_id] = {
            "description": dev.description or {},
            "properties": dict(dev.properties or {}),
            "parent_id": getattr(dev, "parent_id", None),
            "children_ids": list(getattr(dev, "children_ids", []) or []),
            "is_root": getattr(dev, "is_root", device_id == device_id),
            "root_id": getattr(dev, "root_id", device_id),
        }
    return out


def _stamp_device_presentation(
    panel: Any,
    controller: Any,
    entity_specs: list,
) -> None:
    """Fill in spec.device_type / spec.device_name / spec.via_device_id.

    Mappers can't reach across devices, so the per-entity HA presentation
    fields are stamped here at the integration layer. Circuit names come from
    the circuit's own ``info/name``; other descendants get a generated label
    derived from the device class.
    """
    from .node_mappers import device_type_short  # noqa: PLC0415

    for spec in entity_specs:
        dev = controller.devices.get(spec.device_id)
        if dev is None:
            continue
        dtype = device_type_short((dev.description or {}).get("type", "")) or ""
        spec.device_type = dtype
        spec.via_device_id = getattr(dev, "parent_id", None) or panel.serial_number

        if spec.device_id == panel.serial_number:
            spec.device_name = ""  # panel device handled separately
            continue

        if dtype == DEVICE_TYPE_CIRCUIT:
            label = panel.get_property_value(spec.device_id, "info", "name")
            spec.device_name = label or f"Circuit {spec.device_id[:6]}"
        else:
            type_label = DEVICE_TYPE_LABELS.get(dtype, dtype.title())
            short_serial = panel.serial_number.rsplit("-", 1)[-1]
            # Lugs come in matched up/down pairs; the device-class label alone
            # ("Lugs") collides between the two. Read info/direction off the
            # device and prefix accordingly so HA's entity_id auto-derivation
            # doesn't have to suffix one with _2.
            if dtype == DEVICE_TYPE_LUGS:
                direction = panel.get_property_value(
                    spec.device_id, "info", "direction"
                ) or ""
                prefix = direction.strip().capitalize()
                if prefix in {"Upstream", "Downstream"}:
                    type_label = f"{prefix} {type_label}"
            spec.device_name = f"{short_serial} {type_label}"


def _register_descendants(
    device_registry: dr.DeviceRegistry,
    config_entry_id: str,
    panel: Any,
    controller: Any,
    entity_specs: list,
) -> None:
    """Register or update descendant HA devices in the device registry.

    ``async_get_or_create`` only sets the device name on first creation; when
    the integration's default name changes between releases (e.g. the
    upstream / downstream lugs disambiguation), existing devices keep the old
    name. Explicitly call ``async_update_device`` whenever the desired name
    differs from the current one, but skip when the user has set
    ``name_by_user`` so we don't trample their custom labels.
    """
    seen: set[str] = set()
    for spec in entity_specs:
        if spec.device_id == panel.serial_number or spec.device_id in seen:
            continue
        seen.add(spec.device_id)
        info = descendant_device_info(
            panel_serial=panel.serial_number,
            device_id=spec.device_id,
            device_type=spec.device_type,
            device_name=spec.device_name,
            parent_device_id=spec.via_device_id,
        )
        device = device_registry.async_get_or_create(
            config_entry_id=config_entry_id, **info
        )
        desired_name = info.get("name")
        if (
            desired_name
            and device.name != desired_name
            and not device.name_by_user
        ):
            device_registry.async_update_device(device.id, name=desired_name)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a SPAN Panel (eBus) config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            for unreg in data.get("unregister_callbacks", []):
                unreg()
            await data["panel"].async_stop()

        remaining = {
            k for k in hass.data.get(DOMAIN, {})
            if k != "_memory_diag_unsub"
        }
        if not remaining:
            unsub = hass.data[DOMAIN].pop("_memory_diag_unsub", None)
            if unsub:
                unsub()

    return unload_ok
