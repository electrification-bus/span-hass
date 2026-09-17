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

# Grace period before a descendant that dropped off the tree loses its Home
# Assistant device. Removing a device deletes every entity registered on it,
# which is irreversible for the user: entity ids, long-term statistics and any
# Energy Dashboard rows pointing at them all go with it. A panel can drop and
# re-announce part of its tree for reasons that have nothing to do with the
# circuit being decommissioned (a retained ``$state`` clear, a partial
# ``$description.children`` republish), so a single removal signal is treated
# as "absent for now" and only a sustained absence retires the device.
DEVICE_REMOVAL_GRACE = 900  # seconds a descendant must stay absent before retirement

# Below this magnitude, a decrease on a TOTAL_INCREASING energy counter is
# treated as ordinary publisher jitter and held silently rather than warned
# about. Every decrease is still held, because Home Assistant reads ANY decrease
# on a total_increasing counter as a meter reset and adds the whole previous
# total to long-term statistics; the deadband governs only how loudly it is
# reported.
#
# Calibrated against a full day of live traffic across three panels rather than
# a single sample. The observed jitter is 0.1, 0.5, 1.0, 1.1, 1.5 and 2.0 Wh,
# on counters in the tens of MWh: a 2 Wh step on 8.9 MWh is 2e-7, which is
# float noise rather than an energy event. The recalibration events this guard
# exists for are five orders of magnitude larger; the smallest observed on the
# PV energy counter was 115.7 kWh, and the largest 1.01 MWh.
#
# That leaves an enormous safe range, so this sits deliberately in the middle
# of it: 50x above the largest observed jitter and 1000x below the smallest
# real event. An earlier 1.0 Wh value was calibrated from a 29-minute window
# that happened to contain only the 0.1 Wh case, and left the 1.1 to 2.0 Wh
# jitter still warning.
#
# Expressed in Wh and converted to each sensor's own unit at runtime, so a
# counter published in kWh is not given a deadband a thousand times too
# permissive.
COUNTER_DECREASE_DEADBAND_WH = 100.0

# MQTT
MQTT_QOS = 1  # QoS 1 avoids paho-mqtt _in_messages accumulation with QoS 2
EBUS_HOMIE_DOMAIN = "ebus"

# Homie device-type URI prefix; trailing segment is the short device-class name.
HOMIE_DEVICE_TYPE_PREFIX = "energy.ebus.device."

# Device classes (tree data model — short names extracted from the URI).
DEVICE_TYPE_DISTRIBUTION_ENCLOSURE = "distribution-enclosure"
DEVICE_TYPE_LUGS = "lugs"
DEVICE_TYPE_BESS = "bess"
DEVICE_TYPE_MID = "mid"
DEVICE_TYPE_PV = "pv"
DEVICE_TYPE_EVSE = "evse"
DEVICE_TYPE_CIRCUIT = "circuit"

# Capabilities (Homie node-ids within a device). Short capability names form
# part of the (device-class, capability, property) keys in the SEMANTICS table
# in semantics.py.
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
CAPABILITY_CONNECTION = "connection"
