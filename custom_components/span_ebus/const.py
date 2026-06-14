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
DESCRIPTION_TIMEOUT = 30  # seconds to wait for the root device's MQTT $description
DEVICE_READY_TIMEOUT = 120  # seconds to wait for the root device's "ready" state
# Tree-rooted mode (SDK 0.3.0+) discovers descendants only after the parent's
# init→ready edge, so controller.devices populates over time. Wait for the full
# transitive closure (panel → lugs / BESS / PV / EVSE / circuits, BESS → MID)
# to settle before invoking the mapper layer; missing the wait drops every
# descendant device + its entities on the floor.
TREE_DISCOVERY_TIMEOUT = 30  # safety backstop on event-driven tree-discovery wait
CIRCUIT_NAMES_TIMEOUT = 10  # seconds to wait for circuit name properties after ready
API_TIMEOUT = 15  # seconds for REST API calls

# MQTT
MQTT_QOS = 1  # QoS 1 avoids paho-mqtt _in_messages accumulation with QoS 2
EBUS_HOMIE_DOMAIN = "ebus"

# Homie device-type URI prefix; trailing segment is the short device-class name.
HOMIE_DEVICE_TYPE_PREFIX = "energy.ebus.device."

# Device classes (G3P-23496 tree data model — short names extracted from the URI).
DEVICE_TYPE_DISTRIBUTION_ENCLOSURE = "distribution-enclosure"
DEVICE_TYPE_LUGS = "lugs"
DEVICE_TYPE_BESS = "bess"
DEVICE_TYPE_MID = "mid"
DEVICE_TYPE_PV = "pv"
DEVICE_TYPE_EVSE = "evse"
DEVICE_TYPE_CIRCUIT = "circuit"

# Capabilities (Homie node-ids within a device — used as dispatch keys against
# the (device-class, capability) → mapper table in node_mappers_tree.py).
CAPABILITY_INFO = "info"
CAPABILITY_DOOR = "door"
CAPABILITY_METER = "meter"
CAPABILITY_STATUS = "status"
CAPABILITY_PCS = "pcs"
CAPABILITY_POWER_FLOWS = "power-flows"
CAPABILITY_SHED_FORECAST = "shed-forecast"
CAPABILITY_SHED = "shed"
CAPABILITY_SOC = "soc"
CAPABILITY_GRID = "grid"
CAPABILITY_SWITCH = "switch"
CAPABILITY_PRIORITY = "priority"
CAPABILITY_CONFIG = "config"
CAPABILITY_CONNECTION = "connection"
