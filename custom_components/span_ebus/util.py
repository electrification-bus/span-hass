"""Utility helpers for SPAN Panel (eBus) integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

# Node types that become child devices of the panel
_SUB_DEVICE_TYPES = {
    "energy.ebus.device.circuit",
    "energy.ebus.device.bess",
    "energy.ebus.device.pv",
    "energy.ebus.device.evse",
    "energy.ebus.device.power-flows",
}

# Human-readable model labels for sub-device types
_NODE_TYPE_LABELS = {
    "energy.ebus.device.circuit": "Circuit",
    "energy.ebus.device.bess": "Battery Storage",
    "energy.ebus.device.pv": "Solar PV",
    "energy.ebus.device.evse": "EV Charger",
    "energy.ebus.device.power-flows": "Site Metering",
}


def panel_device_info(serial_number: str, firmware_version: str = "") -> DeviceInfo:
    """Build a DeviceInfo for a SPAN Panel."""
    info = DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        manufacturer="SPAN",
        model="SPAN Panel",
        name=f"SPAN Panel {serial_number}",
    )
    if firmware_version:
        info["sw_version"] = firmware_version
    return info


def subdevice_info(
    serial: str, node_id: str, node_type: str, name: str
) -> DeviceInfo:
    """Build a DeviceInfo for a child device (circuit, BESS, PV, EVSE, site metering)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{serial}_{node_id}")},
        name=name,
        manufacturer="SPAN",
        model=_NODE_TYPE_LABELS.get(node_type, "Unknown"),
        via_device=(DOMAIN, serial),
    )


def make_unique_id(serial_number: str, node_id: str, property_id: str) -> str:
    """Build a unique entity ID: {serial}_{node}_{property}."""
    return f"{serial_number}_{node_id}_{property_id}"
