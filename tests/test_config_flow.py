"""Tests for SPAN Panel (eBus) config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_ebus.api_client import (
    SpanApiClient,
    SpanAuthError,
    SpanConnectionError,
    SpanNotReadyError,
)
from custom_components.span_ebus.const import CONF_CA_CERT_PEM, CONF_SERIAL_NUMBER, DOMAIN

from .conftest import (
    MOCK_BROKER_PASSWORD,
    MOCK_CA_CERT,
    MOCK_CONFIG_DATA,
    MOCK_HOST,
    MOCK_SERIAL,
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all config flow tests."""


@pytest.fixture(autouse=True)
def _mock_setup_entry():
    """Prevent actual setup during config flow tests."""
    with patch(
        "custom_components.span_ebus.async_setup_entry",
        return_value=True,
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_api_client(mock_api_client):
    """Patch SpanApiClient for all config flow tests."""
    with patch(
        "custom_components.span_ebus.config_flow.SpanApiClient",
        return_value=mock_api_client,
    ):
        yield


async def test_user_flow_passphrase(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test full manual user flow with passphrase auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    # Enter host
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "auth_menu"

    # Choose passphrase
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth_passphrase"

    # Enter passphrase
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "test-passphrase"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"SPAN Panel {MOCK_SERIAL}"
    assert result["data"][CONF_SERIAL_NUMBER] == MOCK_SERIAL
    assert result["data"][CONF_CA_CERT_PEM] == MOCK_CA_CERT


async def test_user_flow_door_bypass(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test manual user flow with door bypass auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    assert result["type"] is FlowResultType.MENU

    # Choose door bypass
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_door_bypass"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth_door_bypass"

    # Submit door bypass
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_passphrase_field_is_masked(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """The HOP passphrase must never render as a plain text field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )
    assert result["step_id"] == "auth_passphrase"

    selector = result["data_schema"].schema["passphrase"]
    assert isinstance(selector, TextSelector)
    assert selector.config["type"] == TextSelectorType.PASSWORD


async def test_flow_uses_home_assistant_shared_session(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """The flow must hand HA's shared session in, never build its own."""
    with patch(
        "custom_components.span_ebus.config_flow.SpanApiClient",
        return_value=mock_api_client,
    ) as client_cls:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": MOCK_HOST}
        )

    assert client_cls.call_args.args == (MOCK_HOST, async_get_clientsession(hass))


async def test_user_flow_cannot_connect(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test user flow with connection error."""
    mock_api_client.get_status.side_effect = SpanConnectionError("Cannot connect")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("detail", ["Rejected by the panel", None])
async def test_user_flow_invalid_passphrase(
    hass: HomeAssistant,
    mock_api_client,
    detail: str | None,
) -> None:
    """The panel's wrong-passphrase rejection shows the invalid_auth error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )

    # Choose passphrase
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )

    # First attempt: invalid
    mock_api_client.register.side_effect = SpanAuthError(
        detail or "Authentication rejected", detail
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    # Second attempt: valid
    mock_api_client.register.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "correct"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "detail",
    ["Rejected by the panel", None],
)
async def test_door_bypass_not_active(
    hass: HomeAssistant,
    mock_api_client,
    detail: str | None,
) -> None:
    """The panel's no-credential rejection shows the door-bypass instructions."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_door_bypass"}
    )

    mock_api_client.register.side_effect = SpanAuthError(
        detail or "Authentication rejected", detail
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "door_bypass_not_active"}


async def test_zeroconf_discovery(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test zeroconf discovery flow."""
    discovery_info = ZeroconfServiceInfo(
        ip_address="192.168.1.100",
        ip_addresses=["192.168.1.100"],
        port=8883,
        hostname="span-nt-0000-abc12.local.",
        type="_ebus._tcp.local.",
        name="span-nt-0000-abc12._ebus._tcp.local.",
        properties={},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "auth_menu"

    # Choose passphrase and complete
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "test"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_zeroconf_already_configured(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test zeroconf discovery of already-configured panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id=MOCK_SERIAL,
    )
    entry.add_to_hass(hass)

    discovery_info = ZeroconfServiceInfo(
        ip_address="192.168.1.100",
        ip_addresses=["192.168.1.100"],
        port=8883,
        hostname="span-nt-0000-abc12.local.",
        type="_ebus._tcp.local.",
        name="span-nt-0000-abc12._ebus._tcp.local.",
        properties={},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _start_auth(hass: HomeAssistant, step: str) -> dict:
    """Run the flow to the given auth step's form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


_SUBMIT = {"auth_passphrase": {"passphrase": "test"}, "auth_door_bypass": {}}


@pytest.mark.parametrize("step", ["auth_passphrase", "auth_door_bypass"])
@pytest.mark.parametrize("password", [None, ""])
async def test_missing_broker_password_is_specific_error(
    hass: HomeAssistant,
    mock_api_client,
    mock_auth_response,
    step: str,
    password: str | None,
) -> None:
    """A registration without a broker password must not create an entry."""
    result = await _start_auth(hass, step)
    mock_auth_response.ebus_broker_password = password
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUBMIT[step]
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "broker_password_unavailable"}

    # Retrying once the panel returns the password succeeds.
    mock_auth_response.ebus_broker_password = MOCK_BROKER_PASSWORD
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUBMIT[step]
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("step", ["auth_passphrase", "auth_door_bypass"])
async def test_register_not_ready(
    hass: HomeAssistant,
    mock_api_client,
    step: str,
) -> None:
    """A 503 from register is a retryable not-ready error."""
    result = await _start_auth(hass, step)
    mock_api_client.register.side_effect = SpanNotReadyError(
        "Serial number is not available yet"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUBMIT[step]
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_ready"}

    mock_api_client.register.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUBMIT[step]
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("step", ["auth_passphrase", "auth_door_bypass"])
async def test_register_passphrase_unavailable(
    hass: HomeAssistant,
    mock_api_client,
    step: str,
) -> None:
    """The documented passphrase-unavailable 422 shows its own error."""
    result = await _start_auth(hass, step)
    mock_api_client.register.side_effect = SpanAuthError(
        "Dashboard password is not available",
        "Dashboard password is not available",
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _SUBMIT[step]
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "broker_password_unavailable"}


@pytest.mark.parametrize(
    "failure",
    [
        {"side_effect": SpanConnectionError("Cannot connect")},
        {"return_value": ""},
        {"return_value": "<html>not a certificate</html>"},
    ],
    ids=["error", "empty", "not-pem"],
)
async def test_ca_download_failure_is_retryable(
    hass: HomeAssistant,
    mock_api_client,
    failure,
) -> None:
    """No entry is created without the CA; a retry succeeds without re-registering."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )

    mock_api_client.get_ca_certificate.configure_mock(**failure)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "test-passphrase"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ca_certificate"
    assert result["errors"] == {"base": "ca_unavailable"}
    assert not hass.config_entries.async_entries(DOMAIN)

    mock_api_client.get_ca_certificate.side_effect = None
    mock_api_client.get_ca_certificate.return_value = MOCK_CA_CERT
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CA_CERT_PEM] == MOCK_CA_CERT
    assert mock_api_client.register.await_count == 1


# --- REST client parsing, against both firmware response shapes ---

_BASE = f"http://{MOCK_HOST}/api/v2"
_REGISTER = f"{_BASE}/auth/register"
_JSON = {"content-type": "application/json"}
_AUTH_OUT = {
    "accessToken": "tok",
    "tokenType": "Bearer",
    "iatMs": 1,
    "hostname": "span",
    "serialNumber": MOCK_SERIAL,
    "hopPassphrase": "pass",
    "ebusBrokerUsername": MOCK_SERIAL,
    "ebusBrokerPassword": "pass",
    "ebusBrokerHost": "span.local",
    "ebusBrokerMqttsPort": 8883,
    "ebusBrokerWsPort": 9001,
    "ebusBrokerWssPort": 9002,
}


@pytest.fixture
def api(hass: HomeAssistant, aioclient_mock) -> SpanApiClient:
    """Return a real client on HA's mocked shared session."""
    return SpanApiClient(MOCK_HOST, async_get_clientsession(hass))


async def test_status_without_hardware_version(api, aioclient_mock) -> None:
    """r202633 status has no hardwareVersion."""
    aioclient_mock.get(
        f"{_BASE}/status",
        json={"serialNumber": MOCK_SERIAL, "firmwareVersion": "spanos2/r202633/02",
              "proximityProven": False},
        headers=_JSON,
    )
    status = await api.get_status()
    assert status.serial_number == MOCK_SERIAL
    assert status.hardware_version is None


async def test_status_with_hardware_version(api, aioclient_mock) -> None:
    """r202639 status carries hardwareVersion."""
    aioclient_mock.get(
        f"{_BASE}/status",
        json={"serialNumber": MOCK_SERIAL, "firmwareVersion": "spanos2/r202639/02",
              "proximityProven": False, "hardwareVersion": "2.0"},
        headers=_JSON,
    )
    status = await api.get_status()
    assert status.firmware_version == "spanos2/r202639/02"
    assert status.hardware_version == "2.0"


async def test_register_with_password(api, aioclient_mock) -> None:
    """The r202633 shape, where the password is always present."""
    aioclient_mock.post(_REGISTER, json=_AUTH_OUT)
    auth = await api.register(passphrase="pass")
    assert auth.ebus_broker_password == "pass"
    assert auth.ebus_broker_mqtts_port == 8883


@pytest.mark.parametrize("shape", ["null", "absent"])
async def test_register_without_password(api, aioclient_mock, shape: str) -> None:
    """r202639 may send the password as null, or omit it."""
    body = dict(_AUTH_OUT, hopPassphrase=None, ebusBrokerPassword=None)
    if shape == "absent":
        del body["hopPassphrase"], body["ebusBrokerPassword"]
    aioclient_mock.post(_REGISTER, json=body)
    auth = await api.register()
    assert auth.ebus_broker_password is None
    assert auth.access_token == "tok"


async def test_register_missing_required_field_raises(api, aioclient_mock) -> None:
    """Fields other than the password stay required."""
    body = dict(_AUTH_OUT)
    del body["accessToken"]
    aioclient_mock.post(_REGISTER, json=body)
    with pytest.raises(KeyError):
        await api.register()


async def test_register_422_string_detail(api, aioclient_mock) -> None:
    """A 422 body is {"detail": "<message>"}."""
    aioclient_mock.post(
        _REGISTER, status=422, json={"detail": "Dashboard password is not available"}
    )
    with pytest.raises(SpanAuthError) as exc:
        await api.register()
    assert exc.value.detail == "Dashboard password is not available"


@pytest.mark.parametrize(
    "body",
    [
        {"detail": [{"loc": ["body", "name"], "msg": "field required",
                     "type": "value_error.missing"}]},
        {},
        None,
    ],
    ids=["validation-list", "no-detail", "no-body"],
)
async def test_register_422_without_string_detail(api, aioclient_mock, body) -> None:
    """A 422 without a string detail is a rejection without a panel message."""
    if body is None:
        aioclient_mock.post(_REGISTER, status=422, text="")
    else:
        aioclient_mock.post(_REGISTER, status=422, json=body)
    with pytest.raises(SpanAuthError) as exc:
        await api.register()
    assert exc.value.detail is None


async def test_register_503_not_ready(api, aioclient_mock) -> None:
    """A 503 means the panel cannot register clients yet."""
    aioclient_mock.post(
        _REGISTER, status=503, json={"detail": "Serial number is not available yet"}
    )
    with pytest.raises(SpanNotReadyError):
        await api.register()
