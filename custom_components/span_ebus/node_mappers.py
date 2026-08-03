"""Map a Homie 5 parent/child device tree to Home Assistant entity descriptors.

The dispatcher walks a tree of Homie devices (the shape ``ebus_sdk.Controller``
exposes in tree-rooted mode, and the shape the tree fixtures under
``tests/fixtures/tree/`` carry). For each capability property a device's
``$description`` declares, it looks up the Home Assistant presentation in the
declarative ``SEMANTICS`` table (``semantics.py``) and reads the property's
structure (datatype, unit, enum ``$format``, ``$settable``) from the live
description, then emits an ``EntitySpec``.

Entity STRUCTURE always comes from the live ``$description`` (the authoritative
source for what a given firmware actually publishes); SEMANTICS carries only the
Home Assistant presentation (platform, name, device/state class, naming, sign
convention). A property present on the wire but absent from SEMANTICS is skipped
(logged at DEBUG); ``tests/test_semantics_coverage.py`` guarantees SEMANTICS
covers both the live-wire fixtures and the vendored spec catalogs, so a
spec/adapter drift surfaces as a loud test failure rather than a silently
dropped entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory, Platform

from .const import DEVICE_TYPE_LUGS, HOMIE_DEVICE_TYPE_PREFIX
from .semantics import SEMANTICS

_LOGGER = logging.getLogger(__name__)


# ── EntitySpec ────────────────────────────────────────────────────────────


@dataclass
class EntitySpec:
    """Descriptor for an entity to be created from a Homie capability property.

    The (device_id, capability, property_id) triplet locates the property
    uniquely on the wire and forms the basis of the entity's HA ``unique_id``.
    """

    # Tree position — locates the property on the wire.
    device_id: str
    capability: str
    property_id: str

    # HA entity descriptor.
    platform: Platform
    name: str
    device_class: Any | None = None
    state_class: SensorStateClass | None = None
    native_unit: str | None = None
    entity_category: EntityCategory | None = None
    options: list[str] = field(default_factory=list)
    icon: str | None = None
    settable: bool = False
    on_values: set[str] = field(default_factory=set)

    # Subscribe to a different property than property_id for MQTT updates.
    # When set, unique_id uses property_id but the MQTT callback registers on
    # source_property_id. Empty string = "subscribe to property_id itself".
    source_property_id: str = ""

    # Value transform: negate numeric values (used for circuit active-power so
    # consumption is positive in HA, matching the Energy Dashboard convention).
    negate: bool = False

    # When True, the sensor suppresses ``negate`` at runtime if the owning
    # circuit feeds a PV device (``connection/feeds-device-type == pv``). PV-feed
    # circuits already report generation as positive, so the load sign-flip must
    # not apply. Decided at runtime, not build time, because feeds-device-type
    # (a retained sibling property) may not have landed when entities are built.
    pv_sign_aware: bool = False

    # HA DeviceInfo construction. device_type is the short device-class name
    # (one of DEVICE_TYPE_* constants in const.py); via_device_id is the
    # parent's Homie device-id for HA's via_device link (empty for root).
    device_type: str = ""
    device_name: str = ""
    via_device_id: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────


def device_type_short(homie_type: str) -> str | None:
    """Extract the short device-class name from a Homie device-type URI.

    Returns ``"distribution-enclosure"`` for
    ``"energy.ebus.device.distribution-enclosure"``; returns None for URIs that
    don't carry the eBus prefix (the caller should skip those).
    """
    if not homie_type.startswith(HOMIE_DEVICE_TYPE_PREFIX):
        return None
    return homie_type[len(HOMIE_DEVICE_TYPE_PREFIX):]


def _parse_enum_format(fmt: str) -> list[str]:
    """Split a Homie enum $format string ('A,B,C') into a clean list."""
    if not fmt:
        return []
    return [v.strip() for v in fmt.split(",") if v.strip()]


def _lug_direction(device_data: dict[str, Any], device_id: str = "") -> str:
    """Return the lug's direction ('upstream'/'downstream'/'').

    Prefers the runtime ``info/direction`` property when it's been delivered
    (publisher's authoritative source). Falls back to parsing the device-id
    suffix (``-lugs-up`` / ``-lugs-dn``) when the property hasn't been observed
    yet: property values arrive asynchronously after the description, and the
    initial ``entities_from_tree`` walk often runs before ``info/direction`` has
    landed. Without the fallback the lugs ``connection`` mapper would silently
    emit zero entities on the first setup pass, and the meter mapper couldn't
    pick the user-friendly energy names ("Energy" / "Energy Returned" upstream).

    Returns the empty string only when neither source resolves the direction.
    """
    raw = device_data.get("properties", {}).get("info/direction", "")
    if raw:
        return str(raw).lower()
    if device_id.endswith("-lugs-up"):
        return "upstream"
    if device_id.endswith("-lugs-dn"):
        return "downstream"
    return ""


# ── Description-driven builder ─────────────────────────────────────────────


def _build_spec(
    device_id: str,
    capability: str,
    property_id: str,
    decl: dict[str, Any],
    row: dict[str, Any],
    lug_direction: str,
) -> EntitySpec | None:
    """Build one EntitySpec from a SEMANTICS row + the live property declaration.

    ``row`` supplies the HA presentation; ``decl`` (the property's entry in the
    Homie ``$description``) supplies structure: ``native_unit`` from ``$unit``,
    select ``options`` from ``$format``, and ``settable`` from ``$settable``.

    Two builder-only directives may appear in a row and are consumed here:
    ``direction`` (emit only when the owning lug matches that direction) and
    ``name_upstream`` (a name override used on the upstream lug).
    """
    row = dict(row)
    direction = row.pop("direction", None)
    name_upstream = row.pop("name_upstream", None)

    if direction is not None and lug_direction != direction:
        return None
    if lug_direction == "upstream" and name_upstream is not None:
        row["name"] = name_upstream

    unit = decl.get("unit")
    if unit is not None:
        row["native_unit"] = unit
    if row.get("platform") == Platform.SELECT:
        row["options"] = _parse_enum_format(decl.get("format") or "")
    settable = decl.get("settable")
    if settable is not None:
        row["settable"] = bool(settable)

    return EntitySpec(
        device_id=device_id,
        capability=capability,
        property_id=property_id,
        **row,
    )


def entities_from_tree(devices: dict[str, dict[str, Any]]) -> list[EntitySpec]:
    """Walk a tree of Homie devices and emit EntitySpecs for every known property.

    Input shape mirrors what ``ebus_sdk.Controller`` produces in tree-rooted mode
    (and what the tree fixture JSONs under ``tests/fixtures/tree/`` carry under
    the ``devices`` key): a mapping ``device_id -> {description, properties,
    root_id, parent_id, children_ids, is_root}``.

    Every property a device's ``$description`` declares is looked up in SEMANTICS
    by ``(device_class, capability, property_id)``. Properties with no SEMANTICS
    entry are logged at DEBUG and skipped (forward-compatible with spec
    additions; the coverage test guards against silent drops of known ones).
    """
    specs: list[EntitySpec] = []
    for device_id, device_data in devices.items():
        description = device_data.get("description") or {}
        device_type = description.get("type", "")
        device_class = device_type_short(device_type)
        if device_class is None:
            _LOGGER.debug(
                "device %s has non-eBus type %r; skipping", device_id, device_type
            )
            continue

        lug_direction = (
            _lug_direction(device_data, device_id)
            if device_class == DEVICE_TYPE_LUGS
            else ""
        )

        for capability, node_desc in (description.get("nodes") or {}).items():
            properties = (node_desc or {}).get("properties") or {}
            for property_id, decl in properties.items():
                row = SEMANTICS.get((device_class, capability, property_id))
                if row is None:
                    _LOGGER.debug(
                        "no semantics for (%s, %s, %s) on device %s",
                        device_class,
                        capability,
                        property_id,
                        device_id,
                    )
                    continue
                spec = _build_spec(
                    device_id, capability, property_id, decl or {}, row, lug_direction
                )
                if spec is not None:
                    specs.append(spec)

    return specs
