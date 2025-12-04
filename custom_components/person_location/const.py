"""Constants and Classes for person_location integration."""

import asyncio
import logging
import threading
from datetime import datetime, timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.mobile_app.const import ATTR_VERTICAL_ACCURACY
from homeassistant.components.waze_travel_time.const import REGIONS as WAZE_REGIONS
from homeassistant.components.zone.const import DOMAIN as ZONE_DOMAIN
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_FRIENDLY_NAME,
    ATTR_GPS_ACCURACY,
    ATTR_ICON,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.util.yaml.objects import (
    NodeListClass,
    NodeStrClass,
)

# Our info:
DOMAIN = "person_location"
API_STATE_OBJECT = DOMAIN + "." + DOMAIN + "_integration"
INTEGRATION_NAME = "Person Location"
ISSUE_URL = "https://github.com/rodpayne/home-assistant_person_location/issues"
VERSION = "2025.11.29"

# Titles for the config entries:
#TITLE_IMPORTED_YAML_CONFIG = "Imported YAML Config"
TITLE_IMPORTED_YAML_CONFIG = "Person Location Config"
TITLE_PERSON_LOCATION_CONFIG = "Person Location Config"

# Constants:
METERS_PER_KM = 1000
METERS_PER_MILE = 1609.34
IC3_STATIONARY_STATE_PREFIX = "StatZon"
IC3_STATIONARY_ZONE_PREFIX = "ic3_stationary_"

# Fixed parameters:
MIN_DISTANCE_TRAVELLED_TO_GEOCODE = 5  # in km?
THROTTLE_INTERVAL = timedelta(
    seconds=2
)  # See https://operations.osmfoundation.org/policies/nominatim/ regarding throttling.
WAZE_MIN_METERS_FROM_HOME = 500
FAR_AWAY_METERS = 400 * METERS_PER_KM

# Parameters that we may want to be configurable in the future:
DEFAULT_LOCALITY_PRIORITY_OSM = (
#    "neighbourhood",       # smallest urban division (e.g. block, named area)
    "suburb",              # named area within a city
    "hamlet",              # very small rural settlement
    "village",             # small rural settlement
    "town",                # larger than village, smaller than city
    "city_district",       # administrative district within a city
    "municipality",        # local government unit (varies by country)
    "city",                # major urban center
    "county",              # regional division (e.g. Utah County)
    "state_district",      # sub-state division (used in some countries)
    "state",               # e.g. Utah
    "country",             # e.g. United States
)

# Attribute names:
ATTR_ALTITUDE = "altitude"
ATTR_BREAD_CRUMBS = "bread_crumbs"
ATTR_COMPASS_BEARING = "compass_bearing"
ATTR_DIRECTION = "direction"
ATTR_DRIVING_MILES = "driving_miles"
ATTR_DRIVING_MINUTES = "driving_minutes"
ATTR_GEOCODED = "geocoded"
ATTR_LAST_LOCATED = "last_located"
ATTR_LOCATION_TIME = "location_time"
ATTR_METERS_FROM_HOME = "meters_from_home"
ATTR_MILES_FROM_HOME = "miles_from_home"
ATTR_PERSON_NAME = "person_name"
ATTR_REPORTED_STATE = "reported_state"
ATTR_SOURCE = "source"
ATTR_SPEED = "speed"
ATTR_ZONE = "zone"

ATTR_GOOGLE_MAPS = "Google_Maps"
ATTR_MAPQUEST = "MapQuest"
ATTR_OPEN_STREET_MAP = "Open_Street_Map"
ATTR_RADAR = "Radar"

# Items under target.this_entity_info:
INFO_GEOCODE_COUNT = "geocode_count"
INFO_LOCALITY = "locality"
INFO_TRIGGER_COUNT = "trigger_count"
INFO_LOCATION_LATITUDE = "location_latitide"
INFO_LOCATION_LONGITUDE = "location_longitide"

# Configuration Parameters:
CONF_LANGUAGE = "language"
DEFAULT_LANGUAGE = "en"

