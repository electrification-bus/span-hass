"""Tests for non-platform helpers in ``__init__.py``."""

from __future__ import annotations

from unittest.mock import MagicMock

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
    """G3P-24911 cascade case: this panel's upstream is another panel.

    Matches the example from SPAN-c7h: panel lc2 sees lc1 (a
    distribution-enclosure) as its upstream and should link via_device → lc1.
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

    Single-panel install with grid feed, or a panel whose firmware predates
    G3P-24911 and doesn't publish the triplet at all.
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
