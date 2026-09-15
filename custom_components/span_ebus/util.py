"""Utility helpers for SPAN Panel (eBus) integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DEVICE_TYPE_BESS,
    DEVICE_TYPE_CIRCUIT,
    DEVICE_TYPE_DISTRIBUTION_ENCLOSURE,
    DEVICE_TYPE_EVSE,
    DEVICE_TYPE_LUGS,
    DEVICE_TYPE_MID,
    DEVICE_TYPE_PV,
    DOMAIN,
)

# Human-readable model labels per short device-class name (the trailing
# segment of the Homie device type URI; the same short names used in the
# SEMANTICS keys in semantics.py).
DEVICE_TYPE_LABELS = {
    DEVICE_TYPE_DISTRIBUTION_ENCLOSURE: "SPAN Panel",
    DEVICE_TYPE_LUGS: "Lugs",
    DEVICE_TYPE_BESS: "Battery Storage",
    DEVICE_TYPE_MID: "Microgrid Interconnect Device",
    DEVICE_TYPE_PV: "Solar PV",
    DEVICE_TYPE_EVSE: "EV Charger",
    DEVICE_TYPE_CIRCUIT: "Circuit",
}


def panel_device_info(serial_number: str, firmware_version: str = "") -> DeviceInfo:
    """Build a DeviceInfo for the panel-root HA device.

    Carries no parent link. Home Assistant removed ``via_device`` from
    ``DeviceInfo`` in 2026.9 in favor of ``via_device_id``, which is a device
    registry id rather than an identifier tuple and so cannot be known by a
    pure builder. The cascade hierarchy is therefore set on the device row
    itself, by ``__init__._link_parents`` after every device exists.
    """
    info = DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        manufacturer="SPAN",
        model="SPAN Panel",
        name=f"SPAN Panel {serial_number}",
    )
    if firmware_version:
        info["sw_version"] = firmware_version
    return info


def descendant_device_info(
    panel_serial: str,
    device_id: str,
    device_type: str,
    device_name: str,
    manufacturer: str | None = None,
) -> DeviceInfo:
    """Build a DeviceInfo for a non-root descendant device.

    Carries no parent link, for the reason given on ``panel_device_info``; the
    descendant's place under its Homie ``$parent`` is established by
    ``__init__._link_parents``, which resolves ``parent_identifier`` to a
    registry id once every device exists.

    Identifiers always include the panel serial as a prefix to keep IDs
    globally unique across multi-panel installs (matches the unique_id format).
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"{panel_serial}_{device_id}")},
        name=device_name,
        manufacturer=manufacturer or "SPAN",
        model=DEVICE_TYPE_LABELS.get(device_type, "Unknown"),
    )


def parent_identifier(
    panel_serial: str, parent_device_id: str | None
) -> tuple[str, str]:
    """Return the identifier of the device a descendant hangs under.

    A descendant whose Homie ``$parent`` is the panel root (or which names no
    parent) hangs directly off the panel; a MID grandchild hangs off its BESS.
    """
    if parent_device_id and parent_device_id != panel_serial:
        return (DOMAIN, f"{panel_serial}_{parent_device_id}")
    return (DOMAIN, panel_serial)


def make_unique_id(
    panel_serial: str, device_id: str, capability: str, property_id: str
) -> str:
    """Build an HA unique_id from the tree-position triplet.

    Format: ``{panel-serial}_{device-id}_{capability}_{property-id}``. The
    panel-serial prefix is intentional even when device_id already contains
    it — circuits publish under bare UUIDs (no panel-serial prefix in the
    Homie device-id), so the panel-serial here is the only thing keeping
    those entity IDs unique across multiple SPAN integrations on one HA
    install.
    """
    return f"{panel_serial}_{device_id}_{capability}_{property_id}"
