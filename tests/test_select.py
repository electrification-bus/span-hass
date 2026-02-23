"""Tests for SPAN select platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.const import Platform

from custom_components.span_ebus.node_mappers import EntitySpec
from custom_components.span_ebus.select import SpanEbusSelect

from .conftest import MOCK_CIRCUIT_UUID, MOCK_SERIAL


@pytest.fixture
def mock_panel():
    panel = MagicMock()
    panel.serial_number = MOCK_SERIAL
    panel.available = True
    panel.register_property_callback = MagicMock(return_value=lambda: None)
    panel.register_availability_callback = MagicMock(return_value=lambda: None)
    panel.get_property_value = MagicMock(return_value=None)
    panel.set_property = MagicMock(return_value=True)
    return panel


@pytest.fixture
def priority_spec():
    return EntitySpec(
        platform=Platform.SELECT,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="shed-priority",
        name="Kitchen Shed Priority",
        settable=True,
        options=["MUST_HAVE", "NICE_TO_HAVE", "NON_ESSENTIAL"],
    )


def test_select_options(mock_panel, priority_spec):
    """Test select has correct options."""
    select = SpanEbusSelect(mock_panel, priority_spec)
    assert select._attr_options == ["MUST_HAVE", "NICE_TO_HAVE", "NON_ESSENTIAL"]


def test_select_update_known_option(mock_panel, priority_spec):
    """Test select accepts known option values."""
    select = SpanEbusSelect(mock_panel, priority_spec)
    select._update_from_value("MUST_HAVE")
    assert select._attr_current_option == "MUST_HAVE"


def test_select_update_unknown_option(mock_panel, priority_spec):
    """Test select handles unknown options gracefully."""
    select = SpanEbusSelect(mock_panel, priority_spec)
    select._update_from_value("UNKNOWN_VALUE")
    # Should still set the value even if unknown
    assert select._attr_current_option == "UNKNOWN_VALUE"


async def test_select_option(mock_panel, priority_spec):
    """Test selecting an option sends command to panel."""
    select = SpanEbusSelect(mock_panel, priority_spec)
    await select.async_select_option("NICE_TO_HAVE")
    mock_panel.set_property.assert_called_once_with(
        MOCK_CIRCUIT_UUID, "shed-priority", "NICE_TO_HAVE"
    )
