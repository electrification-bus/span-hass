"""The SPAN Panel (eBus) integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
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
    CONF_HOST,
    CONF_SERIAL_NUMBER,
    DESCRIPTION_TIMEOUT,
    DEVICE_READY_TIMEOUT,
    DEVICE_TYPE_CIRCUIT,
    DEVICE_TYPE_LUGS,
    DOMAIN,
    PLATFORMS,
    TREE_DISCOVERY_TIMEOUT,
)
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


def _build_mqtt_cfg(data: Mapping[str, Any]) -> dict[str, Any]:
    """Build the ebus-sdk MQTT config from a config entry's stored data.

    Uses the zeroconf-discovered IP (``CONF_HOST``) as the broker host, not the
    panel's ``.local`` name. On Home Assistant OS the container resolver returns
    only an unroutable IPv6 link-local/ULA for a dual-stack ``.local`` name and
    drops the IPv4 A record, so paho (re-resolving the name at connect time)
    never connects. ``CONF_HOST`` is the routable IPv4 already proven reachable
    for the REST API, a literal IP never hits that resolver, and the IP is in the
    panel's certificate SAN so TLS still verifies. Falls back to the ``.local``
    broker host if no discovered IP is stored.
    """
    return {
        "host": data.get(CONF_HOST) or data[CONF_EBUS_BROKER_HOST],
        "port": data[CONF_EBUS_BROKER_PORT],
        "use_tls": True,
        "tls_ca_data": data.get(CONF_CA_CERT_PEM, ""),
        "tls_insecure": not data.get(CONF_CA_CERT_PEM),
        "authentication": {
            "type": "USER_PASS",
            "username": data[CONF_EBUS_BROKER_USERNAME],
            "password": data[CONF_EBUS_BROKER_PASSWORD],
        },
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SPAN Panel (eBus) from a config entry."""
    # Import here so the config flow can be discovered before ebus-sdk is installed.
    from .node_mappers import entities_from_tree  # noqa: PLC0415
    from .span_panel import SpanPanel  # noqa: PLC0415

    serial_number = entry.data[CONF_SERIAL_NUMBER]

    mqtt_cfg = _build_mqtt_cfg(entry.data)

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
    _register_panel_and_descendants(
        device_registry, entry.entry_id, panel, controller, entity_specs
    )

    unregister_callbacks: list[Callable[[], None]] = []

    # Reactively update circuit device names when info/name arrives via MQTT.
    # Property-update doesn't trigger an init→ready edge (no structural
    # change), so the tree-state hook wouldn't fire — we wire a per-circuit
    # property callback instead. ``async_get_or_create`` only sets ``name``
    # on first creation, so the propagation has to go through
    # ``async_update_device`` directly.
    for circuit_device_id in circuit_device_ids:
        _cid = circuit_device_id

        def _on_name_update(value: str, cid: str = _cid) -> None:
            _LOGGER.debug(
                "Circuit %s name updated to '%s'; refreshing device registry", cid, value
            )
            dev_reg = dr.async_get(hass)
            existing = dev_reg.async_get_device(
                identifiers={(DOMAIN, f"{panel.serial_number}_{cid}")}
            )
            if existing is None:
                # Brand-new circuit (e.g. user added a breaker mid-session).
                # async_get_or_create will set name on first creation.
                dev_reg.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    **descendant_device_info(
                        panel_serial=panel.serial_number,
                        device_id=cid,
                        device_type=DEVICE_TYPE_CIRCUIT,
                        device_name=value,
                    ),
                )
                return
            if existing.name != value and not existing.name_by_user:
                dev_reg.async_update_device(existing.id, name=value)

        unregister_callbacks.append(
            panel.register_property_callback(
                circuit_device_id, "info", "name", _on_name_update
            )
        )

    def _on_tree_state() -> None:
        """Re-walk the tree on any descendant's init→ready edge.

        Per Homie 5, init→ready is the consumer's "trust me now" signal. Use
        every descendant's ready edge (not just the root's) to catch
        late-arriving children that weren't present when initial setup
        committed, and to pick up any upstream-topology change the publisher
        announces (e.g. the lugs-up/connection/fed-by-device-id triplet that
        drives the panel's via_device link). All registration is idempotent
        via ``async_get_or_create``, so reruns on already-known devices are
        essentially free.
        """
        if panel.controller is None:
            return
        refreshed_snapshot = _controller_devices_to_snapshot(panel.controller.devices)
        refreshed_specs = entities_from_tree(refreshed_snapshot)
        _stamp_device_presentation(panel, panel.controller, refreshed_specs)
        _register_panel_and_descendants(
            dr.async_get(hass), entry.entry_id, panel, panel.controller, refreshed_specs
        )

    unregister_callbacks.append(panel.register_tree_state_callback(_on_tree_state))

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

    Event-driven on the Homie 5 init→ready signal: ``register_tree_state_callback``
    fires whenever any device transitions to ``ready`` (or is first observed
    already in ``ready``), which is the spec's authoritative "description and
    state are now current" trigger. Each ready edge means the closure may have
    grown (a previously-unseen child published its $description, listing
    grandchildren) or may have settled (every expected device has ready+desc).

    The closure walk runs once per ready edge — no polling, no fixed sleep.
    The timeout is a safety backstop, not a per-iteration delay.

    Returns True when the tree has settled, False on timeout.
    """
    controller = panel.controller
    if controller is None:
        return False

    edge_event = asyncio.Event()
    unregister = panel.register_tree_state_callback(edge_event.set)

    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        last_logged_count = 0

        while True:
            # Clear BEFORE checking so a ready-edge that fires after the check
            # but before the await still wakes us up.
            edge_event.clear()

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

            remaining = deadline - loop.time()
            if remaining <= 0:
                _LOGGER.warning(
                    "SPAN Panel %s: tree discovery timeout — %d/%d expected "
                    "devices missing descriptions; first few: %s",
                    root_device_id,
                    len(missing),
                    len(expected),
                    ", ".join(sorted(missing)[:5]),
                )
                return False

            # If the wait times out, the next loop iteration's deadline check
            # logs and returns False — no extra handling needed here.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(edge_event.wait(), timeout=remaining)
    finally:
        unregister()


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


def _flatten_properties(props: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten the SDK's nested ``{node: {prop: value}}`` to ``{"node/prop": value}``.

    ``DiscoveredDevice.properties`` is nested by node, but the tree snapshot and
    the node_mappers that read ``device_data["properties"]`` for sibling-gate
    lookups (e.g. ``"connection/feeds-device-type"``, ``"info/direction"``)
    expect flat ``"capability/property"`` keys, matching the tree fixture JSONs.
    Without this flattening those lookups silently miss at runtime and fall back
    to defaults (settable gates, lug-direction resolution).
    """
    flat: dict[str, Any] = {}
    for node_id, node_props in (props or {}).items():
        if isinstance(node_props, dict):
            for prop_id, value in node_props.items():
                flat[f"{node_id}/{prop_id}"] = value
        else:
            # Defensive: an already-flat "node/prop" -> scalar entry.
            flat[node_id] = node_props
    return flat


