"""Constants for the SPAN Panel (eBus) integration."""

from homeassistant.const import Platform

DOMAIN = "span_ebus"

# Config entry keys
CONF_HOST = "host"
CONF_SERIAL_NUMBER = "serial_number"
CONF_ACCESS_TOKEN = "access_token"
CONF_EBUS_BROKER_USERNAME = "ebus_broker_username"
CONF_EBUS_BROKER_PASSWORD = "ebus_broker_password"
CONF_EBUS_BROKER_HOST = "ebus_broker_host"
CONF_EBUS_BROKER_PORT = "ebus_broker_port"
CONF_CA_CERT_PEM = "ca_cert_pem"

# Defaults
DEFAULT_EBUS_BROKER_PORT = 8883

# Platforms to set up
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Timeouts
DESCRIPTION_TIMEOUT = 30  # seconds to wait for MQTT $description
DEVICE_READY_TIMEOUT = 120  # seconds to wait for device "ready" state
CIRCUIT_NAMES_TIMEOUT = 10  # seconds to wait for circuit name properties after ready
API_TIMEOUT = 15  # seconds for REST API calls

# MQTT
MQTT_QOS = 1  # QoS 1 avoids paho-mqtt _in_messages accumulation with QoS 2
EBUS_HOMIE_DOMAIN = "ebus"
