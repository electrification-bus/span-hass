"""Tests for the SPAN Panel (eBus) sensor platform runtime value handling."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import Platform, UnitOfPower

from custom_components.span_ebus.const import CAPABILITY_CONNECTION, CAPABILITY_METER
from custom_components.span_ebus.node_mappers import EntitySpec
from custom_components.span_ebus.sensor import SpanEbusSensor


class _FakePanel:
    """Minimal stand-in exposing only what entity construction + value handling need."""

    serial_number = "nt-0000-test1"

    def __init__(self, props: dict[tuple[str, str, str], str] | None = None) -> None:
        self._props = {} if props is None else props

    def get_property_value(
        self, device_id: str, capability: str, property_id: str
    ) -> str | None:
        return self._props.get((device_id, capability, property_id))


def _circuit_power_spec(device_id: str) -> EntitySpec:
    """Build the circuit active-power spec as emitted by _map_circuit_meter."""
    return EntitySpec(
        device_id=device_id,
        capability=CAPABILITY_METER,
        property_id="active-power",
        platform=Platform.SENSOR,
        name="Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.WATT,
        negate=True,
        pv_sign_aware=True,
    )


def test_load_circuit_power_is_negated() -> None:
    """A load circuit (no PV connection) keeps the consumption sign flip."""
    panel = _FakePanel()
    sensor = SpanEbusSensor(panel, _circuit_power_spec("circ-load"))
    sensor._update_from_value("83.3")
    assert sensor.native_value == -83.3


def test_non_pv_der_circuit_power_is_negated() -> None:
    """A circuit feeding a non-PV DER (e.g. EVSE) still gets the load sign flip."""
    panel = _FakePanel(
        {("circ-evse", CAPABILITY_CONNECTION, "feeds-device-type"): "energy.ebus.device.evse"}
    )
    sensor = SpanEbusSensor(panel, _circuit_power_spec("circ-evse"))
    sensor._update_from_value("83.3")
    assert sensor.native_value == -83.3


def test_pv_feed_circuit_power_is_not_negated() -> None:
    """A PV-feed circuit reports positive generation already.

    The sensor must read the live feeds-device-type and suppress the flip, so
    the published power agrees in sign with the positive imported-energy counter
    and renders a positive solar band in the Energy Dashboard.
    """
    panel = _FakePanel(
        {("circ-pv", CAPABILITY_CONNECTION, "feeds-device-type"): "energy.ebus.device.pv"}
    )
    sensor = SpanEbusSensor(panel, _circuit_power_spec("circ-pv"))
    sensor._update_from_value("83.3")
    assert sensor.native_value == 83.3


def test_pv_detection_is_late_binding_and_sticky() -> None:
    """feeds-device-type can arrive after the first power sample (startup race).

    The first update before the retained connection value lands is negated; once
    feeds-device-type appears the sensor flips to positive and stays there even
    if the value later reads back empty (it does not change at runtime).
    """
    props: dict[tuple[str, str, str], str] = {}
    panel = _FakePanel(props)
    sensor = SpanEbusSensor(panel, _circuit_power_spec("circ-pv"))

    # feeds-device-type not yet present → treated as a load circuit, negated.
    sensor._update_from_value("100.0")
    assert sensor.native_value == -100.0

    # Retained connection value arrives → subsequent samples are positive.
    props[("circ-pv", CAPABILITY_CONNECTION, "feeds-device-type")] = "energy.ebus.device.pv"
    sensor._update_from_value("100.0")
    assert sensor.native_value == 100.0

    # Sticky: a transient empty read does not revert the determination.
    props.clear()
    sensor._update_from_value("100.0")
    assert sensor.native_value == 100.0
