"""Tests for the SPAN Panel (eBus) select platform write gate.

Whether a select takes writes is declared per device by ``$settable``: a circuit
the panel manages itself publishes its shed priority without it. Such a select
is still created to show the value, and refuses a write instead of publishing.
"""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.const import Platform
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.span_ebus.const import DOMAIN
from custom_components.span_ebus.node_mappers import EntitySpec, entities_from_tree
from custom_components.span_ebus.select import SpanEbusSelect

CIRCUIT = "circ-0001"
OPTIONS = ["UNKNOWN", "OFF_GRID", "SOC_THRESHOLD", "NEVER"]
FIXTURE = Path(__file__).parent / "fixtures" / "tree" / "nt-2143-c1akc.json"


class _FakePanel:
    """Minimal stand-in exposing what the select reads, plus a write log."""

    serial_number = "nt-0000-test1"

    def __init__(self, settable: bool) -> None:
        self.settable = settable
        self.writes: list[tuple[str, str, str, str]] = []

    def is_property_settable(self, device_id: str, capability: str, property_id: str) -> bool:
        return self.settable

    def set_property(self, device_id: str, capability: str, property_id: str, value: str) -> bool:
        self.writes.append((device_id, capability, property_id, value))
        return True


def _priority_spec(settable: bool) -> EntitySpec:
    return EntitySpec(
        device_id=CIRCUIT,
        capability="load-shed",
        property_id="priority",
        platform=Platform.SELECT,
        name="Shed Priority",
        options=OPTIONS,
        settable=settable,
    )


async def test_settable_select_publishes_the_option() -> None:
    panel = _FakePanel(settable=True)
    select = SpanEbusSelect(panel, _priority_spec(settable=True))

    await select.async_select_option("SOC_THRESHOLD")

    assert panel.writes == [(CIRCUIT, "load-shed", "priority", "SOC_THRESHOLD")]


async def test_non_settable_select_refuses_and_sends_nothing() -> None:
    panel = _FakePanel(settable=False)
    select = SpanEbusSelect(panel, _priority_spec(settable=False))

    with pytest.raises(ServiceValidationError) as err:
        await select.async_select_option("OFF_GRID")

    assert panel.writes == []
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "option_not_settable"
    entity_id = err.value.translation_placeholders["entity_id"]
    assert entity_id == select.unique_id
    assert entity_id in str(err.value)


async def test_non_settable_select_still_shows_its_value() -> None:
    select = SpanEbusSelect(_FakePanel(settable=False), _priority_spec(settable=False))

    select._update_from_value("NEVER")

    assert select.current_option == "NEVER"


async def test_gate_follows_the_live_description_not_the_build_time_spec() -> None:
    """Re-commissioning republishes $description; the existing entity is not rebuilt."""
    panel = _FakePanel(settable=True)
    select = SpanEbusSelect(panel, _priority_spec(settable=False))

    await select.async_select_option("NEVER")
    assert panel.writes == [(CIRCUIT, "load-shed", "priority", "NEVER")]

    panel.settable = False
    with pytest.raises(ServiceValidationError):
        await select.async_select_option("OFF_GRID")
    assert len(panel.writes) == 1


def test_description_without_settable_builds_a_read_only_select() -> None:
    """The panel-managed circuit's priority is a select, but not a settable one."""
    devices = json.loads(FIXTURE.read_text())["devices"]
    priorities = {
        s.device_id: s
        for s in entities_from_tree(devices)
        if (s.capability, s.property_id) == ("load-shed", "priority")
    }
    declared = {
        device_id: data["description"]["nodes"]["load-shed"]["properties"]["priority"]
        for device_id, data in devices.items()
        if device_id in priorities
    }
    managed = {d for d, decl in declared.items() if "settable" not in decl}
    assert managed, "fixture has no circuit whose priority omits $settable"

    for device_id, spec in priorities.items():
        assert spec.platform == Platform.SELECT
        assert spec.settable is (device_id not in managed)


def test_translation_key_resolves_in_both_string_files() -> None:
    """A typo between select.py and the JSON would ship a raw key to the user."""
    root = Path(__file__).resolve().parents[1] / "custom_components" / "span_ebus"
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((root / name).read_text())
        message = data["exceptions"]["option_not_settable"]["message"]
        assert "{entity_id}" in message, f"{name} drops the entity_id placeholder"


def test_is_property_settable_reads_the_fixture_descriptions() -> None:
    """The live gate agrees with the descriptions the panel publishes."""
    from unittest.mock import MagicMock

    from custom_components.span_ebus.span_panel import SpanPanel

    devices = json.loads(FIXTURE.read_text())["devices"]
    panel = SpanPanel(MagicMock(), "nt-0000-test1", {})
    panel._controller = MagicMock()
    panel._controller.devices = {}
    for device_id, data in devices.items():
        device = MagicMock()
        device.description = data["description"]
        panel._controller.devices[device_id] = device

    seen = {True: 0, False: 0}
    for device_id, data in devices.items():
        node = data["description"].get("nodes", {}).get("load-shed")
        if not node or "priority" not in node.get("properties", {}):
            continue
        expected = bool(node["properties"]["priority"].get("settable", False))
        assert panel.is_property_settable(device_id, "load-shed", "priority") is expected
        seen[expected] += 1
    assert seen[True] and seen[False]

    assert not panel.is_property_settable("no-such-device", "load-shed", "priority")
    assert not panel.is_property_settable(next(iter(devices)), "no-such-node", "priority")
