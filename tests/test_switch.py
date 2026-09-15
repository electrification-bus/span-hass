"""Tests for the SPAN Panel (eBus) switch platform relay write gate.

The panel decides whether a circuit's relay may be operated and says so on
``switch/relay-controllable``; a circuit commissioned as permanently on or
permanently off reports it false. These tests pin the gate so a relay command
can never reach a circuit the panel has locked.
"""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.span_ebus.const import CAPABILITY_SHED, CAPABILITY_SWITCH, DOMAIN
from custom_components.span_ebus.node_mappers import EntitySpec
from custom_components.span_ebus.switch import (
    PROPERTY_RELAY,
    PROPERTY_RELAY_CONTROLLABLE,
    SpanEbusSwitch,
)

CIRCUIT = "circ-0001"


class _FakePanel:
    """Minimal stand-in exposing only what the write gate reads, plus a write log."""

    serial_number = "nt-0000-test1"

    def __init__(
        self,
        props: dict[tuple[str, str, str], str] | None = None,
        declared: set[tuple[str, str, str]] | None = None,
    ) -> None:
        self._props = {} if props is None else props
        # Defaults to "whatever has a value is also declared", which is what a
        # publisher that implements the capability actually looks like.
        self._declared = set(self._props) if declared is None else declared
        self.writes: list[tuple[str, str, str, str]] = []

    def get_property_value(
        self, device_id: str, capability: str, property_id: str
    ) -> str | None:
        return self._props.get((device_id, capability, property_id))

    def is_property_declared(
        self, device_id: str, capability: str, property_id: str
    ) -> bool:
        return (device_id, capability, property_id) in self._declared

    def set_property(
        self, device_id: str, capability: str, property_id: str, value: str
    ) -> bool:
        self.writes.append((device_id, capability, property_id, value))
        return True


def _relay_spec(settable: bool = True) -> EntitySpec:
    """Build the circuit relay spec as emitted by the mapper."""
    return EntitySpec(
        device_id=CIRCUIT,
        capability=CAPABILITY_SWITCH,
        property_id=PROPERTY_RELAY,
        platform=Platform.SWITCH,
        name="Relay",
        settable=settable,
    )


def _controllable(value: str) -> dict[tuple[str, str, str], str]:
    return {(CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY_CONTROLLABLE): value}


async def test_controllable_circuit_accepts_both_commands() -> None:
    """The ordinary case: the panel reports the relay controllable, writes go out."""
    panel = _FakePanel(_controllable("true"))
    switch = SpanEbusSwitch(panel, _relay_spec())

    await switch.async_turn_on()
    await switch.async_turn_off()

    assert panel.writes == [
        (CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY, "CLOSED"),
        (CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY, "OPEN"),
    ]


@pytest.mark.parametrize("method", ["async_turn_on", "async_turn_off"])
async def test_locked_circuit_refuses_and_sends_nothing(method: str) -> None:
    """A circuit commissioned as locked refuses the command without writing."""
    panel = _FakePanel(_controllable("false"))
    switch = SpanEbusSwitch(panel, _relay_spec())

    with pytest.raises(ServiceValidationError) as err:
        await getattr(switch, method)()

    assert panel.writes == []
    # frenck's ask was a HomeAssistantError; ServiceValidationError is one.
    assert isinstance(err.value, HomeAssistantError)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "relay_not_controllable"


async def test_gate_is_read_live_so_recommissioning_takes_effect() -> None:
    """Re-commissioning a circuit changes the gate with no reload and no rebuild."""
    props = _controllable("false")
    panel = _FakePanel(props)
    switch = SpanEbusSwitch(panel, _relay_spec())

    with pytest.raises(ServiceValidationError):
        await switch.async_turn_on()

    props[(CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY_CONTROLLABLE)] = "true"
    await switch.async_turn_on()

    assert panel.writes == [(CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY, "CLOSED")]


async def test_declared_but_valueless_gate_fails_closed() -> None:
    """A declared capability whose retained value has not landed refuses the write.

    ``$settable`` cannot stand in here: the adapter schema declares the relay
    settable unconditionally, so falling back to it would let a command out on a
    circuit that is about to report itself locked.
    """
    panel = _FakePanel(
        props={},
        declared={(CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY_CONTROLLABLE)},
    )
    switch = SpanEbusSwitch(panel, _relay_spec(settable=True))

    with pytest.raises(ServiceValidationError):
        await switch.async_turn_on()
    assert panel.writes == []


async def test_publisher_without_the_capability_falls_back_to_settable() -> None:
    """relay-controllable is SHOULD-level, so a publisher may omit it entirely."""
    allowed = SpanEbusSwitch(_FakePanel(props={}, declared=set()), _relay_spec(True))
    await allowed.async_turn_on()
    assert allowed._panel.writes == [
        (CIRCUIT, CAPABILITY_SWITCH, PROPERTY_RELAY, "CLOSED")
    ]

    refused = SpanEbusSwitch(_FakePanel(props={}, declared=set()), _relay_spec(False))
    with pytest.raises(ServiceValidationError):
        await refused.async_turn_on()
    assert refused._panel.writes == []


