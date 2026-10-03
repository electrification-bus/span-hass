"""Tests for the description-driven node-mapper builder.

``entities_from_tree`` walks a Homie tree snapshot and, for every property a
device's ``$description`` declares, looks up the HA presentation in
``semantics.SEMANTICS`` and reads structure (unit / enum options / settable)
from the live description. These tests exercise it end-to-end against a real
panel snapshot (``fixtures/tree/nt-2143-c1akc.json``, a live lc1 dump) plus
focused unit tests of the builder directives.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, Platform
import pytest

from custom_components.span_ebus.node_mappers import (
    EntitySpec,
    _build_spec,
    _lug_direction,
    _parse_enum_format,
    device_type_short,
    entities_from_tree,
)
from custom_components.span_ebus.util import make_unique_id

FIXTURES = Path(__file__).parent / "fixtures" / "tree"

SERIAL = "nt-2143-c1akc"
ROOT = SERIAL
LUGS_UP = f"{SERIAL}-lugs-up"
LUGS_DN = f"{SERIAL}-lugs-dn"
BESS = f"{SERIAL}-tg121153003k7g"
MID = f"{BESS}-mid"
PV = f"{SERIAL}-iq7plus-72-x-us"
LOAD_CIRCUIT = "ac3dccda46a94b98878a227df6fed588"  # "Ovens"
PV_CIRCUIT = "a9942a48e45f484b92a58adc27e78a60"  # feeds the PV device


def _load(name: str) -> dict[str, Any]:
    """Load a tree snapshot fixture and return its ``devices`` map."""
    data = json.loads((FIXTURES / name).read_text())
    assert data["metadata"]["schema"] == "tree-v1"
    return data["devices"]


@pytest.fixture
def lc1() -> dict[str, Any]:
    """Load the lc1 panel snapshot (22 devices, firmware spanos3/r202639/02)."""
    return _load("nt-2143-c1akc.json")


def _by_device_capability(specs: list[EntitySpec]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for s in specs:
        counts[(s.device_id, s.capability)] += 1
    return counts


def _find(specs: list[EntitySpec], device_id: str, capability: str, property_id: str) -> EntitySpec:
    for s in specs:
        if s.device_id == device_id and s.capability == capability and s.property_id == property_id:
            return s
    raise AssertionError(f"no spec for ({device_id}, {capability}, {property_id})")


def _circuit_ids(devs: dict[str, Any]) -> set[str]:
    return {
        did for did, d in devs.items()
        if d["description"].get("type") == "energy.ebus.device.circuit"
    }


# ── helpers ────────────────────────────────────────────────────────────────


def test_device_type_short_strips_prefix() -> None:
    assert device_type_short("energy.ebus.device.distribution-enclosure") == "distribution-enclosure"
    assert device_type_short("energy.ebus.device.bess") == "bess"


def test_device_type_short_rejects_non_ebus() -> None:
    assert device_type_short("homie.device.thermostat") is None
    assert device_type_short("") is None


def test_parse_enum_format() -> None:
    assert _parse_enum_format("A, B ,C") == ["A", "B", "C"]
    assert _parse_enum_format("") == []


def test_lug_direction_from_property_then_suffix() -> None:
    assert _lug_direction({"properties": {"info/direction": "UPSTREAM"}}) == "upstream"
    assert _lug_direction({"properties": {}}, "p-lugs-dn") == "downstream"
    assert _lug_direction({"properties": {}}, "p-lugs-up") == "upstream"
    assert _lug_direction({"properties": {}}, "circuit-xyz") == ""


# ── end-to-end totals ────────────────────────────────────────────────────────


def test_total_specs_and_platform_breakdown(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    assert len(specs) == 375
    plat = Counter(s.platform for s in specs)
    assert plat[Platform.SENSOR] == 281
    assert plat[Platform.BINARY_SENSOR] == 61
    assert plat[Platform.SELECT] == 17  # 16 circuit shed-priority + 1 root asserted-islanding-state
    assert plat[Platform.SWITCH] == 16  # 16 circuit relays
    # Every emitted device is one of the six known device classes.
    assert {s.device_id for s in specs} == {ROOT, LUGS_UP, LUGS_DN, BESS, MID, PV} | _circuit_ids(lc1)


def test_main32_unique_id_set_is_unchanged(lc1: dict[str, Any]) -> None:
    # Snapshot of the MAIN 32 fixture's unique-id set, taken before the rows
    # for SPAN Panel models other than MAIN 32 were added. Every MAIN 32 entity
    # stays enabled by default.
    specs = entities_from_tree(lc1)
    ids = sorted(make_unique_id(SERIAL, s.device_id, s.capability, s.property_id) for s in specs)
    assert len(ids) == len(set(ids)) == 375
    assert hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16] == "828778695a1cbbc6"
    assert all(s.enabled_default for s in specs)


def test_enclosure_capability_counts(lc1: dict[str, Any]) -> None:
    counts = _by_device_capability(entities_from_tree(lc1))
    assert counts[(ROOT, "info")] == 6
    assert counts[(ROOT, "door")] == 1
    assert counts[(ROOT, "meter")] == 2
    assert counts[(ROOT, "breaker")] == 1
    assert counts[(ROOT, "status")] == 7
    assert counts[(ROOT, "pcs")] == 16
    assert counts[(ROOT, "power-flows")] == 4
    assert counts[(ROOT, "shed-forecast")] == 5
    assert counts[(ROOT, "shed")] == 2


def test_bess_mid_pv_counts(lc1: dict[str, Any]) -> None:
    counts = _by_device_capability(entities_from_tree(lc1))
    assert counts[(BESS, "info")] == 6
    assert counts[(BESS, "soc")] == 2
    assert counts[(BESS, "meter")] == 1
    assert counts[(BESS, "status")] == 1
    assert counts[(MID, "info")] == 5
    assert counts[(MID, "grid")] == 3
    assert counts[(PV, "info")] == 5


def test_every_circuit_has_the_full_capability_surface(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    counts = _by_device_capability(specs)
    circuits = _circuit_ids(lc1)
    assert len(circuits) == 16
    for cid in circuits:
        assert counts[(cid, "info")] == 2       # name, spaces
        assert counts[(cid, "breaker")] == 2    # rating, poles
        assert counts[(cid, "meter")] == 4      # current, active-power, imported/exported energy
        assert counts[(cid, "load-shed")] == 1  # priority (select)
        assert counts[(cid, "pcs")] == 2        # managed, priority
        assert counts[(cid, "switch")] == 3     # relay, relay-controllable, relay-requester
        assert counts[(cid, "connection")] == 4 # feeds-* triplet + count


# ── new-vocabulary spot checks ───────────────────────────────────────────────


def test_circuit_load_shed_is_a_writable_select_with_options_from_format(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    cid = LOAD_CIRCUIT
    spec = _find(specs, cid, "load-shed", "priority")
    assert spec.platform == Platform.SELECT
    assert spec.name == "Shed Priority"
    assert spec.settable is True  # from the live $settable
    assert spec.options == ["UNKNOWN", "OFF_GRID", "SOC_THRESHOLD", "NEVER"]  # from $format


def test_circuit_switch_relay_is_a_settable_switch(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    cid = LOAD_CIRCUIT
    relay = _find(specs, cid, "switch", "relay")
    assert relay.platform == Platform.SWITCH
    assert relay.settable is True
    # relay-controllable moved onto the switch node (was on the legacy priority node).
    ctrl = _find(specs, cid, "switch", "relay-controllable")
    assert ctrl.platform == Platform.BINARY_SENSOR


def test_pv_circuit_relay_and_shed_priority_are_read_only(lc1: dict[str, Any]) -> None:
    # The panel omits $settable on the PV circuit's relay and shed priority, so
    # the entities keep their platforms but are not writable.
    specs = entities_from_tree(lc1)
    assert lc1[PV_CIRCUIT]["properties"]["connection/feeds-device-id"] == PV
    relay = _find(specs, PV_CIRCUIT, "switch", "relay")
    assert relay.platform == Platform.SWITCH
    assert relay.settable is False
    priority = _find(specs, PV_CIRCUIT, "load-shed", "priority")
    assert priority.platform == Platform.SELECT
    assert priority.settable is False


def test_circuit_meter_power_keeps_negate_and_pv_sign_aware(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    for cid in _circuit_ids(lc1):
        power = _find(specs, cid, "meter", "active-power")
        assert power.device_class == SensorDeviceClass.POWER
        assert power.native_unit == "W"          # trusted from $unit, not hardcoded
        assert power.negate is True
        assert power.pv_sign_aware is True
    # Panel-perspective energy naming preserved.
    cid = LOAD_CIRCUIT
    assert _find(specs, cid, "meter", "exported-energy").name == "Energy"
    assert _find(specs, cid, "meter", "imported-energy").name == "Energy Returned"


def test_circuit_breaker_ratings(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    cid = LOAD_CIRCUIT
    rating = _find(specs, cid, "breaker", "rating")
    assert rating.device_class == SensorDeviceClass.CURRENT
    assert rating.native_unit == "A"
    assert _find(specs, cid, "breaker", "poles").name == "Breaker Poles"


def test_enclosure_shed_asserted_islanding_is_writable_select(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    islanding = _find(specs, ROOT, "shed", "asserted-islanding-state")
    assert islanding.platform == Platform.SELECT
    assert islanding.settable is True
    assert islanding.options == ["NONE", "ON_GRID", "OFF_GRID"]
    assert _find(specs, ROOT, "shed", "policy").platform == Platform.SENSOR  # json diagnostic


def test_enclosure_meter_voltages_and_main_breaker(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    va = _find(specs, ROOT, "meter", "voltage-a")
    assert va.name == "L1 Voltage"
    assert va.device_class == SensorDeviceClass.VOLTAGE
    assert va.native_unit == "V"
    main = _find(specs, ROOT, "breaker", "rating")
    assert main.name == "Main Breaker Rating"
    assert main.native_unit == "A"


def test_bess_meter_status_and_pv_nominal_power(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    power = _find(specs, BESS, "meter", "active-power")
    assert power.device_class == SensorDeviceClass.POWER and power.native_unit == "W"
    comm = _find(specs, BESS, "status", "communication-state")
    assert comm.platform == Platform.BINARY_SENSOR
    assert comm.device_class == BinarySensorDeviceClass.PROBLEM
    assert comm.on_values == {"LOST", "DEGRADED"}
    # BESS identity gained part-number; capacity stays kWh energy-storage.
    assert _find(specs, BESS, "info", "part-number").name == "Part Number"
    cap = _find(specs, BESS, "info", "nameplate-capacity")
    assert cap.device_class == SensorDeviceClass.ENERGY_STORAGE and cap.native_unit == "kWh"
    # PV rating moved to nominal-power (W).
    nom = _find(specs, PV, "info", "nominal-power")
    assert nom.device_class == SensorDeviceClass.POWER and nom.native_unit == "W"


# ── lug direction gating + naming ────────────────────────────────────────────


def test_upstream_lug_emits_fed_by_and_grid_energy_names(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    conn = {s.property_id for s in specs if s.device_id == LUGS_UP and s.capability == "connection"}
    assert conn == {"count", "fed-by-device-id", "fed-by-device-type", "fed-by-device-status"}
    # Friendly Energy-Dashboard names on the upstream lug.
    assert _find(specs, LUGS_UP, "meter", "imported-energy").name == "Energy"
    assert _find(specs, LUGS_UP, "meter", "exported-energy").name == "Energy Returned"


def test_downstream_lug_emits_feeds_and_literal_energy_names(lc1: dict[str, Any]) -> None:
    specs = entities_from_tree(lc1)
    conn = {s.property_id for s in specs if s.device_id == LUGS_DN and s.capability == "connection"}
    assert conn == {"count", "feeds-device-id", "feeds-device-type", "feeds-device-status"}
    assert _find(specs, LUGS_DN, "meter", "imported-energy").name == "Imported Energy"
    assert _find(specs, LUGS_DN, "meter", "exported-energy").name == "Exported Energy"


# ── SPAN Panel models other than MAIN 32 (synthetic fixture) ─────────────────

SYN_SERIAL = "zz-0000-synth"
SYN_ROOT = SYN_SERIAL
SYN_LUGS_UP = f"{SYN_SERIAL}-lugs-up"
SYN_EVSE = f"{SYN_SERIAL}-evse"
SYN_CIRCUIT = "zz-circ-a"
SYN_METER_ONLY = "zz-meter-a"

_DIAG = EntityCategory.DIAGNOSTIC

# (device, capability, property) -> (platform, name, entity_category, enabled_default)
_OTHER_MODEL_ROWS: dict[tuple[str, str, str], tuple[Platform, str, EntityCategory | None, bool]] = {
    (SYN_ROOT, "info", "name"): (Platform.SENSOR, "Site Name", _DIAG, True),
    (SYN_ROOT, "info", "address-lines"): (Platform.SENSOR, "Address", _DIAG, True),
    (SYN_ROOT, "info", "locality"): (Platform.SENSOR, "Locality", _DIAG, True),
    (SYN_ROOT, "info", "region"): (Platform.SENSOR, "Region", _DIAG, True),
    (SYN_ROOT, "info", "country-code"): (Platform.SENSOR, "Country Code", _DIAG, True),
    (SYN_ROOT, "info", "latitude"): (Platform.SENSOR, "Latitude", _DIAG, True),
    (SYN_ROOT, "info", "longitude"): (Platform.SENSOR, "Longitude", _DIAG, True),
    (SYN_ROOT, "info", "utility-meter-serial-number"): (
        Platform.SENSOR, "Utility Meter Serial Number", _DIAG, True
    ),
    (SYN_ROOT, "meter", "busbar-current"): (Platform.SENSOR, "Busbar Current", None, True),
    (SYN_ROOT, "meter", "frequency"): (Platform.SENSOR, "Frequency", None, True),
    (SYN_CIRCUIT, "info", "tags"): (Platform.SENSOR, "Tags", _DIAG, True),
    (SYN_CIRCUIT, "info", "locations"): (Platform.SENSOR, "Locations", _DIAG, True),
    (SYN_CIRCUIT, "info", "dedicated"): (Platform.BINARY_SENSOR, "Dedicated", _DIAG, True),
    (SYN_CIRCUIT, "info", "nominal-voltage"): (Platform.SENSOR, "Nominal Voltage", _DIAG, True),
    (SYN_CIRCUIT, "breaker", "protection-functions"): (Platform.SENSOR, "Breaker Protection", _DIAG, True),
    (SYN_CIRCUIT, "meter", "shared-with-device-ids"): (Platform.SENSOR, "Meter Shared With", _DIAG, False),
    (SYN_CIRCUIT, "switch", "shared-with-device-ids"): (Platform.SENSOR, "Relay Shared With", _DIAG, False),
    (SYN_CIRCUIT, "connection", "fed-by-device-id"): (Platform.SENSOR, "Fed By Device", _DIAG, True),
    (SYN_CIRCUIT, "connection", "fed-by-device-type"): (Platform.SENSOR, "Fed By Device Type", _DIAG, True),
    (SYN_CIRCUIT, "connection", "fed-by-device-status"): (
        Platform.BINARY_SENSOR, "Fed By Connection Problem", _DIAG, True
    ),
    (SYN_CIRCUIT, "connection", "backed-up"): (Platform.SENSOR, "Backed Up", _DIAG, True),
    (SYN_CIRCUIT, "connection", "feeds-role"): (Platform.SENSOR, "Feeds Role", _DIAG, True),
    (SYN_LUGS_UP, "connection", "backed-up"): (Platform.SENSOR, "Backed Up", _DIAG, True),
    (SYN_LUGS_UP, "connection", "feeds-role"): (Platform.SENSOR, "Feeds Role", _DIAG, True),
    (SYN_LUGS_UP, "connection", "service-rating"): (Platform.SENSOR, "Service Rating", _DIAG, True),
    (SYN_LUGS_UP, "connection", "overcurrent-protection"): (
        Platform.SENSOR, "Overcurrent Protection Rating", _DIAG, True
    ),
}


@pytest.fixture
def other_model() -> dict[str, Any]:
    """Load the synthetic tree for a SPAN Panel model other than MAIN 32."""
    return _load("synthetic-other-model.json")


def test_other_model_properties_are_all_mapped(other_model: dict[str, Any]) -> None:
    specs = entities_from_tree(other_model)
    assert len(_OTHER_MODEL_ROWS) == 26
    for (device_id, capability, property_id), (platform, name, category, enabled) in _OTHER_MODEL_ROWS.items():
        spec = _find(specs, device_id, capability, property_id)
        assert spec.platform == platform, (device_id, capability, property_id)
        assert spec.name == name, (device_id, capability, property_id)
        assert spec.entity_category == category, (device_id, capability, property_id)
        assert spec.enabled_default is enabled, (device_id, capability, property_id)
        assert spec.settable is False, (device_id, capability, property_id)


def test_other_model_measurements_and_ratings(other_model: dict[str, Any]) -> None:
    specs = entities_from_tree(other_model)
    busbar = _find(specs, SYN_ROOT, "meter", "busbar-current")
    assert busbar.device_class == SensorDeviceClass.CURRENT
    assert busbar.state_class == SensorStateClass.MEASUREMENT
    assert busbar.native_unit == "A"
    freq = _find(specs, SYN_ROOT, "meter", "frequency")
    assert freq.device_class == SensorDeviceClass.FREQUENCY
    assert freq.state_class == SensorStateClass.MEASUREMENT
    assert freq.native_unit == "Hz"
    voltage = _find(specs, SYN_CIRCUIT, "info", "nominal-voltage")
    assert voltage.device_class == SensorDeviceClass.VOLTAGE and voltage.native_unit == "V"
    for prop in ("service-rating", "overcurrent-protection"):
        rating = _find(specs, SYN_LUGS_UP, "connection", prop)
        assert rating.device_class == SensorDeviceClass.CURRENT and rating.native_unit == "A"
    fed_by = _find(specs, SYN_CIRCUIT, "connection", "fed-by-device-status")
    assert fed_by.device_class == BinarySensorDeviceClass.PROBLEM
    assert fed_by.on_values == {"LOST", "DEGRADED"}


def test_other_model_upstream_lug_connection_rows_are_not_direction_gated(
    other_model: dict[str, Any],
) -> None:
    specs = entities_from_tree(other_model)
    conn = {s.property_id for s in specs if s.device_id == SYN_LUGS_UP and s.capability == "connection"}
    assert conn == {
        "count",
        "fed-by-device-id",
        "fed-by-device-type",
        "fed-by-device-status",
        "backed-up",
        "feeds-role",
        "service-rating",
        "overcurrent-protection",
    }
    # Same rows on a downstream lug.
    dn = {
        "description": other_model[SYN_LUGS_UP]["description"],
        "properties": {"info/direction": "DOWNSTREAM"},
    }
    dn_conn = {s.property_id for s in entities_from_tree({"lugs": dn}) if s.capability == "connection"}
    assert {"backed-up", "feeds-role", "service-rating", "overcurrent-protection"} <= dn_conn
    assert "fed-by-device-id" not in dn_conn


def test_other_model_omits_absent_capabilities(other_model: dict[str, Any]) -> None:
    specs = entities_from_tree(other_model)
    keys = {(s.device_id, s.capability, s.property_id) for s in specs}
    assert not any(d == SYN_ROOT and c == "breaker" for d, c, _ in keys)
    assert not any(d == SYN_EVSE and c == "meter" for d, c, _ in keys)
    assert (SYN_EVSE, "switch", "lock-state") in keys


def test_other_model_meter_only_circuit_maps_its_meter(other_model: dict[str, Any]) -> None:
    specs = [s for s in entities_from_tree(other_model) if s.device_id == SYN_METER_ONLY]
    assert {(s.capability, s.property_id) for s in specs} == {
        ("meter", "current"),
        ("meter", "active-power"),
        ("meter", "imported-energy"),
        ("meter", "exported-energy"),
        ("meter", "shared-with-device-ids"),
    }
    power = _find(specs, SYN_METER_ONLY, "meter", "active-power")
    assert power.negate is False and power.pv_sign_aware is False
    assert _find(specs, SYN_METER_ONLY, "meter", "imported-energy").name == "Energy"
    assert _find(specs, SYN_METER_ONLY, "meter", "exported-energy").name == "Energy Returned"
    assert _find(specs, SYN_METER_ONLY, "meter", "shared-with-device-ids").enabled_default is False


def test_other_model_disabled_rows_are_only_shared_with(other_model: dict[str, Any]) -> None:
    disabled = {(s.capability, s.property_id) for s in entities_from_tree(other_model) if not s.enabled_default}
    assert disabled == {("meter", "shared-with-device-ids"), ("switch", "shared-with-device-ids")}


# ── _build_spec directives ───────────────────────────────────────────────────


def test_build_spec_direction_gate_skips_mismatched_lug() -> None:
    row = {"platform": Platform.SENSOR, "name": "Fed By Device", "direction": "upstream"}
    assert _build_spec("d", "connection", "fed-by-device-id", {}, row, "downstream") is None
    kept = _build_spec("d", "connection", "fed-by-device-id", {}, row, "upstream")
    assert kept is not None and kept.name == "Fed By Device"


def test_build_spec_applies_name_upstream_override() -> None:
    row = {
        "platform": Platform.SENSOR,
        "name": "Exported Energy",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "name_upstream": "Energy Returned",
    }
    up = _build_spec("d", "meter", "exported-energy", {}, row, "upstream")
    dn = _build_spec("d", "meter", "exported-energy", {}, row, "downstream")
    assert up is not None and up.name == "Energy Returned"
    assert dn is not None and dn.name == "Exported Energy"


def test_build_spec_reads_structure_from_description() -> None:
    row = {"platform": Platform.SELECT, "name": "Shed Priority"}
    decl = {"datatype": "enum", "format": "A,B,C", "settable": True, "unit": None}
    spec = _build_spec("d", "load-shed", "priority", decl, row, "")
    assert spec is not None
    assert spec.options == ["A", "B", "C"]
    assert spec.settable is True

    row2 = {"platform": Platform.SENSOR, "name": "Power", "device_class": SensorDeviceClass.POWER}
    spec2 = _build_spec("d", "meter", "active-power", {"unit": "W"}, row2, "")
    assert spec2 is not None and spec2.native_unit == "W"


# ── edge cases ───────────────────────────────────────────────────────────────


def test_empty_tree_returns_empty_list() -> None:
    assert entities_from_tree({}) == []


def test_non_ebus_device_is_skipped() -> None:
    devs = {"x": {"description": {"type": "homie.device.thermostat", "nodes": {"t": {"properties": {"setpoint": {}}}}}}}
    assert entities_from_tree(devs) == []


def test_unknown_capability_or_device_class_is_skipped() -> None:
    # A non-SPAN publisher on the broker (e.g. a SkyCentrics water-heater with a
    # flex capability) has no SEMANTICS entries and emits nothing.
    devs = {
        "wh": {
            "description": {
                "type": "energy.ebus.device.water-heater",
                "nodes": {"flex": {"properties": {"request": {"datatype": "json", "settable": True}}}},
            }
        },
        "panel": {
            "description": {
                "type": "energy.ebus.device.circuit",
                "nodes": {"unknown-cap": {"properties": {"whatever": {}}}},
            }
        },
    }
    assert entities_from_tree(devs) == []
