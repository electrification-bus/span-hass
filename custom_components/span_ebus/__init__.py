"""The SPAN Panel (eBus) integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later

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
    DEVICE_REMOVAL_GRACE,
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

    retirement = _DeferredDeviceRetirement(hass, panel, entry.entry_id)

    # Circuits whose entities are held back until ``info/name`` lands, mapped to
    # the cleanups for their name callback and timeout backstop. Home Assistant
    # freezes ``entity_id`` at creation, so a circuit whose entities are built
    # while its name is still in flight is stuck on the ``Circuit <hex>``
    # fallback permanently. Setup avoids that with ``_wait_for_circuit_names``;
    # these two structures are the post-setup equivalent.
    named_deferrals: dict[str, list[Callable[[], None]]] = {}
    named_released: set[str] = set()

    @callback
    def _release_named(device_id: str, *_args: Any) -> None:
        """Stop holding a circuit's entities and re-run the walk."""
        for cleanup in named_deferrals.pop(device_id, []):
            cleanup()
        named_released.add(device_id)
        _on_tree_state()

    @callback
    def _defer_until_named(device_id: str) -> None:
        """Hold a newly seen circuit's entities until its name arrives.

        Released either by ``info/name`` arriving or by a
        ``CIRCUIT_NAMES_TIMEOUT`` backstop, so a circuit that never publishes a
        name still gets entities (on the fallback id, which is the best
        available answer at that point).
        """
        if device_id in named_deferrals:
            return
        named_deferrals[device_id] = [
            async_call_later(
                hass, CIRCUIT_NAMES_TIMEOUT, partial(_release_named, device_id)
            ),
            panel.register_property_callback(
                device_id, "info", "name", partial(_release_named, device_id)
            ),
        ]

    @callback
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

        The walk also feeds the platforms: any spec the previous walk didn't
        produce becomes an entity now, so a re-announced or newly commissioned
        descendant comes back without a config-entry reload.
        """
        if panel.controller is None:
            return

        # A descendant that came back inside its grace period is not gone.
        retirement.cancel_for_present(panel.controller.devices)

        refreshed_snapshot = _controller_devices_to_snapshot(panel.controller.devices)
        refreshed_specs = entities_from_tree(refreshed_snapshot)
        _stamp_device_presentation(panel, panel.controller, refreshed_specs)
        _register_panel_and_descendants(
            dr.async_get(hass), entry.entry_id, panel, panel.controller, refreshed_specs
        )

        held = {
            spec.device_id
            for spec in refreshed_specs
            if spec.device_type == DEVICE_TYPE_CIRCUIT
            and spec.device_id not in named_released
            and panel.get_property_value(spec.device_id, "info", "name") is None
        }
        for device_id in held:
            _defer_until_named(device_id)
        addable = (
            refreshed_specs
            if not held
            else [s for s in refreshed_specs if s.device_id not in held]
        )
        _async_add_new_entities(hass, entry, refreshed_specs, addable)

    unregister_callbacks.append(panel.register_tree_state_callback(_on_tree_state))

    unregister_callbacks.append(
        panel.register_device_removed_callback(retirement.schedule)
    )
    unregister_callbacks.append(retirement.cancel_all)

    @callback
    def _cancel_named_deferrals() -> None:
        """Drop any in-flight name waits when the entry unloads."""
        while named_deferrals:
            _, cleanups = named_deferrals.popitem()
            for cleanup in cleanups:
                cleanup()

    unregister_callbacks.append(_cancel_named_deferrals)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "panel": panel,
        "entity_specs": entity_specs,
        # device_id -> the unique_ids already handed to a platform for it, so a
        # later tree walk adds only what is genuinely new. Keyed by device
        # rather than a flat set so retirement can drop a device's ids exactly,
        # without prefix matching: a retired device that is later re-announced
        # has to be able to create its entities again.
        "created_by_device": {},
        # Platform.<X> -> callable taking a spec list and adding the new ones.
        # Populated by each platform's async_setup_entry.
        "adders": {},
        "unregister_callbacks": unregister_callbacks,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


class _DeferredDeviceRetirement:
    """Hold a dropped descendant's HA device until its absence is confirmed.

    ``async_remove_device`` deletes every entity registered on the device, and
    from the user's side that is not recoverable: entity ids, long-term
    statistics and any Energy Dashboard rows built on them go with it. A panel
    drops and re-announces parts of its tree for reasons that have nothing to do
    with a circuit being decommissioned (a retained ``$state`` clear, a partial
    ``$description.children`` republish), so one removal signal only means
    "absent for now". The device is retired only if it is still absent after
    ``DEVICE_REMOVAL_GRACE``.

    The asymmetry is the whole argument: a device row that outlives its circuit
    is cosmetic and the user can delete it, while a device removed in error
    takes history with it.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        panel: Any,
        entry_id: str = "",
        grace: float = DEVICE_REMOVAL_GRACE,
    ) -> None:
        """Initialize the retirement gate for one panel."""
        self._hass = hass
        self._panel = panel
        self._entry_id = entry_id
        self._grace = grace
        self._pending: dict[str, Callable[[], None]] = {}

    @callback
    def schedule(self, device_id: str) -> None:
        """Start (or keep) the grace period for a descendant that just dropped."""
        if device_id in self._pending:
            return
        # INFO, not WARNING: a drop that is re-announced inside the grace
        # period is routine on these panels (observed daily, per panel) and is
        # fully self-healing. WARNING is reserved for the retirement itself,
        # which is the step that actually destroys entities.
        _LOGGER.info(
            "SPAN Panel %s: descendant %s dropped off the tree; holding its Home "
            "Assistant device for %s s in case the panel re-announces it",
            self._panel.serial_number,
            device_id,
            self._grace,
        )
        self._pending[device_id] = async_call_later(
            self._hass, self._grace, partial(self._confirm, device_id)
        )
        # Keeping the device is not the same as pretending it is still live.
        # Nothing in the SDK's drop paths goes through a $state transition, so
        # without this the held entities would keep showing their last value as
        # though current for the whole grace period.
        self._panel.refresh_availability()

    @callback
    def cancel_for_present(self, present_ids: Any) -> None:
        """Call off retirement for any held descendant that has come back."""
        for device_id in list(self._pending):
            if device_id in present_ids:
                self._pending.pop(device_id)()
                _LOGGER.info(
                    "SPAN Panel %s: descendant %s re-announced within the grace "
                    "period; keeping its device and entities",
                    self._panel.serial_number,
                    device_id,
                )

    @callback
    def cancel_all(self) -> None:
        """Drop every in-flight grace timer (the config entry is unloading)."""
        while self._pending:
            _, cancel = self._pending.popitem()
            cancel()

    @callback
    def _confirm(self, device_id: str, _now: Any = None) -> None:
        """Grace period elapsed: retire the device unless it came back."""
        self._pending.pop(device_id, None)
        controller = self._panel.controller
        if controller is not None and device_id in controller.devices:
            _LOGGER.info(
                "SPAN Panel %s: descendant %s is back; keeping its device",
                self._panel.serial_number,
                device_id,
            )
            return
        dev_reg = dr.async_get(self._hass)
        ha_device = dev_reg.async_get_device(
            identifiers={(DOMAIN, f"{self._panel.serial_number}_{device_id}")}
        )
        if ha_device is None:
            return
        _LOGGER.warning(
            "SPAN Panel %s: descendant %s absent for %s s; removing its Home "
            "Assistant device, which also deletes the entities registered on it",
            self._panel.serial_number,
            device_id,
            self._grace,
        )
        self._forget_created(device_id)
        dev_reg.async_remove_device(ha_device.id)

    def _forget_created(self, device_id: str) -> None:
        """Drop a retired device's unique_ids from the created-entity bookkeeping.

        Home Assistant deletes the entity registry entries along with the
        device, so leaving the ids behind would make the platform adders skip
        them forever and a re-announced descendant would get a device row with
        nothing on it: the same "entities never come back" failure this gate
        exists to prevent, displaced by the grace period.
        """
        data = self._hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is not None:
            data["created_by_device"].pop(device_id, None)


