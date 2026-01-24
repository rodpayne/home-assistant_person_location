"""Constants and Classes for person_location integration."""

import asyncio
from datetime import datetime, timedelta
import logging
import threading

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import homeassistant.helpers.config_validation as cv
from homeassistant.util.yaml.objects import (
    NodeListClass,
    NodeStrClass,
)

# Our info:

DOMAIN = "person_location"
API_STATE_OBJECT = DOMAIN + "." + DOMAIN + "_integration"
INTEGRATION_NAME = "Person Location"
ISSUE_URL = "https://github.com/rodpayne/home-assistant_person_location/issues"

VERSION = "2026.01.24"

# Titles for the config entries:

# TITLE_IMPORTED_YAML_CONFIG = "Imported YAML Config"
TITLE_IMPORTED_YAML_CONFIG = "Person Location Config"
TITLE_PERSON_LOCATION_CONFIG = "Person Location Config"

# Constants:

IC3_STATIONARY_STATE_PREFIX = "StatZon"
IC3_STATIONARY_ZONE_PREFIX = "ic3_stationary_"
METERS_PER_KM = 1000
METERS_PER_MILE = 1609.34

# Fixed parameters:

FAR_AWAY_METERS = 400 * METERS_PER_KM
MIN_DISTANCE_TRAVELLED_TO_GEOCODE = 5  # in km?
THROTTLE_INTERVAL = timedelta(
    seconds=2
)  # See https://operations.osmfoundation.org/policies/nominatim/ regarding throttling.
WAZE_MIN_METERS_FROM_HOME = 500

# Parameters that we may want to be configurable in the future:

DEFAULT_LOCALITY_PRIORITY_OSM = (
    # "neighbourhood", # ---- smallest urban division (e.g. block, named area)
    "suburb",  # ------------ named area within a city
    "hamlet",  # ------------ very small rural settlement
    "village",  # ----------- small rural settlement
    "town",  # -------------- larger than village, smaller than city
    "city_district",  # ----- administrative district within a city
    "municipality",  # ------ local government unit (varies by country)
    "city",  # -------------- major urban center
    "county",  # ------------ regional division (e.g. Utah County)
    "state_district",  # ---- sub-state division (used in some countries)
    "state",  # ------------- e.g. Utah
    "country",  # ----------- e.g. United States
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

# --------------------------------------------
#  Data (structural configuration parameters)
# --------------------------------------------

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
CONF_DISTANCE_DURATION_SOURCE = "distance_duration_source"
CONF_USE_WAZE = "use_waze"
CONF_WAZE_REGION = "waze_region"

CONF_LANGUAGE = "language"
DEFAULT_LANGUAGE = "en"

CONF_OUTPUT_PLATFORM = "platform"
DEFAULT_OUTPUT_PLATFORM = "sensor"
VALID_OUTPUT_PLATFORM = ["sensor", "device_tracker"]

CONF_REGION = "region"
DEFAULT_REGION = "US"

# API keys
CONF_GOOGLE_API_KEY = "google_api_key"
CONF_MAPBOX_API_KEY = "mapbox_api_key"
CONF_MAPQUEST_API_KEY = "mapquest_api_key"
CONF_OSM_API_KEY = "osm_api_key"
CONF_RADAR_API_KEY = "radar_api_key"
DEFAULT_API_KEY_NOT_SET = "not used"
REDACT_KEYS = {
    CONF_GOOGLE_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
}

# API providers
SWITCH_GOOGLE_GEOCODING_API = "google_geocoding_api"
SWITCH_GOOGLE_DISTANCE_API = "google_distance_matrix_api"
SWITCH_MAPBOX_DIRECTIONS_API = "mapbox_directions_api"
SWITCH_MAPBOX_STATIC_IMAGE_API = "mapbox_static_image_api"
SWITCH_MAPQUEST_GEOCODING_API = "mapquest_geocoding_api"
SWITCH_OSM_NOMINATIM_GEOCODING_API = "nominatim_geocoding_api"
SWITCH_RADAR_GEOCODING_API = "radar_geocoding_api"
SWITCH_RADAR_DISTANCE_API = "radar_routing_distance_api"
SWITCH_WAZE_TRAVEL_TIME = "waze_travel_time"

# API providers for images
SWITCH_GOOGLE_MAPS_STATIC_API = "google_maps_static_api"
SWITCH_MAPBOX_STATIC_IMAGE_API = "mapbox_static_image_api"
SWITCH_MAPQUEST_STATIC_MAP_API = "mapquest_static_map_api"
SWITCH_RADAR_STATIC_MAPS_API = "radar_static_maps_api"

# API providers and their keys
API_PROVIDER_SWITCHES = [
    (SWITCH_GOOGLE_GEOCODING_API, CONF_GOOGLE_API_KEY),
    (SWITCH_GOOGLE_DISTANCE_API, CONF_GOOGLE_API_KEY),
    (SWITCH_GOOGLE_MAPS_STATIC_API, CONF_GOOGLE_API_KEY),
    (SWITCH_MAPBOX_DIRECTIONS_API, CONF_MAPBOX_API_KEY),
    (SWITCH_MAPBOX_STATIC_IMAGE_API, CONF_MAPBOX_API_KEY),
    (SWITCH_MAPQUEST_GEOCODING_API, CONF_MAPQUEST_API_KEY),
    (SWITCH_MAPQUEST_STATIC_MAP_API, CONF_MAPQUEST_API_KEY),
    (SWITCH_OSM_NOMINATIM_GEOCODING_API, CONF_OSM_API_KEY),
    (SWITCH_RADAR_GEOCODING_API, CONF_RADAR_API_KEY),
    (SWITCH_RADAR_DISTANCE_API, CONF_RADAR_API_KEY),
    (SWITCH_RADAR_STATIC_MAPS_API, CONF_RADAR_API_KEY),
    (SWITCH_WAZE_TRAVEL_TIME, None),
]

# Image API providers by key
IMAGE_API_PROVIDER_SWITCHES = {
    CONF_GOOGLE_API_KEY: SWITCH_GOOGLE_MAPS_STATIC_API,
    CONF_MAPBOX_API_KEY: SWITCH_MAPBOX_STATIC_IMAGE_API,
    CONF_MAPQUEST_API_KEY: SWITCH_MAPQUEST_STATIC_MAP_API,
    CONF_RADAR_API_KEY: SWITCH_RADAR_STATIC_MAPS_API,
}

# State abbreviation dictionary

STATE_ABBREVIATIONS = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}