CONF_FRIENDLY_NAME_TEMPLATE = "friendly_name_template"
DEFAULT_FRIENDLY_NAME_TEMPLATE = (
    "{{person_name}} ({{source.attributes.friendly_name}}) {{friendly_name_location}}"
)

CONF_HOURS_EXTENDED_AWAY = "extended_away"
DEFAULT_HOURS_EXTENDED_AWAY = 48

CONF_MINUTES_JUST_ARRIVED = "just_arrived"
DEFAULT_MINUTES_JUST_ARRIVED = 3

CONF_MINUTES_JUST_LEFT = "just_left"
DEFAULT_MINUTES_JUST_LEFT = 3

CONF_SHOW_ZONE_WHEN_AWAY = "show_zone_when_away"
DEFAULT_SHOW_ZONE_WHEN_AWAY = False

CONF_OUTPUT_PLATFORM = "platform"
DEFAULT_OUTPUT_PLATFORM = "sensor"
VALID_OUTPUT_PLATFORM = ["sensor", "device_tracker"]

CONF_REGION = "region"
DEFAULT_REGION = "US"

CONF_DISTANCE_DURATION_SOURCE = "distance_duration_source"
CONF_USE_WAZE = "use_waze"
CONF_WAZE_REGION = "waze_region"

CONF_GOOGLE_API_KEY = "google_api_key"
CONF_MAPBOX_API_KEY = "mapbox_api_key"
CONF_MAPQUEST_API_KEY = "mapquest_api_key"
CONF_NAME = "name"
CONF_OSM_API_KEY = "osm_api_key"
CONF_RADAR_API_KEY = "radar_api_key"
DEFAULT_API_KEY_NOT_SET = "not used"


CONF_CREATE_SENSORS = "create_sensors"
VALID_CREATE_SENSORS = [
    ATTR_ALTITUDE,
    ATTR_BREAD_CRUMBS,
    ATTR_DIRECTION,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_GEOCODED,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
]

CONF_FOLLOW_PERSON_INTEGRATION = "follow_person_integration"
CONF_PERSON_NAMES = "person_names"
CONF_DEVICES = "devices"
VALID_ENTITY_DOMAINS = ("binary_sensor", "device_tracker", "person", "sensor")

CONF_FROM_YAML = "configuration_from_yaml"

# Camera provider fields

CONF_STATE = "state"
CONF_STILL_IMAGE_URL = "still_image_url"
CONF_CONTENT_TYPE = "content_type"
CONF_VERIFY_SSL = "verify_ssl"

# Camera provider management (OptionsFlow + config entry)

CONF_PROVIDERS = "providers"
CONF_REMOVE_PROVIDERS = "remove_providers"
CONF_NEW_PROVIDER_NAME = "new_provider_name"
CONF_NEW_PROVIDER_URL = "new_provider_url"
CONF_NEW_PROVIDER_STATE = "new_provider_state"
CONF_EDIT_PROVIDER = "edit_provider"
CONF_DONE = "done"

STARTUP_VERSION = """
-------------------------------------------------------------------
{name}
Version: {version}
This is a custom integration
If you have any issues with this you need to open an issue here:
{issue_link}
-------------------------------------------------------------------
"""