@callback
def _async_add_new_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    specs: list[Any],
    addable: list[Any] | None = None,
) -> None:
    """Hand a refreshed spec list to every platform that has registered.

    Each platform's adder filters to its own specs and skips unique_ids it has
    already created, so this is a no-op on a tree that has not changed shape.
    Runs from the tree-state hook, which fires before the platforms finish
    setting up on the very first pass; the ``adders`` dict is simply empty then
    and the initial entities come from the platform setup itself.
    """
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is None:
        return
    data["entity_specs"] = specs
    total = 0
    # ``specs`` is the full walk (what a platform setting up later should see);
    # ``addable`` is the subset cleared for creation right now.
    offered = specs if addable is None else addable
    for adder in list(data["adders"].values()):
        total += adder(offered)
    if total:
        _LOGGER.info(
            "SPAN Panel %s: tree walk added %d new entities",
            data["panel"].serial_number,
            total,
        )


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
    # ``list(...)`` throughout: these are the SDK's own live dicts, mutated from
    # the paho network thread with no lock, and this walk runs on the HA loop.
    # A concurrent insert would raise "dictionary changed size during iteration"
    # and abandon the whole walk, which is now the only path by which a
    # re-announced descendant gets its entities back.
    flat: dict[str, Any] = {}
    for node_id, node_props in list((props or {}).items()):
        if isinstance(node_props, dict):
            for prop_id, value in list(node_props.items()):
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
    for device_id, dev in list(devices.items()):
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

    The panel firmware publishes the cascade topology via the lugs-up
    ``connection`` capability: ``fed-by-device-id`` carries the serial of whatever feeds this
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

    return unload_ok