async def test_unparseable_gate_value_fails_closed() -> None:
    """A value the panel never promised is not read as permission."""
    panel = _FakePanel(_controllable("MAYBE"))
    switch = SpanEbusSwitch(panel, _relay_spec())

    with pytest.raises(ServiceValidationError):
        await switch.async_turn_on()
    assert panel.writes == []


async def test_non_relay_switch_is_not_gated() -> None:
    """The gate is specific to the circuit relay, not to every boolean switch."""
    spec = EntitySpec(
        device_id="nt-0000-test1",
        capability=CAPABILITY_SHED,
        property_id="override",
        platform=Platform.SWITCH,
        name="Shed Override",
        settable=True,
    )
    panel = _FakePanel()
    switch = SpanEbusSwitch(panel, spec)

    await switch.async_turn_on()
    await switch.async_turn_off()

    assert panel.writes == [
        ("nt-0000-test1", CAPABILITY_SHED, "override", "true"),
        ("nt-0000-test1", CAPABILITY_SHED, "override", "false"),
    ]


def test_translation_key_resolves_in_both_string_files() -> None:
    """A typo between switch.py and the JSON would ship a raw key to the user."""
    root = Path(__file__).resolve().parents[1] / "custom_components" / "span_ebus"
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((root / name).read_text())
        message = data["exceptions"]["relay_not_controllable"]["message"]
        assert "{entity_id}" in message, f"{name} drops the entity_id placeholder"


# ── is_property_declared, the fail-closed half of the gate ────────────────


def test_is_property_declared_reads_the_real_description_shape() -> None:
    """The gate's fail-closed branch depends on parsing $description correctly.

    A wrong key here would silently turn "declared but valueless" into "not
    declared", which falls back to $settable and reopens the hole.
    """
    from unittest.mock import MagicMock

    from custom_components.span_ebus.span_panel import SpanPanel

    # Shape taken verbatim from a live circuit $description.
    device = MagicMock()
    device.description = {
        "homie": "5.0",
        "type": "energy.ebus.device.circuit",
        "nodes": {
            "switch": {
                "properties": {
                    "relay": {"datatype": "enum", "format": "OPEN,CLOSED"},
                    "relay-controllable": {"datatype": "boolean"},
                }
            },
            "meter": {"properties": {"active-power": {"datatype": "float"}}},
        },
    }
    panel = SpanPanel(MagicMock(), "nt-0000-test1", {})
    panel._controller = MagicMock()
    panel._controller.devices = {CIRCUIT: device}

    assert panel.is_property_declared(CIRCUIT, CAPABILITY_SWITCH, "relay-controllable")
    assert panel.is_property_declared(CIRCUIT, CAPABILITY_SWITCH, "relay")
    # Absent property, absent node, absent device, and a device with no
    # description at all must all read as "not declared".
    assert not panel.is_property_declared(CIRCUIT, CAPABILITY_SWITCH, "nope")
    assert not panel.is_property_declared(CIRCUIT, "no-such-node", "relay")
    assert not panel.is_property_declared("no-such-device", CAPABILITY_SWITCH, "relay")

    bare = MagicMock()
    bare.description = None
    panel._controller.devices = {CIRCUIT: bare}
    assert not panel.is_property_declared(CIRCUIT, CAPABILITY_SWITCH, "relay-controllable")


async def test_locked_circuit_from_the_live_wire_shape_is_refused() -> None:
    """End to end on the exact shape a locked circuit publishes.

    Observed on two circuits across the reference panels: relay CLOSED,
    relay-controllable false, and no $settable on the relay.
    """
    from unittest.mock import MagicMock

    from custom_components.span_ebus.span_panel import SpanPanel

    device = MagicMock()
    device.description = {
        "type": "energy.ebus.device.circuit",
        "nodes": {
            "switch": {
                "properties": {
                    "relay": {"datatype": "enum", "format": "OPEN,CLOSED"},
                    "relay-controllable": {"datatype": "boolean"},
                }
            }
        },
    }
    device.get_property = lambda node, prop: {
        ("switch", "relay"): "CLOSED",
        ("switch", "relay-controllable"): False,
    }.get((node, prop))

    panel = SpanPanel(MagicMock(), "nt-0000-test1", {})
    panel._controller = MagicMock()
    panel._controller.devices = {CIRCUIT: device}
    panel._controller.set_property = MagicMock(return_value=True)

    # settable=True is what the class-level adapter schema declares.
    switch = SpanEbusSwitch(panel, _relay_spec(settable=True))
    with pytest.raises(ServiceValidationError):
        await switch.async_turn_off()
    panel._controller.set_property.assert_not_called()
