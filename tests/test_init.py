"""Tests for non-platform helpers in ``__init__.py``."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from custom_components import span_ebus
from custom_components.span_ebus import (
    _build_mqtt_cfg,
    _controller_devices_to_snapshot,
    _resolve_upstream_panel,
)
from custom_components.span_ebus.const import (
    CONF_CA_CERT_PEM,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOST,
)


def _mock_panel(serial: str, fed_by_id: str | None, fed_by_type: str | None) -> MagicMock:
    """Build a SpanPanel mock that returns the given lugs-up/connection values."""
    panel = MagicMock()
    panel.serial_number = serial

    expected = {
        (f"{serial}-lugs-up", "connection", "fed-by-device-id"): fed_by_id,
        (f"{serial}-lugs-up", "connection", "fed-by-device-type"): fed_by_type,
    }
    panel.get_property_value = lambda *args, **kwargs: expected.get(args)
    return panel


def test_controller_devices_to_snapshot_flattens_nested_properties() -> None:
    """Flatten nested ``DiscoveredDevice.properties`` to ``"capability/property"`` keys.

    The SDK exposes properties nested by node; the snapshot the mappers consume
    must flatten them so the sibling-gate lookups (e.g.
    ``"connection/feeds-device-type"``) resolve at runtime instead of silently
    falling back to defaults.
    """
    dev = MagicMock()
    dev.description = {"type": "energy.ebus.device.circuit", "nodes": {}}
    dev.properties = {
        "switch": {"relay": "CLOSED", "relay-controllable": True},
        "info": {"direction": "UPSTREAM"},
    }
    dev.parent_id = "root"
    dev.children_ids = []
    dev.is_root = False
    dev.root_id = "root"

    snap = _controller_devices_to_snapshot({"dev1": dev})

    assert snap["dev1"]["properties"] == {
        "switch/relay": "CLOSED",
        "switch/relay-controllable": True,
        "info/direction": "UPSTREAM",
    }


def test_build_mqtt_cfg_prefers_discovered_ip_over_local_broker_host() -> None:
    """The MQTT host must be the reachable discovered IP, not the panel ``.local``.

    On HA OS the container resolver returns an IPv6-only (unroutable) result for
    ``.local`` broker names; dialing the discovered IP (which is also in the cert
    SAN) sidesteps that resolver entirely.
    """
    cfg = _build_mqtt_cfg(
        {
            CONF_HOST: "192.168.128.95",
            CONF_EBUS_BROKER_HOST: "span-nt-2143-c1akc.local",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "nt-2143-c1akc",
            CONF_EBUS_BROKER_PASSWORD: "pw",
            CONF_CA_CERT_PEM: "CA-PEM",
        }
    )
    assert cfg["host"] == "192.168.128.95"
    assert cfg["port"] == 8883
    assert cfg["tls_insecure"] is False  # CA present -> verify


def test_build_mqtt_cfg_falls_back_to_broker_host_without_discovered_ip() -> None:
    """Without a stored discovered IP, fall back to the ``.local`` broker host."""
    cfg = _build_mqtt_cfg(
        {
            CONF_EBUS_BROKER_HOST: "span-nt-2143-c1akc.local",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "u",
            CONF_EBUS_BROKER_PASSWORD: "p",
        }
    )
    assert cfg["host"] == "span-nt-2143-c1akc.local"
    assert cfg["tls_insecure"] is True  # no CA


def test_resolve_upstream_panel_returns_serial_for_distribution_enclosure() -> None:
    """Cascade case: this panel's upstream is another panel.

    Panel lc2 sees lc1 (a distribution-enclosure) as its upstream and should
    link via_device → lc1.
    """
    panel = _mock_panel(
        serial="nt-2204-lc2",
        fed_by_id="nt-2143-lc1",
        fed_by_type="energy.ebus.device.distribution-enclosure",
    )
    assert _resolve_upstream_panel(panel) == "nt-2143-lc1"


def test_resolve_upstream_panel_returns_none_for_bess() -> None:
    """Top-of-cascade case: this panel sits directly under a BESS.

    The BESS is already a child of this panel via the Homie parent/child
    tree (BESS publishes parent = panel-serial), so setting via_device on
    the panel pointing at the BESS would create a cycle. Return None so
    the panel stays at the top of the HA device hierarchy.
    """
    panel = _mock_panel(
        serial="nt-2143-c1akc",
        fed_by_id="nt-2143-c1akc-tg121153003k7g",
        fed_by_type="energy.ebus.device.bess",
    )
    assert _resolve_upstream_panel(panel) is None


def test_resolve_upstream_panel_returns_none_for_utility_feed() -> None:
    """No upstream pointer published = utility feed.

    Single-panel install with grid feed, or a panel whose firmware predates the
    cascade-topology feature and doesn't publish the triplet at all.
    """
    panel = _mock_panel(
        serial="nt-2143-c1akc", fed_by_id=None, fed_by_type=None
    )
    assert _resolve_upstream_panel(panel) is None


def test_resolve_upstream_panel_returns_none_when_id_present_but_type_unknown() -> None:
    """Defensive against unknown fed-by-device-type values.

    A fed-by-device-id without a recognised type shouldn't crash or guess.
    Return None so the panel stays unlinked rather than mislink.
    """
    panel = _mock_panel(
        serial="nt-2143-c1akc",
        fed_by_id="some-other-device",
        fed_by_type="io.somevendor.gadget",
    )
    assert _resolve_upstream_panel(panel) is None


# ── Deferred device retirement ────────────────────────────────────────────


class _FakeClock:
    """Stand-in for ``async_call_later`` that fires on demand instead of on time."""

    def __init__(self) -> None:
        self.timers: list[tuple[float, object]] = []
        self.cancelled = 0

    def __call__(self, _hass, delay, action):
        index = len(self.timers)
        self.timers.append((delay, action))

        def cancel() -> None:
            self.cancelled += 1
            self.timers[index] = (delay, None)

        return cancel

    def fire_all(self) -> None:
        for _delay, action in list(self.timers):
            if action is not None:
                action()


def _retirement_panel(serial: str, present: list[str]) -> MagicMock:
    panel = MagicMock()
    panel.serial_number = serial
    panel.controller.devices = dict.fromkeys(present)
    return panel


def _retirement_setup(monkeypatch, present: list[str]):
    """Build a retirement gate wired to a fake clock and a fake device registry."""
    clock = _FakeClock()
    monkeypatch.setattr(span_ebus, "async_call_later", clock)

    device = MagicMock()
    device.id = "ha-device-id"
    dev_reg = MagicMock()
    dev_reg.async_get_device_by_identifier.return_value = device
    monkeypatch.setattr(span_ebus.dr, "async_get", lambda _hass: dev_reg)

    panel = _retirement_panel("nt-0000-test1", present)
    hass = MagicMock()
    hass.data = {
        span_ebus.DOMAIN: {
            "entry-1": {
                "panel": panel,
                "entity_specs": [],
                "created_by_device": {},
                "adders": {},
            }
        }
    }
    monkeypatch.setattr(span_ebus.dr, "async_get", lambda _h: dev_reg)
    gate = span_ebus._DeferredDeviceRetirement(hass, panel, "entry-1", grace=900)
    return gate, clock, dev_reg, panel, hass


def test_dropped_descendant_is_not_removed_immediately(monkeypatch) -> None:
    """One removal signal must not delete the device (and so its entities)."""
    gate, clock, dev_reg, _panel, _hass = _retirement_setup(monkeypatch, present=[])

    gate.schedule("circ-0001")

    dev_reg.async_remove_device.assert_not_called()
    assert [delay for delay, _ in clock.timers] == [900]


def test_descendant_still_absent_after_grace_is_retired(monkeypatch) -> None:
    """A genuine decommissioning still retires the device, just not instantly."""
    gate, clock, dev_reg, _panel, _hass = _retirement_setup(monkeypatch, present=[])

    gate.schedule("circ-0001")
    clock.fire_all()

    dev_reg.async_remove_device.assert_called_once_with("ha-device-id")


def test_descendant_back_before_grace_expires_is_kept(monkeypatch) -> None:
    """The daily drop-and-re-announce case: the device (and history) survives."""
    gate, clock, dev_reg, panel, _hass = _retirement_setup(monkeypatch, present=[])

    gate.schedule("circ-0001")
    panel.controller.devices["circ-0001"] = MagicMock()
    gate.cancel_for_present(panel.controller.devices)
    clock.fire_all()

    assert clock.cancelled == 1
    dev_reg.async_remove_device.assert_not_called()


def test_descendant_present_again_at_confirmation_is_kept(monkeypatch) -> None:
    """Even with no tree-state edge to cancel the timer, presence wins at the end."""
    gate, clock, dev_reg, panel, _hass = _retirement_setup(monkeypatch, present=[])

    gate.schedule("circ-0001")
    panel.controller.devices["circ-0001"] = MagicMock()
    clock.fire_all()

    dev_reg.async_remove_device.assert_not_called()


def test_repeated_removal_signals_do_not_stack_timers(monkeypatch) -> None:
    """A chatty publisher must not queue one retirement per message."""
    gate, clock, _dev_reg, _panel, _hass = _retirement_setup(monkeypatch, present=[])

    gate.schedule("circ-0001")
    gate.schedule("circ-0001")
    gate.schedule("circ-0001")

    assert len(clock.timers) == 1


def test_unload_cancels_pending_retirements(monkeypatch) -> None:
    """Unloading the entry must not leave a timer that fires into a dead registry."""
    gate, clock, dev_reg, _panel, _hass = _retirement_setup(monkeypatch, present=[])

    gate.schedule("circ-0001")
    gate.schedule("circ-0002")
    gate.cancel_all()
    clock.fire_all()

    assert clock.cancelled == 2
    dev_reg.async_remove_device.assert_not_called()


# ── Post-setup entity additions ───────────────────────────────────────────


def _hass_with_entry(specs: list, adders: dict) -> tuple[MagicMock, MagicMock, dict]:
    entry = MagicMock()
    entry.entry_id = "entry-1"
    panel = MagicMock()
    panel.serial_number = "nt-0000-test1"
    data = {
        "panel": panel,
        "entity_specs": specs,
        "created_by_device": {},
        "adders": adders,
    }
    hass = MagicMock()
    hass.data = {span_ebus.DOMAIN: {"entry-1": data}}
    return hass, entry, data


def test_tree_walk_hands_new_specs_to_every_platform() -> None:
    """The refreshed walk must reach the platforms, not be computed and dropped."""
    seen: dict[str, list] = {}

    def _adder(name):
        def add(specs):
            seen[name] = specs
            return len(specs)

        return add

    hass, entry, data = _hass_with_entry([], {"sensor": _adder("sensor"), "switch": _adder("switch")})
    refreshed = ["spec-a", "spec-b"]

    span_ebus._async_add_new_entities(hass, entry, refreshed)

    assert seen == {"sensor": refreshed, "switch": refreshed}
    assert data["entity_specs"] == refreshed


def test_tree_walk_after_unload_is_a_no_op() -> None:
    """A tree-state edge racing config-entry teardown must not raise."""
    hass = MagicMock()
    hass.data = {span_ebus.DOMAIN: {}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    span_ebus._async_add_new_entities(hass, entry, ["spec-a"])


def test_platform_setup_registers_an_adder_and_dedupes_by_unique_id() -> None:
    """A second walk over the same tree adds nothing, so no ``_2`` duplicates."""
    from homeassistant.const import Platform

    from custom_components.span_ebus.entity_base import async_setup_platform_entities
    from custom_components.span_ebus.node_mappers import EntitySpec

    spec = EntitySpec(
        device_id="circ-0001",
        capability="meter",
        property_id="active-power",
        platform=Platform.SENSOR,
        name="Power",
    )
    other = EntitySpec(
        device_id="circ-0002",
        capability="meter",
        property_id="active-power",
        platform=Platform.SENSOR,
        name="Power",
    )
    added: list[list] = []
    hass, entry, data = _hass_with_entry([spec], {})
    data["panel"].serial_number = "nt-0000-test1"

    async_setup_platform_entities(
        hass, entry, Platform.SENSOR, lambda e: added.append(e), lambda _p, s: s
    )
    assert added == [[spec]]

    # Same tree again: nothing new.
    data["adders"][Platform.SENSOR]([spec])
    assert added == [[spec]]

    # A newly commissioned circuit does get created.
    data["adders"][Platform.SENSOR]([spec, other])
    assert added == [[spec], [other]]


def test_platform_setup_ignores_specs_owned_by_other_platforms() -> None:
    """Each platform takes only its own specs off the shared list."""
    from homeassistant.const import Platform

    from custom_components.span_ebus.entity_base import async_setup_platform_entities
    from custom_components.span_ebus.node_mappers import EntitySpec

    sensor_spec = EntitySpec(
        device_id="circ-0001",
        capability="meter",
        property_id="active-power",
        platform=Platform.SENSOR,
        name="Power",
    )
    switch_spec = EntitySpec(
        device_id="circ-0001",
        capability="switch",
        property_id="relay",
        platform=Platform.SWITCH,
        name="Relay",
    )
    added: list[list] = []
    hass, entry, _data = _hass_with_entry([sensor_spec, switch_spec], {})

    async_setup_platform_entities(
        hass, entry, Platform.SWITCH, lambda e: added.append(e), lambda _p, s: s
    )

    assert added == [[switch_spec]]


def test_retirement_forgets_the_device_so_it_can_come_back(monkeypatch) -> None:
    """A retired descendant that is re-announced must get its entities again.

    Home Assistant deletes the entity registry entries along with the device, so
    the created-entity bookkeeping has to forget them too. Leaving them behind
    reproduces the exact failure this gate exists to prevent (a device row with
    nothing on it), just displaced by the grace period.
    """
    from homeassistant.const import Platform

    from custom_components.span_ebus.entity_base import async_setup_platform_entities
    from custom_components.span_ebus.node_mappers import EntitySpec

    gate, clock, dev_reg, panel, hass = _retirement_setup(monkeypatch, present=[])
    entry = MagicMock()
    entry.entry_id = "entry-1"
    spec = EntitySpec(
        device_id="circ-0001",
        capability="meter",
        property_id="active-power",
        platform=Platform.SENSOR,
        name="Power",
    )
    data = hass.data[span_ebus.DOMAIN]["entry-1"]
    data["entity_specs"] = [spec]

    added: list[list] = []
    async_setup_platform_entities(
        hass, entry, Platform.SENSOR, lambda e: added.append(e), lambda _p, s: s
    )
    assert added == [[spec]]

    # The circuit is decommissioned for real: dropped, and still gone at expiry.
    gate.schedule("circ-0001")
    clock.fire_all()
    dev_reg.async_remove_device.assert_called_once()

    # Now the panel re-announces it. Its entities must be created again.
    panel.controller.devices["circ-0001"] = MagicMock()
    span_ebus._async_add_new_entities(hass, entry, [spec])
    assert added == [[spec], [spec]], "re-announced device got no entities back"


def test_drop_marks_held_entities_unavailable(monkeypatch) -> None:
    """Holding the device must not make its entities look live with a stale value.

    None of the SDK's drop paths go through a ``$state`` transition, so nothing
    else would re-evaluate availability for the grace period's duration.
    """
    gate, _clock, _dev_reg, panel, _hass = _retirement_setup(monkeypatch, present=[])
    gate.schedule("circ-0001")
    panel.refresh_availability.assert_called_once()


def test_retirement_grace_defaults_to_the_configured_constant() -> None:
    """The default must be the shared constant, not an ad-hoc number."""
    gate = span_ebus._DeferredDeviceRetirement(MagicMock(), MagicMock(), "entry-1")
    assert gate._grace == span_ebus.DEVICE_REMOVAL_GRACE


def test_snapshot_survives_a_concurrent_insert_from_the_paho_thread() -> None:
    """The SDK's dicts are mutated off-loop with no lock; the walk must not abort.

    A ``RuntimeError: dictionary changed size during iteration`` here is caught
    and logged by the dispatcher, silently skipping the only path by which a
    re-announced descendant regains its entities. Reproduced by mutating the
    dict from inside the loop body, which is where the paho thread's insert
    would land.
    """
    devices: dict[str, Any] = {}

    def _device(on_read=None) -> MagicMock:
        dev = MagicMock()
        dev.properties = {}
        dev.parent_id = "root"
        dev.children_ids = []
        dev.is_root = False
        dev.root_id = "root"
        if on_read is None:
            dev.description = {"type": "energy.ebus.device.circuit", "nodes": {}}
        else:
            type(dev).description = property(lambda _self: on_read())
        return dev

    def _insert_like_paho() -> dict[str, Any]:
        devices["late-arrival"] = _device()
        return {"type": "energy.ebus.device.circuit", "nodes": {}}

    devices["dev1"] = _device(on_read=_insert_like_paho)
    devices["dev2"] = _device()

    snap = _controller_devices_to_snapshot(devices)
    assert "dev1" in snap and "dev2" in snap


def test_flatten_survives_a_concurrent_property_insert() -> None:
    """Same race one level down, on DiscoveredDevice.properties."""
    props: dict[str, Any] = {}

    class _Node(dict):
        def items(self):
            props["late-node"] = {"x": 1}
            return super().items()

    props["meter"] = _Node({"active-power": 1.0})
    flat = span_ebus._flatten_properties(props)
    assert flat["meter/active-power"] == 1.0


def test_description_arrival_dispatches_a_tree_walk() -> None:
    """A description is a tree change: the SDK delivers $state before it.

    The entity structure comes entirely from the description, so a walk that
    runs between a descendant's ready edge and its description produces no
    specs for it. Without a dispatch here, the last device in a re-announce
    wave would wait for some unrelated device's ready edge.
    """
    from custom_components.span_ebus.span_panel import SpanPanel

    hass = MagicMock()
    scheduled: list = []
    hass.loop.call_soon_threadsafe = lambda fn, *a: scheduled.append(fn)

    panel = SpanPanel(hass, "nt-0000-test1", {})
    device = MagicMock()
    device.device_id = "circ-0001"  # a descendant, not the root

    panel._on_description_received(device)

    assert panel._dispatch_tree_state in scheduled


# ── Device hierarchy (via_device_id) ──────────────────────────────────────


def test_parent_identifier_points_descendants_at_the_right_row() -> None:
    """A MID hangs off its BESS; everything else hangs off the panel."""
    from custom_components.span_ebus.util import parent_identifier

    panel = "nt-0000-test1"
    assert parent_identifier(panel, None) == (span_ebus.DOMAIN, panel)
    assert parent_identifier(panel, panel) == (span_ebus.DOMAIN, panel)
    assert parent_identifier(panel, "bess-1") == (
        span_ebus.DOMAIN,
        f"{panel}_bess-1",
    )


def _dev(dev_id: str, via: str | None = None) -> MagicMock:
    d = MagicMock()
    d.id = dev_id
    d.via_device_id = via
    return d


def test_link_parents_sets_via_device_id() -> None:
    """Hierarchy is a property of the device row now, not of DeviceInfo."""
    child, parent = _dev("child-row"), _dev("parent-row")
    reg = MagicMock()
    reg.async_get_device_by_identifier.return_value = parent

    span_ebus._link_parents(reg, "entry-1", [(child, (span_ebus.DOMAIN, "p"))])

    reg.async_update_device.assert_called_once_with(
        "child-row", via_device_id="parent-row"
    )


def test_link_parents_is_idempotent() -> None:
    """The tree walk re-runs constantly; an unchanged link must not be rewritten."""
    child, parent = _dev("child-row", via="parent-row"), _dev("parent-row")
    reg = MagicMock()
    reg.async_get_device_by_identifier.return_value = parent

    span_ebus._link_parents(reg, "entry-1", [(child, (span_ebus.DOMAIN, "p"))])

    reg.async_update_device.assert_not_called()


def test_link_parents_skips_a_parent_that_is_not_registered_yet() -> None:
    """An upstream sister panel whose own config entry has not set up yet."""
    child = _dev("child-row")
    reg = MagicMock()
    reg.async_get_device_by_identifier.return_value = None
    reg.async_get_devices.return_value = []

    span_ebus._link_parents(reg, "entry-1", [(child, (span_ebus.DOMAIN, "p"))])

    reg.async_update_device.assert_not_called()


def test_find_device_falls_back_across_config_entries() -> None:
    """A cascade's upstream panel is a different config entry.

    ``async_get_device_by_identifier`` only searches the entry it is given, so
    the panel-to-panel link has to fall back to the cross-entry lookup.
    """
    sister = _dev("sister-row")
    reg = MagicMock()
    reg.async_get_device_by_identifier.return_value = None
    reg.async_get_devices.return_value = [sister]

    found = span_ebus._find_device(reg, "entry-1", (span_ebus.DOMAIN, "nt-other"))
    assert found is sister


def test_find_device_refuses_an_ambiguous_cross_entry_match() -> None:
    """Identifiers are only unique within an entry, which is why the old API went away."""
    reg = MagicMock()
    reg.async_get_device_by_identifier.return_value = None
    reg.async_get_devices.return_value = [_dev("a"), _dev("b")]

    assert span_ebus._find_device(reg, "entry-1", (span_ebus.DOMAIN, "x")) is None


def test_device_info_builders_carry_no_parent_link() -> None:
    """HA 2026.9 removed via_device from DeviceInfo; leaving it in would raise."""
    from custom_components.span_ebus.util import (
        descendant_device_info,
        panel_device_info,
    )

    panel = panel_device_info("nt-0000-test1", "fw-1")
    child = descendant_device_info(
        panel_serial="nt-0000-test1",
        device_id="circ-1",
        device_type="circuit",
        device_name="Kitchen",
    )
    for info in (panel, child):
        assert "via_device" not in info
        assert "via_device_id" not in info
    assert panel["identifiers"] == {(span_ebus.DOMAIN, "nt-0000-test1")}
    assert child["identifiers"] == {(span_ebus.DOMAIN, "nt-0000-test1_circ-1")}


def test_registry_apis_the_integration_calls_actually_exist() -> None:
    """Guard the declared minimum Home Assistant version in ``hacs.json``.

    The device-registry tests mock the registry, so they pass against any
    Home Assistant release and cannot catch a method that does not exist yet.
    These are the real symbols, and they set the floor: ``via_device_id`` on
    ``DeviceInfo`` landed by 2026.5, but ``async_get_device_by_identifier`` only
    appeared in 2026.8, which is why ``hacs.json`` declares 2026.8.0.
    """
    import typing

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo, DeviceRegistry

    for name in (
        "async_get_device_by_identifier",
        "async_get_devices",
        "async_get_or_create",
        "async_update_device",
        "async_remove_device",
    ):
        assert hasattr(DeviceRegistry, name), f"DeviceRegistry.{name} is missing"

    assert hasattr(HomeAssistant, "async_add_import_executor_job")

    keys = typing.get_type_hints(DeviceInfo)
    assert "via_device_id" in keys, "DeviceInfo.via_device_id is required"

    import inspect

    assert "via_device_id" in inspect.signature(DeviceRegistry.async_get_or_create).parameters


def test_every_integration_module_imports() -> None:
    """Import all of them, so a floor-only API in any module is exercised.

    The tests reach most modules indirectly, but not all: nothing else here
    imports ``binary_sensor`` or ``select``, so an API that exists on the
    development pin and not on the declared minimum could hide there. mypy
    covers this statically on both CI legs; this covers it at import time.
    """
    import importlib
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[1] / "custom_components" / "span_ebus"
    modules = sorted(p.stem for p in pkg.glob("*.py") if p.stem != "__init__")
    assert {"binary_sensor", "select", "sensor", "switch"} <= set(modules)
    for name in modules:
        importlib.import_module(f"custom_components.span_ebus.{name}")
    importlib.import_module("custom_components.span_ebus")