def _controller_devices_to_snapshot(
    devices: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Adapt the live Controller.devices dict to the snapshot shape the walker expects.

    ``DiscoveredDevice`` carries the same fields the fixture snapshots do
    (description, properties, parent_id, children_ids, is_root, root_id) just as
    attributes rather than dict keys. Materialise a dict-of-dicts so
    ``entities_from_tree`` doesn't need to know the runtime type. Properties are
    flattened from the SDK's nested ``{node: {prop: value}}`` to the flat
    ``{"node/prop": value}`` shape the mappers and fixtures use.
    """
    out: dict[str, dict[str, Any]] = {}
    for device_id, dev in devices.items():
        out[device_id] = {
            "description": dev.description or {},
            "properties": _flatten_properties(dev.properties),
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


def _resolve_upstream_panel(panel: Any) -> str | None:
    """Read the publisher's upstream-topology pointer for this panel.

    G3P-24911 publishes the cascade topology via the lugs-up ``connection``
    capability: ``fed-by-device-id`` carries the serial of whatever feeds this
    panel, and ``fed-by-device-type`` distinguishes a sister panel
    (``energy.ebus.device.distribution-enclosure`` — a downstream panel in a
    cascade) from a BESS feeding from above (``energy.ebus.device.bess``) or
    a utility feed (null triplet).

    For the cascade case, return the upstream panel's serial so the caller
    can set ``via_device`` on this panel's HA device — making the daisy
    chain visible in Settings → Devices with no user action. For the BESS
    case, return None: the BESS is already a child of this panel via the
    Homie parent/child tree, so the via-device link runs BESS→panel, not
    the other way around. For utility feed, also None — top of cascade.
    """
    lugs_up_id = f"{panel.serial_number}-lugs-up"
    fed_by_id = panel.get_property_value(lugs_up_id, "connection", "fed-by-device-id")
    fed_by_type = panel.get_property_value(lugs_up_id, "connection", "fed-by-device-type")
    if not fed_by_id:
        return None
    if fed_by_type == "energy.ebus.device.distribution-enclosure":
        return str(fed_by_id)
    return None


def _register_panel_and_descendants(
    device_registry: dr.DeviceRegistry,
    config_entry_id: str,
    panel: Any,
    controller: Any,
    entity_specs: list,
) -> None:
    """Register or update the panel root device plus every descendant.

    The panel root carries ``via_device`` only when the publisher's lugs-up
    connection points at a sister panel (cascade case) — handled by
    ``_resolve_upstream_panel``. ``async_get_or_create`` only sets the device
    name (and via_device) on first creation; explicit ``async_update_device``
    keeps both in sync when the integration's default changes between releases
    or when the publisher republishes the upstream link, while preserving any
    user-set ``name_by_user``.
    """
    serial_number = panel.serial_number
    firmware = panel.get_property_value(serial_number, "info", "firmware-version") or (
        panel.get_property_value(serial_number, "info", "software-version") or ""
    )
    upstream = _resolve_upstream_panel(panel)
    panel_info = panel_device_info(
        serial_number, firmware, upstream_panel_serial=upstream
    )
    panel_device = device_registry.async_get_or_create(
        config_entry_id=config_entry_id, **panel_info
    )
    _refresh_name_and_via_device(
        device_registry, panel_device, panel_info, upstream_serial=upstream
    )

    seen: set[str] = set()
    for spec in entity_specs:
        if spec.device_id == serial_number or spec.device_id in seen:
            continue
        seen.add(spec.device_id)
        info = descendant_device_info(
            panel_serial=serial_number,
            device_id=spec.device_id,
            device_type=spec.device_type,
            device_name=spec.device_name,
            parent_device_id=spec.via_device_id,
        )
        device = device_registry.async_get_or_create(
            config_entry_id=config_entry_id, **info
        )
        _refresh_name_and_via_device(device_registry, device, info)


def _refresh_name_and_via_device(
    device_registry: dr.DeviceRegistry,
    device: dr.DeviceEntry,
    info: Any,
    upstream_serial: str | None = None,
) -> None:
    """Update a device's name and via_device link when our defaults change.

    Preserves user-customized names (``name_by_user`` set). The via_device
    update only applies when ``upstream_serial`` is supplied (panel-root
    only); descendant via_device is set at creation time and rarely changes.
    """
    updates: dict[str, Any] = {}
    desired_name = info.get("name")
    if desired_name and device.name != desired_name and not device.name_by_user:
        updates["name"] = desired_name
    if upstream_serial is not None:
        upstream_device = device_registry.async_get_device(
            identifiers={(DOMAIN, upstream_serial)}
        )
        upstream_device_id = upstream_device.id if upstream_device else None
        if upstream_device_id != device.via_device_id:
            updates["via_device_id"] = upstream_device_id
    if updates:
        device_registry.async_update_device(device.id, **updates)


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