# Camera provider fields

CONF_CONTENT_TYPE = "content_type"
CONF_NAME = "name"
CONF_STATE = "state"
CONF_STILL_IMAGE_URL = "still_image_url"
CONF_VERIFY_SSL = "verify_ssl"

# Camera provider management (OptionsFlow + config entry)

CONF_DONE = "done"
CONF_EDIT_PROVIDER = "edit_provider"
CONF_NEW_PROVIDER_NAME = "new_provider_name"
CONF_NEW_PROVIDER_STATE = "new_provider_state"
CONF_NEW_PROVIDER_URL = "new_provider_url"
CONF_PROVIDERS = "providers"
CONF_REMOVE_PROVIDERS = "remove_providers"

# -----------------------------------------------
#  Options (behavioral configuration parameters)
# -----------------------------------------------

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

ALLOWED_OPTIONS_KEYS = {
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_HOURS_EXTENDED_AWAY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_SHOW_ZONE_WHEN_AWAY,
}

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
                    sorted,  # ensures deterministic ordering
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
                vol.Optional(CONF_FOLLOW_PERSON_INTEGRATION, default=False): cv.boolean,
                vol.Optional(CONF_DISTANCE_DURATION_SOURCE, default="waze"): cv.string,
                vol.Optional(CONF_PERSON_NAMES, default=[]): vol.All(
                    cv.ensure_list, [PERSON_SCHEMA]
                ),
            },
            extra=vol.ALLOW_EXTRA,
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

# Items under hass.data[DOMAIN]:

DATA_STATE = "state"
DATA_ATTRIBUTES = "attributes"
DATA_CONFIG_ENTRY = "config_entry"
DATA_CONFIGURATION = "configuration"  # CFG = Merged YAML and Config Data and Options
DATA_ENTITY_INFO = "entity_info"  # TODO: structure like switch_entities
DATA_INTEGRATION = "integration"  # PLI = Person Location integration runtime data
DATA_SENSOR_ENTITIES = "sensor_entities"  # TODO: structure like switch_entities
DATA_SWITCH_ENTITIES = "switch_entities"
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

