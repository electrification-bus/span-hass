"""Declarative Home Assistant semantics for eBus capability properties.

This is the *presentation* layer of the description-driven mapper. It answers
"given a capability property, what kind of Home Assistant entity is it, and what
device_class / state_class / naming / sign convention does it carry?" It does
NOT carry structure (datatype, unit, enum options, settable): those are read at
runtime from the live Homie ``$description`` the panel publishes, which is the
authoritative source for what a given firmware actually emits.

``SEMANTICS`` is keyed by ``(device_class, capability, property_id)`` — the
device-class dimension matters because the same capability differs by host
(circuit ``pcs`` = managed/priority vs distribution-enclosure ``pcs`` = the
import-limit families; enclosure ``meter`` = voltages vs lugs ``meter`` =
per-leg currents + power + energy).

``tests/test_semantics_coverage.py`` keeps this table honest against the
adapter's own generated schema (``GET /api/v2/homie/schema``, vendored as
``custom_components/span_ebus/adapter_schema.json``): every device class / capability /
property the ebus-panel-adapter can publish must be mapped here, so a renamed or
added property surfaces as a loud test failure rather than a silently dropped
entity. The adapter schema, not the upstream spec (aspirational) nor only the
live wire (only instantiated devices), is the coverage oracle: it is what
"track the adapter first" means, and it carries device classes the reference
panels lack (EVSE) and forward-declared capabilities (``doe``). The vendored
public spec catalogs under ``spec/`` anchor the pinned version (``.ebus-spec.json``)
and are the units/format reference.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, Platform

# Type of a SEMANTICS row: kwargs merged into an EntitySpec, plus a few
# builder-only directives (name_upstream, direction) stripped before construction.
Row = dict[str, Any]


# ── Row constructors (reduce boilerplate, keep the table readable) ──────────


def _diag(name: str, **extra: Any) -> Row:
    """Build a diagnostic text-sensor row (enum/string value shown verbatim)."""
    return {
        "platform": Platform.SENSOR,
        "name": name,
        "entity_category": EntityCategory.DIAGNOSTIC,
        **extra,
    }


def _measure(name: str, device_class: SensorDeviceClass, **extra: Any) -> Row:
    """Build an instantaneous measurement-sensor row (unit from the description)."""
    return {
        "platform": Platform.SENSOR,
        "name": name,
        "device_class": device_class,
        "state_class": SensorStateClass.MEASUREMENT,
        **extra,
    }


def _total(name: str, device_class: SensorDeviceClass, **extra: Any) -> Row:
    """Build a cumulative counter-sensor row (TOTAL_INCREASING; unit from the description)."""
    return {
        "platform": Platform.SENSOR,
        "name": name,
        "device_class": device_class,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        **extra,
    }


def _binary(name: str, **extra: Any) -> Row:
    """Build a binary-sensor row (on_values decide truthiness; else the _TRUTHY fallback)."""
    return {"platform": Platform.BINARY_SENSOR, "name": name, **extra}


def _problem(name: str, on: set[str], **extra: Any) -> Row:
    """Build a PROBLEM binary-sensor row ('on' means a not-OK status value)."""
    return {
        "platform": Platform.BINARY_SENSOR,
        "name": name,
        "device_class": BinarySensorDeviceClass.PROBLEM,
        "on_values": on,
        "entity_category": EntityCategory.DIAGNOSTIC,
        **extra,
    }


def _switch(name: str, **extra: Any) -> Row:
    """Build a writable-switch row (settable-ness read from the description $settable)."""
    return {"platform": Platform.SWITCH, "name": name, **extra}


def _select(name: str, **extra: Any) -> Row:
    """Build a writable-select row (options + settable read from the description $format/$settable)."""
    return {"platform": Platform.SELECT, "name": name, **extra}


_NOT_OK = {"LOST", "DEGRADED"}


# ── The table ───────────────────────────────────────────────────────────────
#
# Keys are (device_class, capability, property_id). ``unit`` and enum ``options``
# and ``settable`` come from the live $description, not from here.

SEMANTICS: dict[tuple[str, str, str], Row] = {
    # ── distribution-enclosure (panel root) ──────────────────────────────────
    ("distribution-enclosure", "info", "vendor-name"): _diag("Vendor"),
    ("distribution-enclosure", "info", "model"): _diag("Model"),
    ("distribution-enclosure", "info", "serial-number"): _diag("Serial Number"),
    ("distribution-enclosure", "info", "hardware-version"): _diag("Hardware Version"),
    ("distribution-enclosure", "info", "firmware-version"): _diag("Firmware Version"),
    ("distribution-enclosure", "info", "software-version"): _diag("Firmware Version"),  # legacy alias
    ("distribution-enclosure", "info", "data-model-version"): _diag("eBus Data-Model Version"),
    ("distribution-enclosure", "info", "part-number"): _diag("Part Number"),
    ("distribution-enclosure", "door", "state"): _binary(
        "Door", device_class=BinarySensorDeviceClass.TAMPER, on_values={"OPEN"}
    ),
    ("distribution-enclosure", "door", "door"): _binary(  # legacy alias
        "Door", device_class=BinarySensorDeviceClass.TAMPER, on_values={"OPEN"}
    ),
    ("distribution-enclosure", "meter", "voltage-a"): _measure("L1 Voltage", SensorDeviceClass.VOLTAGE),
    ("distribution-enclosure", "meter", "voltage-b"): _measure("L2 Voltage", SensorDeviceClass.VOLTAGE),
    ("distribution-enclosure", "breaker", "rating"): _diag(
        "Main Breaker Rating", device_class=SensorDeviceClass.CURRENT
    ),
    ("distribution-enclosure", "power-flows", "pv"): _measure("PV Power", SensorDeviceClass.POWER),
    ("distribution-enclosure", "power-flows", "battery"): _measure("Battery Power", SensorDeviceClass.POWER),
    ("distribution-enclosure", "power-flows", "grid"): _measure("Grid Power", SensorDeviceClass.POWER),
    ("distribution-enclosure", "power-flows", "site"): _measure("Site Power", SensorDeviceClass.POWER),
    ("distribution-enclosure", "shed-forecast", "total-time-remaining"): _measure(
        "Battery Time Remaining", SensorDeviceClass.DURATION
    ),
    ("distribution-enclosure", "shed-forecast", "time-to-priority-shed"): _measure(
        "Time to Priority Shed", SensorDeviceClass.DURATION
    ),
    ("distribution-enclosure", "shed-forecast", "full-charge-total-time-remaining"): _measure(
        "Battery Time Remaining at Full Charge", SensorDeviceClass.DURATION
    ),
    ("distribution-enclosure", "shed-forecast", "full-charge-time-to-priority-shed"): _measure(
        "Time to Priority Shed at Full Charge", SensorDeviceClass.DURATION
    ),
    ("distribution-enclosure", "shed-forecast", "confidence"): _diag("Shed Forecast Confidence"),
    ("distribution-enclosure", "shed", "asserted-islanding-state"): _select(
        "Asserted Islanding State", icon="mdi:transmission-tower-off"
    ),
    ("distribution-enclosure", "shed", "policy"): _diag("Shed Policy", icon="mdi:cog"),
    ("distribution-enclosure", "status", "relay"): _binary(
        "Main Relay", on_values={"CLOSED"}, icon="mdi:electric-switch"
    ),
    ("distribution-enclosure", "status", "ethernet"): _binary(
        "Ethernet",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ("distribution-enclosure", "status", "wifi"): _binary(
        "Wi-Fi",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ("distribution-enclosure", "status", "wifi-ssid"): _diag("Wi-Fi SSID"),
    ("distribution-enclosure", "status", "cloud-connection"): _diag("Cloud Connection"),
    ("distribution-enclosure", "status", "vendor-cloud"): _diag("Cloud Connection"),  # legacy alias
    ("distribution-enclosure", "status", "postal-code"): _diag("Postal Code"),
    ("distribution-enclosure", "status", "time-zone"): _diag("Time Zone"),

    # ── lugs (upstream + downstream) ─────────────────────────────────────────
    ("lugs", "info", "direction"): _diag("Direction"),
    ("lugs", "meter", "current-a"): _measure("L1 Current", SensorDeviceClass.CURRENT),
    ("lugs", "meter", "current-b"): _measure("L2 Current", SensorDeviceClass.CURRENT),
    ("lugs", "meter", "active-power"): _measure("Power", SensorDeviceClass.POWER),
    ("lugs", "meter", "imported-energy"): _total(
        "Imported Energy", SensorDeviceClass.ENERGY, name_upstream="Energy"
    ),
    ("lugs", "meter", "exported-energy"): _total(
        "Exported Energy", SensorDeviceClass.ENERGY, name_upstream="Energy Returned"
    ),
    ("lugs", "connection", "count"): _diag("Connection Count"),
    ("lugs", "connection", "fed-by-device-id"): _diag("Fed By Device", direction="upstream"),
    ("lugs", "connection", "fed-by-device-type"): _diag("Fed By Device Type", direction="upstream"),
    ("lugs", "connection", "fed-by-device-status"): _problem(
        "Upstream Connection Problem", _NOT_OK, direction="upstream"
    ),
    ("lugs", "connection", "feeds-device-id"): _diag("Feeds Device", direction="downstream"),
    ("lugs", "connection", "feeds-device-type"): _diag("Feeds Device Type", direction="downstream"),
    ("lugs", "connection", "feeds-device-status"): _problem(
        "Downstream Connection Problem", _NOT_OK, direction="downstream"
    ),

    # ── bess ─────────────────────────────────────────────────────────────────
    ("bess", "info", "vendor-name"): _diag("Vendor"),
    ("bess", "info", "model"): _diag("Model"),
    ("bess", "info", "part-number"): _diag("Part Number"),
    ("bess", "info", "serial-number"): _diag("Serial Number"),
    ("bess", "info", "hardware-version"): _diag("Hardware Version"),
    ("bess", "info", "firmware-version"): _diag("Firmware Version"),
    ("bess", "info", "software-version"): _diag("Firmware Version"),  # legacy alias
    ("bess", "info", "nameplate-capacity"): _diag(
        "Nameplate Capacity", device_class=SensorDeviceClass.ENERGY_STORAGE
    ),
    ("bess", "meter", "active-power"): _measure("Power", SensorDeviceClass.POWER),
    ("bess", "soc", "soc"): _measure("State of Charge", SensorDeviceClass.BATTERY),
    ("bess", "soc", "soe"): _measure("State of Energy", SensorDeviceClass.ENERGY_STORAGE),
    ("bess", "status", "communication-state"): _problem("Communication Problem", _NOT_OK),

    # ── mid (grandchild under bess) ──────────────────────────────────────────
    ("mid", "info", "vendor-name"): _diag("Vendor"),
    ("mid", "info", "model"): _diag("Model"),
    ("mid", "info", "serial-number"): _diag("Serial Number"),
    ("mid", "info", "hardware-version"): _diag("Hardware Version"),
    ("mid", "info", "firmware-version"): _diag("Firmware Version"),
    ("mid", "info", "software-version"): _diag("Firmware Version"),  # legacy alias
    ("mid", "grid", "islanding-state"): _diag("Islanding State", icon="mdi:transmission-tower-export"),
    ("mid", "grid", "grid-state"): _diag("Grid State", icon="mdi:transmission-tower"),
    ("mid", "grid", "grid-forming-entity"): _diag("Grid Forming Entity"),

    # ── pv ───────────────────────────────────────────────────────────────────
    ("pv", "info", "vendor-name"): _diag("Vendor"),
    ("pv", "info", "model"): _diag("Model"),
    ("pv", "info", "serial-number"): _diag("Serial Number"),
    ("pv", "info", "firmware-version"): _diag("Firmware Version"),
    ("pv", "info", "software-version"): _diag("Firmware Version"),  # legacy alias
    ("pv", "info", "nominal-power"): _diag("Nominal Power", device_class=SensorDeviceClass.POWER),

    # ── circuit ──────────────────────────────────────────────────────────────
    ("circuit", "info", "name"): _diag("Name"),
    ("circuit", "info", "spaces"): _diag("Tabs"),
    ("circuit", "breaker", "rating"): _diag("Breaker Rating", device_class=SensorDeviceClass.CURRENT),
    ("circuit", "breaker", "poles"): _diag("Breaker Poles"),
    ("circuit", "meter", "current"): _measure("Current", SensorDeviceClass.CURRENT),
    ("circuit", "meter", "active-power"): _measure(
        "Power", SensorDeviceClass.POWER, negate=True, pv_sign_aware=True
    ),
    # SPAN panel-perspective naming: exported-energy = delivered TO the circuit
    # (= consumption, the dominant counter) = "Energy"; imported-energy = flow
    # back FROM the circuit (backfeed) = "Energy Returned".
    ("circuit", "meter", "exported-energy"): _total("Energy", SensorDeviceClass.ENERGY),
    ("circuit", "meter", "imported-energy"): _total("Energy Returned", SensorDeviceClass.ENERGY),
    ("circuit", "load-shed", "priority"): _select("Shed Priority", icon="mdi:priority-high"),
    ("circuit", "pcs", "managed"): _binary("PCS Managed", entity_category=EntityCategory.DIAGNOSTIC),
    ("circuit", "pcs", "priority"): _diag("PCS Priority"),
    ("circuit", "switch", "relay"): _switch("Relay", icon="mdi:electric-switch"),
    ("circuit", "switch", "relay-controllable"): _binary(
        "Relay Controllable", entity_category=EntityCategory.DIAGNOSTIC
    ),
    ("circuit", "switch", "relay-requester"): _diag("Relay Requester"),
    ("circuit", "connection", "feeds-device-id"): _diag("Feeds Device"),
    ("circuit", "connection", "feeds-device-type"): _diag("Feeds Device Type"),
    ("circuit", "connection", "feeds-device-status"): _problem("Feeds Connection Problem", _NOT_OK),
    ("circuit", "connection", "count"): _diag("Feeds Count"),
}


# distribution-enclosure ``pcs``: the aggregate ``import-limit`` ceiling (value
# only), four sub-limit families that each add an -enablement enum + an -active
# boolean, plus the enabled / active / binding-constraint master flags. Generated
# to avoid ~15 near-identical rows.
SEMANTICS[("distribution-enclosure", "pcs", "import-limit")] = _measure("Import Limit", SensorDeviceClass.CURRENT)
_PCS_LIMIT_FAMILIES = {
    "feed-import-limit": "Feed Import Limit",
    "operator-import-limit": "Operator Import Limit",
    "requested-import-limit": "Requested Import Limit",
    "off-grid-import-limit": "Off-Grid Import Limit",
}
for _lim, _label in _PCS_LIMIT_FAMILIES.items():
    SEMANTICS[("distribution-enclosure", "pcs", _lim)] = _measure(_label, SensorDeviceClass.CURRENT)
    SEMANTICS[("distribution-enclosure", "pcs", f"{_lim}-enablement")] = _diag(f"{_label} Enablement")
    SEMANTICS[("distribution-enclosure", "pcs", f"{_lim}-active")] = _binary(
        f"{_label} Active", entity_category=EntityCategory.DIAGNOSTIC
    )
SEMANTICS[("distribution-enclosure", "pcs", "enabled")] = _binary(
    "PCS Enabled", entity_category=EntityCategory.DIAGNOSTIC
)
SEMANTICS[("distribution-enclosure", "pcs", "active")] = _binary(
    "PCS Active", entity_category=EntityCategory.DIAGNOSTIC
)
SEMANTICS[("distribution-enclosure", "pcs", "binding-constraint")] = _diag("PCS Binding Constraint")

# distribution-enclosure ``doe`` (dynamic operating envelope): forward-declared
# by the adapter schema (not populated on today's panels); json, shown verbatim.
SEMANTICS[("distribution-enclosure", "doe", "import-limit")] = _diag(
    "Import DOE", icon="mdi:transmission-tower-import"
)
SEMANTICS[("distribution-enclosure", "doe", "export-limit")] = _diag(
    "Export DOE", icon="mdi:transmission-tower-export"
)

# ── evse ─────────────────────────────────────────────────────────────────────
# Published by the adapter but not instantiated on the reference panels (nobody
# owns one), so validated against the adapter schema rather than a live fixture.
SEMANTICS[("evse", "info", "vendor-name")] = _diag("Vendor")
SEMANTICS[("evse", "info", "model")] = _diag("Model")
SEMANTICS[("evse", "info", "part-number")] = _diag("Part Number")
SEMANTICS[("evse", "info", "serial-number")] = _diag("Serial Number")
SEMANTICS[("evse", "info", "firmware-version")] = _diag("Firmware Version")
# lock-state (UNLOCKED/LOCKED) and status (AVAILABLE/PREPARING/CHARGING/
# UNAVAILABLE) are read-only enums; shown verbatim so every state stays legible.
SEMANTICS[("evse", "switch", "lock-state")] = _diag("Lock State", icon="mdi:lock")
SEMANTICS[("evse", "status", "status")] = _diag("Status", icon="mdi:ev-station")
SEMANTICS[("evse", "meter", "advertised-current")] = _measure("Advertised Current", SensorDeviceClass.CURRENT)
# user-max-charge-current is settable in the adapter schema; surfaced read-only
# until a NUMBER platform is added (Platform.NUMBER is not yet in PLATFORMS).
SEMANTICS[("evse", "config", "user-max-charge-current")] = _diag(
    "User Max Charge Current", device_class=SensorDeviceClass.CURRENT
)
SEMANTICS[("evse", "config", "max-charge-current")] = _diag(
    "Max Charge Current", device_class=SensorDeviceClass.CURRENT
)
