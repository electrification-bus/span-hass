"""Tests for the SPAN Panel (eBus) sensor platform runtime value handling."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import Platform, UnitOfEnergy, UnitOfPower

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


# ── power-flows sign frame ────────────────────────────────────────────────


def _power_flows_spec(property_id: str) -> EntitySpec:
    """Build the enclosure power-flows spec the way SEMANTICS declares it."""
    from custom_components.span_ebus.const import CAPABILITY_POWER_FLOWS
    from custom_components.span_ebus.semantics import SEMANTICS

    row = SEMANTICS[("distribution-enclosure", CAPABILITY_POWER_FLOWS, property_id)]
    return EntitySpec(
        device_id="nt-0000-test1",
        capability=CAPABILITY_POWER_FLOWS,
        property_id=property_id,
        platform=Platform.SENSOR,
        name=row["name"],
        device_class=row["device_class"],
        state_class=row["state_class"],
        native_unit=UnitOfPower.WATT,
        negate=bool(row.get("negate")),
    )


def test_power_flows_pv_is_positive_while_generating() -> None:
    """SPAN publishes pv negative while generating; HA wants generation positive."""
    sensor = SpanEbusSensor(_FakePanel(), _power_flows_spec("pv"))
    sensor._update_from_value("-3490")
    assert sensor.native_value == 3490


def test_power_flows_grid_is_positive_while_importing() -> None:
    """Importing must read positive.

    SPAN publishes grid positive while exporting; Home Assistant and the lugs
    meter both use the opposite frame.
    """
    sensor = SpanEbusSensor(_FakePanel(), _power_flows_spec("grid"))
    sensor._update_from_value("161")   # panel exporting 161 W
    assert sensor.native_value == -161
    sensor._update_from_value("-500")  # panel importing 500 W
    assert sensor.native_value == 500


def test_power_flows_site_is_left_consumption_positive() -> None:
    """Site already reports consumption positive, so it must not be flipped."""
    sensor = SpanEbusSensor(_FakePanel(), _power_flows_spec("site"))
    sensor._update_from_value("3329.0")
    assert sensor.native_value == 3329.0


def test_power_flows_battery_is_left_raw() -> None:
    """Publish battery unflipped until there is a sample to verify a flip against.

    SPAN's battery sign contradicts the eBus specification and SPAN's own docs
    say it may be corrected upstream.
    """
    sensor = SpanEbusSensor(_FakePanel(), _power_flows_spec("battery"))
    sensor._update_from_value("1000")  # SPAN: positive = charging
    assert sensor.native_value == 1000


def test_power_flows_sign_frame_matches_the_upstream_lugs_meter() -> None:
    """The two entities reporting the same grid flow must not contradict each other.

    Captured live on lc1: upstream-lugs active-power -151 W (panel-perspective:
    negative = exporting) alongside power-flows grid +161 W (source-centric:
    positive = exporting). After mapping, both must read negative.
    """
    lugs = SpanEbusSensor(
        _FakePanel(),
        EntitySpec(
            device_id="nt-0000-test1-lugs-up",
            capability=CAPABILITY_METER,
            property_id="active-power",
            platform=Platform.SENSOR,
            name="Power",
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit=UnitOfPower.WATT,
        ),
    )
    grid = SpanEbusSensor(_FakePanel(), _power_flows_spec("grid"))
    lugs._update_from_value("-151.0")
    grid._update_from_value("161")

    assert lugs.native_value < 0 and grid.native_value < 0


# ── Energy counter decrease deadband ──────────────────────────────────────


def _above(caplog, level: int) -> list[int]:
    """Levels of the captured records at or above ``level``, in order."""
    return [r.levelno for r in caplog.records if r.levelno >= level]


def _energy_spec(unit: str = UnitOfEnergy.WATT_HOUR) -> EntitySpec:
    """Build a cumulative energy counter spec as emitted for a circuit."""
    return EntitySpec(
        device_id="circ-0001",
        capability=CAPABILITY_METER,
        property_id="exported-energy",
        platform=Platform.SENSOR,
        name="Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit=unit,
    )


def test_tiny_decrease_is_still_held(caplog) -> None:
    """Jitter must not reach Home Assistant: any decrease is a meter reset to it."""
    sensor = SpanEbusSensor(_FakePanel(), _energy_spec())
    sensor._update_from_value("9084107.8")
    sensor._update_from_value("9084107.7")
    assert sensor.native_value == 9084107.8


def test_tiny_decrease_does_not_log_a_warning(caplog) -> None:
    """A 0.1 Wh blip arrives every few seconds; it must not warn."""
    sensor = SpanEbusSensor(_FakePanel(), _energy_spec())
    sensor._update_from_value("9084107.8")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        sensor._update_from_value("9084107.7")
    assert _above(caplog, logging.INFO) == []


def test_tiny_decrease_recovery_is_also_silent(caplog) -> None:
    """The recovery notice must match the volume of the notice that opened it."""
    sensor = SpanEbusSensor(_FakePanel(), _energy_spec())
    sensor._update_from_value("9084107.8")
    sensor._update_from_value("9084107.7")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        sensor._update_from_value("9084107.9")
    assert _above(caplog, logging.INFO) == []
    assert sensor.native_value == 9084107.9


def test_large_decrease_still_warns(caplog) -> None:
    """A recalibration is what this guard exists for and must stay loud."""
    sensor = SpanEbusSensor(_FakePanel(), _energy_spec())
    sensor._update_from_value("3655345.7")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        sensor._update_from_value("2641187.7")  # -1,014,158 Wh, an observed event
    assert _above(caplog, logging.INFO) == [logging.WARNING]
    assert sensor.native_value == 3655345.7


def test_large_decrease_recovery_is_reported(caplog) -> None:
    """Having warned, say when it resolved."""
    sensor = SpanEbusSensor(_FakePanel(), _energy_spec())
    sensor._update_from_value("3655345.7")
    sensor._update_from_value("2641187.7")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        sensor._update_from_value("3655346.0")
    assert _above(caplog, logging.INFO) == [logging.INFO]


def test_deadband_is_scaled_for_a_kwh_counter(caplog) -> None:
    """A kWh counter must not inherit a deadband a thousand times too permissive.

    1 Wh is 0.001 kWh, so a 0.5 kWh drop (500 Wh) is far above the threshold and
    has to warn, even though 0.5 is below the raw Wh figure of 1.0.
    """
    sensor = SpanEbusSensor(_FakePanel(), _energy_spec(UnitOfEnergy.KILO_WATT_HOUR))
    sensor._update_from_value("1000.0")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        sensor._update_from_value("999.5")
    assert _above(caplog, logging.INFO) == [logging.WARNING]
    assert sensor.native_value == 1000.0


def test_measurement_sensors_are_free_to_decrease(caplog) -> None:
    """The guard is specific to TOTAL_INCREASING; power may fall all it likes."""
    sensor = SpanEbusSensor(_FakePanel(), _circuit_power_spec("circ-load"))
    sensor._update_from_value("-100.0")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        sensor._update_from_value("-50.0")
    assert caplog.records == []
    assert sensor.native_value == 50.0