PERSON_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_DEVICES, default=[]): vol.All(
            cv.ensure_list, cv.entities_domain(VALID_ENTITY_DOMAINS)
        ),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_CREATE_SENSORS, default=[]): vol.All(
                    cv.ensure_list,  # turn string into list
                    [vol.In(VALID_CREATE_SENSORS)],
                    sorted           # ensures deterministic ordering
                ),
                vol.Optional(
                    CONF_HOURS_EXTENDED_AWAY, default=DEFAULT_HOURS_EXTENDED_AWAY
                ): cv.positive_int,
                vol.Optional(
                    CONF_MINUTES_JUST_ARRIVED, default=DEFAULT_MINUTES_JUST_ARRIVED
                ): cv.positive_int,
                vol.Optional(
                    CONF_MINUTES_JUST_LEFT, default=DEFAULT_MINUTES_JUST_LEFT
                ): cv.positive_int,
                vol.Optional(
                    CONF_SHOW_ZONE_WHEN_AWAY, default=DEFAULT_SHOW_ZONE_WHEN_AWAY
                ): cv.boolean,
                vol.Optional(CONF_LANGUAGE, default=DEFAULT_LANGUAGE): cv.string,
                vol.Optional(
                    CONF_OUTPUT_PLATFORM, default=DEFAULT_OUTPUT_PLATFORM
                ): cv.string,
                vol.Optional(CONF_REGION, default=DEFAULT_REGION): cv.string,
                vol.Optional(
                    CONF_MAPBOX_API_KEY, default=DEFAULT_API_KEY_NOT_SET
                ): cv.string,
                vol.Optional(
                    CONF_MAPQUEST_API_KEY, default=DEFAULT_API_KEY_NOT_SET
                ): cv.string,
                vol.Optional(
                    CONF_OSM_API_KEY, default=DEFAULT_API_KEY_NOT_SET
                ): cv.string,
                vol.Optional(
                    CONF_GOOGLE_API_KEY, default=DEFAULT_API_KEY_NOT_SET
                ): cv.string,
                vol.Optional(
                    CONF_RADAR_API_KEY, default=DEFAULT_API_KEY_NOT_SET
                ): cv.string,
                vol.Optional(CONF_FOLLOW_PERSON_INTEGRATION, default=False
                ): cv.boolean,
                vol.Optional(CONF_DISTANCE_DURATION_SOURCE, default="waze"
                ): cv.string,
                vol.Optional(CONF_PERSON_NAMES, default=[]): vol.All(
                    cv.ensure_list, [PERSON_SCHEMA]
                ),
            },
            extra=vol.ALLOW_EXTRA,
        ),
    },
    extra=vol.ALLOW_EXTRA,   
)

ALLOWED_OPTIONS_KEYS = {
    CONF_HOURS_EXTENDED_AWAY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_SHOW_ZONE_WHEN_AWAY,
    CONF_FRIENDLY_NAME_TEMPLATE,
}

# Items under hass.data[DOMAIN]:
DATA_STATE = "state"
DATA_ATTRIBUTES = "attributes"
DATA_CONFIG_ENTRY = "config_entry"
DATA_CONFIGURATION = "configuration"
DATA_ENTITY_INFO = "entity_info"
DATA_UNDO_STATE_LISTENER = "undo_state_listener"
DATA_UNDO_UPDATE_LISTENER = "undo_update_listener"
DATA_ASYNC_SETUP_ENTRY = "async_setup_entry"

INTEGRATION_LOCK = threading.Lock()
TARGET_LOCK = threading.Lock()
# Note to future me: If functions where these locks are used are converted to async,
#   they will need to be changed to asyncio.Lock and the locations where
#   `with TARGET_LOCK:` is used would need to change to `async with TARGET_LOCK:`
INTEGRATION_ASYNCIO_LOCK = asyncio.Lock()
TARGET_ASYNCIO_LOCK = asyncio.Lock()

_LOGGER = logging.getLogger(__name__)

def get_waze_region(country_code: str) -> str:
    """Determine Waze region from country code or Waze region setting"""
    country_code = country_code.lower()
    if country_code in ("us", "ca", "mx"):
        return "us"
    if country_code in WAZE_REGIONS:
        return country_code
    return "eu"