_warned_messages = set()


def warn_once(logger: logging.Logger, message: str) -> bool:
    """Log a warning only once for message."""
    if message not in _warned_messages:
        logger.warning(message)
        _warned_messages.add(message)
        return True
    return False


_error_messages = set()


def error_once(logger: logging.Logger, message: str) -> bool:
    """Log an error only once for message."""
    if message not in _error_messages:
        logger.error(message)
        _error_messages.add(message)
        return True
    return False


def get_home_coordinates(hass: HomeAssistant) -> tuple:
    """Get Home latitude and longitude and validate that they have been entered."""
    lat = hass.config.latitude
    lon = hass.config.longitude

    if not lat or not lon or (lat == 0 and lon == 0):
        description = "Home Location is needed for geocoding (Settings → System → General → Location)"
        if error_once(
            _LOGGER,
            description,
        ):
            # ⭐ Create a repair notification - Required configuration is missing
            ir.async_create_issue(
                hass,
                DOMAIN,
                "home_location_required",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                title="Home Assistant Location Required",
                description=description,
            )

        return (None, None)

    # ⭐ Clear repair notification
    registry = ir.async_get(hass)
    if registry.async_get_issue(DOMAIN, "home_location_required"):
        ir.async_delete_issue(hass, DOMAIN, "home_location_required")

    return (lat, lon)


class PERSON_LOCATION_INTEGRATION:
    """Class to represent the integration runtime."""

    def __init__(self, _entity_id, _hass: HomeAssistant) -> None:
        """Initialize the integration instance."""
        # Log startup message:
        _LOGGER.info(
            STARTUP_VERSION.format(name=DOMAIN, version=VERSION, issue_link=ISSUE_URL)
        )

        self.entity_id = _entity_id
        self.hass = _hass

        self.state = "on"
        self.attributes = {}
        self.configuration = {}
        self.entity_info = {}

        self._target_sensors_restored = []

        self.attributes[ATTR_ICON] = "mdi:api"
        self.attributes["api_last_updated"] = datetime.now()
        self.attributes["api_exception_count"] = 0
        self.attributes["api_calls_requested"] = 0
        self.attributes["api_calls_skipped"] = 0
        self.attributes["api_calls_throttled"] = 0
        self.attributes["startup"] = True
        self.attributes["waze_error_count"] = 0
        self.attributes[ATTR_ATTRIBUTION] = (
            f"System information for the {INTEGRATION_NAME} integration ({DOMAIN}), version {VERSION}."
        )

        # ❌ self.set_state()

    def set_state(self) -> None:
        """Schedule async_set_state safely from a thread or sync context."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_set_state())
        )

    async def async_set_state(self) -> None:
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

        # simple_attributes = {ATTR_ICON: self.attributes[ATTR_ICON]}
        # self.hass.states.async_set(self.entity_id, self.state, simple_attributes)
        self.hass.states.async_set(self.entity_id, self.state, self.attributes)

        _LOGGER.debug(
            "[async_set_state] (%s) -state: %s -attributes: %s",
            self.entity_id,
            self.state,
            self.attributes,
        )


class PERSON_LOCATION_TRIGGER:
    """Class to represent device trackers that trigger us."""

    def __init__(self, _entity_id, _pli: PERSON_LOCATION_INTEGRATION) -> None:
        """Initialize the entity instance."""
        _LOGGER.debug("[PERSON_LOCATION_TRIGGER] (%s) === __init__ ===", _entity_id)

        self.entity_id = _entity_id
        self.pli = _pli
        self.hass = _pli.hass

        self.configuration = self.hass.data[DOMAIN][DATA_CONFIGURATION]

        targetStateObject = self.hass.states.get(self.entity_id)
        if targetStateObject is not None:
            self.firstTime = False
            if (
                targetStateObject.state.startswith(IC3_STATIONARY_STATE_PREFIX)
                or targetStateObject.state == STATE_NOT_HOME
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
        elif (
            "friendly_name" in self.attributes
            and self.entity_id.split(".")[0] == "person"
        ):
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