class PERSON_LOCATION_INTEGRATION:
    """Class to represent the integration itself."""

    def __init__(self, _entity_id, _hass):
        """Initialize the integration instance."""

        # Log startup message:
        _LOGGER.info(
            STARTUP_VERSION.format(name=DOMAIN, version=VERSION, issue_link=ISSUE_URL)
        )

        self.entity_id = _entity_id
        self.hass = _hass
        #self.config = _config
        self.state = "on"
        self.attributes = {}
        self.attributes[ATTR_ICON] = "mdi:api"

        self.configuration = {}
        self.entity_info = {}
        self._target_sensors_restored = []


        home_zone = "zone.home"
        self.attributes[ATTR_FRIENDLY_NAME] = f"{INTEGRATION_NAME} Service"
        self.attributes["home_latitude"] = str(
            self.hass.states.get(home_zone).attributes.get(ATTR_LATITUDE)
        )
        self.attributes["home_longitude"] = str(
            self.hass.states.get(home_zone).attributes.get(ATTR_LONGITUDE)
        )
        self.attributes["api_last_updated"] = datetime.now()
        self.attributes["api_error_count"] = 0
        self.attributes["api_calls_requested"] = 0
        self.attributes["api_calls_skipped"] = 0
        self.attributes["api_calls_throttled"] = 0
        self.attributes["startup"] = True
        self.attributes["waze_error_count"] = 0
        self.attributes[
            ATTR_ATTRIBUTION
        ] = f"System information for the {INTEGRATION_NAME} integration \
                ({DOMAIN}), version {VERSION}."

        '''
        if DOMAIN in self.config:
            self.configuration[CONF_FROM_YAML] = True

            # Pull in configuration from configuration.yaml:

            self.configuration[CONF_GOOGLE_API_KEY] = self.config[DOMAIN].get(
                CONF_GOOGLE_API_KEY, DEFAULT_API_KEY_NOT_SET
            )
            self.configuration[CONF_LANGUAGE] = self.config[DOMAIN].get(
                CONF_LANGUAGE, DEFAULT_LANGUAGE
            )
            self.configuration[CONF_FRIENDLY_NAME_TEMPLATE] = self.config[DOMAIN].get(
                CONF_FRIENDLY_NAME_TEMPLATE, DEFAULT_FRIENDLY_NAME_TEMPLATE
            )
            self.configuration[CONF_HOURS_EXTENDED_AWAY] = self.config[DOMAIN].get(
                CONF_HOURS_EXTENDED_AWAY, DEFAULT_HOURS_EXTENDED_AWAY
            )
            self.configuration[CONF_MINUTES_JUST_ARRIVED] = self.config[DOMAIN].get(
                CONF_MINUTES_JUST_ARRIVED, DEFAULT_MINUTES_JUST_ARRIVED
            )
            self.configuration[CONF_MINUTES_JUST_LEFT] = self.config[DOMAIN].get(
                CONF_MINUTES_JUST_LEFT, DEFAULT_MINUTES_JUST_LEFT
            )
            self.configuration[CONF_OUTPUT_PLATFORM] = self.config[DOMAIN].get(
                CONF_OUTPUT_PLATFORM, DEFAULT_OUTPUT_PLATFORM
            )
            self.configuration[CONF_MAPBOX_API_KEY] = self.config[DOMAIN].get(
                CONF_MAPBOX_API_KEY, DEFAULT_API_KEY_NOT_SET
            )
            self.configuration[CONF_MAPQUEST_API_KEY] = self.config[DOMAIN].get(
                CONF_MAPQUEST_API_KEY, DEFAULT_API_KEY_NOT_SET
            )
            self.configuration[CONF_OSM_API_KEY] = self.config[DOMAIN].get(
                CONF_OSM_API_KEY, DEFAULT_API_KEY_NOT_SET
            )
            self.configuration[CONF_RADAR_API_KEY] = self.config[DOMAIN].get(
                CONF_RADAR_API_KEY, DEFAULT_API_KEY_NOT_SET
            )
            self.configuration[CONF_SHOW_ZONE_WHEN_AWAY] = self.config[DOMAIN].get(
                CONF_SHOW_ZONE_WHEN_AWAY, DEFAULT_SHOW_ZONE_WHEN_AWAY
            )
            self.configuration[CONF_REGION] = self.config[DOMAIN].get(
                CONF_REGION, DEFAULT_REGION
            )
            self.configuration[CONF_WAZE_REGION] = get_waze_region(
                self.configuration[CONF_REGION]
            )
            if self.configuration[CONF_WAZE_REGION] in WAZE_REGIONS:
                self.configuration[CONF_USE_WAZE] = True
            else:
                self.configuration[CONF_USE_WAZE] = False
                _LOGGER.warning(
                    "Configured Waze region (%s) is not valid",
                    self.configuration[CONF_WAZE_REGION],
                )
                _LOGGER.debug(
                    "Valid Waze regions: %s",
                    WAZE_REGIONS,
                )
            raw_conf_create_sensors = self.config[DOMAIN].get(CONF_CREATE_SENSORS, [])
            itemType = type(raw_conf_create_sensors)
            if itemType in (list, NodeListClass):
                self.configuration[CONF_CREATE_SENSORS] = sorted(
                    raw_conf_create_sensors
                )
            elif itemType in (str, NodeStrClass):
                self.configuration[CONF_CREATE_SENSORS] = sorted(
                    [x.strip() for x in raw_conf_create_sensors.split(",")]
                )
            else:
                _LOGGER.error(
                    "Configured %s: %s is not valid (type %s)",
                    CONF_CREATE_SENSORS,
                    raw_conf_create_sensors,
                    itemType,
                )
                self.configuration[CONF_CREATE_SENSORS] = []
            for sensor_name in self.configuration[CONF_CREATE_SENSORS]:
                if sensor_name not in VALID_CREATE_SENSORS:
                    _LOGGER.error(
                        "Configured %s: %s is not valid",
                        CONF_CREATE_SENSORS,
                        sensor_name,
                    )
            self.configuration[CONF_FOLLOW_PERSON_INTEGRATION] = self.config[
                DOMAIN
            ].get(CONF_FOLLOW_PERSON_INTEGRATION, False)

            self.configuration[CONF_DEVICES] = {}
            for person_name_config in self.config[DOMAIN].get(CONF_PERSON_NAMES, []):
                person_name = person_name_config[CONF_NAME]
                _LOGGER.debug("person_name_config name = %s", person_name)
                devices = person_name_config[CONF_DEVICES]
                if (devices is str) or (devices is NodeStrClass):
                    devices = [devices]
                for device in devices:
                    _LOGGER.debug("person_name_config device = %s", device)
                    self.configuration[CONF_DEVICES][device] = person_name

        else:
            self.configuration[CONF_FROM_YAML] = False

            # Provide defaults if no configuration.yaml config:

            self.configuration[CONF_GOOGLE_API_KEY] = DEFAULT_API_KEY_NOT_SET
            self.configuration[CONF_LANGUAGE] = DEFAULT_LANGUAGE
            self.configuration[CONF_HOURS_EXTENDED_AWAY] = DEFAULT_HOURS_EXTENDED_AWAY
            self.configuration[CONF_MINUTES_JUST_ARRIVED] = DEFAULT_MINUTES_JUST_ARRIVED
            self.configuration[CONF_MINUTES_JUST_LEFT] = DEFAULT_MINUTES_JUST_LEFT
            self.configuration[CONF_OUTPUT_PLATFORM] = DEFAULT_OUTPUT_PLATFORM
            self.configuration[CONF_MAPQUEST_API_KEY] = DEFAULT_API_KEY_NOT_SET
            self.configuration[CONF_OSM_API_KEY] = DEFAULT_API_KEY_NOT_SET
            self.configuration[CONF_RADAR_API_KEY] = DEFAULT_API_KEY_NOT_SET
            self.configuration[CONF_REGION] = DEFAULT_REGION
            self.configuration[CONF_WAZE_REGION] = DEFAULT_REGION
            self.configuration[CONF_USE_WAZE] = True
            self.configuration[CONF_CREATE_SENSORS] = []
            self.configuration[CONF_FOLLOW_PERSON_INTEGRATION] = False
            self.configuration[CONF_DEVICES] = {}
        '''

        # ❌ self.set_state()

    def set_state(self):
        """Schedule async_set_state safely from a thread or sync context."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_set_state())
        )

    async def async_set_state(self):
        """Async-safe state setter."""
        integration_state_data = {
            DATA_STATE: self.state,
            DATA_ATTRIBUTES: self.attributes,
            DATA_CONFIGURATION: self.configuration,
            DATA_ENTITY_INFO: self.entity_info,
        }
        if DOMAIN in self.hass.data:
            self.hass.data[DOMAIN].update(integration_state_data)
        else:
            self.hass.data[DOMAIN] = integration_state_data

        simple_attributes = {ATTR_ICON: self.attributes[ATTR_ICON]}
        self.hass.states.async_set(self.entity_id, self.state, simple_attributes)

        _LOGGER.debug(
            "(%s.async_set_state) -state: %s -attributes: %s",
            self.entity_id,
            self.state,
            self.attributes,
        )
        
class PERSON_LOCATION_TRIGGER:
    """Class to represent device trackers that trigger us."""

    def __init__(self, _entity_id, _pli):
        """Initialize the entity instance."""

        _LOGGER.debug("[PERSON_LOCATION_TRIGGER] (%s) === __init__ ===", _entity_id)

        self.entity_id = _entity_id
        self.pli = _pli
        self.hass = _pli.hass

        self.configuration = self.hass.data[DOMAIN][DATA_CONFIGURATION]

        targetStateObject = self.hass.states.get(self.entity_id)
        if targetStateObject is not None:
            self.firstTime = False
            if (targetStateObject.state.startswith(IC3_STATIONARY_STATE_PREFIX) 
                    or 
                targetStateObject.state == STATE_NOT_HOME
            ):
                self.state = "Away"
            else:
                self.state = targetStateObject.state
            self.last_changed = targetStateObject.last_changed
            self.last_updated = targetStateObject.last_updated
            self.attributes = targetStateObject.attributes.copy()
        else:
            self.firstTime = True
            self.state = STATE_UNKNOWN
            self.last_changed = datetime(2020, 3, 14, 15, 9, 26, 535897)
            self.last_updated = datetime(2020, 3, 14, 15, 9, 26, 535897)
            self.attributes = {}

        if "friendly_name" in self.attributes:
            self.friendlyName = self.attributes["friendly_name"]
        else:
            self.friendlyName = ""
            _LOGGER.debug("friendly_name attribute is missing")

        if self.state.lower() == STATE_HOME or self.state.lower() == STATE_ON:
            self.stateHomeAway = "Home"
            self.state = "Home"
        else:
            self.stateHomeAway = "Away"
            if self.state == STATE_NOT_HOME:
                self.state = "Away"

        if self.entity_id in self.pli.configuration[CONF_DEVICES]:
            self.personName = self.pli.configuration[CONF_DEVICES][
                self.entity_id
            ].lower()
        elif "person_name" in self.attributes:
            self.personName = self.attributes["person_name"]
        elif "account_name" in self.attributes:
            self.personName = self.attributes["account_name"]
        elif "owner_fullname" in self.attributes:
            self.personName = self.attributes["owner_fullname"].split()[0].lower()
        elif "friendly_name" in self.attributes and self.entity_id.split(".")[0] == "person":
            self.personName = self.attributes["friendly_name"]
        else:
            self.personName = self.entity_id.split(".")[1].split("_")[0].lower()
            if self.firstTime is False:
                _LOGGER.debug(
                    'The account_name (or person_name) attribute is missing in %s, trying "%s"',
                    self.entity_id,
                    self.personName,
                )
        # It is tempting to make the output a device_tracker instead of sensor,
        #   so that it can be input into the Person built-in integration,
        #   but if you do, be very careful not to trigger a loop.
        #   The state and other attributes will also need to be adjusted.

        self.targetName = (
            self.configuration[CONF_OUTPUT_PLATFORM]
            + "."
            + self.personName.lower()
            + "_location"
        )
