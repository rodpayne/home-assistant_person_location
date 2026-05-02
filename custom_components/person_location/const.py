"""const.py - Constants and Classes for person_location integration."""

# pyright: reportMissingImports=false
import asyncio
from datetime import timedelta
import logging

import voluptuous as vol

# from homeassistant.components.mobile_app.const import ATTR_VERTICAL_ACCURACY
# from homeassistant.components.waze_travel_time.const import REGIONS as WAZE_REGIONS
# from homeassistant.components.zone.const import DOMAIN as ZONE_DOMAIN
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
)

# from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

# from homeassistant.helpers.entity import Entity

# Our info:

DOMAIN = "person_location"
API_STATE_OBJECT = DOMAIN + "." + DOMAIN + "_integration"
INTEGRATION_NAME = "Person Location"
ISSUE_URL = "https://github.com/rodpayne/home-assistant_person_location/issues"

VERSION = "2026.05.02"

# Titles for the config entries:

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

# Attribute names in the target sensor's state attributes:

ATTR_ALTITUDE = "altitude"  # - altitude in meters if reported by trigger device
ATTR_AWAY_TIMESTAMP = (
    "away_time"  # - timestamp of when person left home (to detect STATE_EXTENDED_AWAY)
)
ATTR_BREAD_CRUMBS = "bread_crumbs"  # - the last several localities geocoded (e.g. "Home > Spanish Fork > Provo")
ATTR_COMPASS_BEARING = "compass_bearing"  # - compass bearing in degrees (0-360) calculated between last two locations (0 if home)
ATTR_DIRECTION = "direction"  # - subjective direction of travel ("home", "toward home", "away from home", "stationary", "far away")
ATTR_DRIVING_KM = (
    "driving_km"  # - driving distance in km calculated based on route home
)
ATTR_DRIVING_MILES = (
    "driving_miles"  # - driving distance in miles calculated based on route home
)
ATTR_DRIVING_MINUTES = (
    "driving_minutes"  # - driving time in minutes calculated based on route home
)
ATTR_LOCALITY = "locality"  # - locality associated with the location (e.g. "Provo")
ATTR_LOCATION_TIMESTAMP = (
    "location_time"  # - timestamp of the locationinfo in last processed trigger
)
ATTR_METERS_FROM_HOME = "meters_from_home"  # - straight-line distance in meters calculated between location and home
ATTR_MILES_FROM_HOME = "miles_from_home"  # - straight-line distance in miles calculated between location and home
ATTR_PERSON_NAME = "person_name"  # - the name used to identify the person (e.g. "Rod")
ATTR_REPORTED_STATE = "reported_state"  # - the state reported by trigger device, normalized to "home", "not_home", "just_left", etc.)
ATTR_ZONE = "zone"  # - the zone if reported by the trigger device (e.g. "home", "post_office", "walmart")
ATTR_SOURCE = "source"  # - the trigger device ID (e.g. device_tracker.rod_iphone_16)
ATTR_SPEED = "speed"  # - speed (if reported by trigger device) or calculated between last two updates (straight-line meters travelled)/(seconds elapsed)

# Additional attribute names examined in trigger sensors:

ATTR_LAST_LOCATED = "last_located"  # - timestamp of the last time the person was located (iCloud3) or trigger device reported a location

ATTR_GOOGLE_MAPS = "Google_Maps"
ATTR_MAPQUEST = "MapQuest"
ATTR_OPEN_STREET_MAP = "Open_Street_Map"
ATTR_RADAR = "Radar"

# States for the target sensor:
STATE_JUST_ARRIVED = "just_arrived"  # state for recent transition to home status
STATE_JUST_LEFT = "just_left"  # state for recent transition to away status
STATE_EXTENDED_AWAY = "extended_away"  # state for long-running away status

# Integration-specific states that imply "away"
AWAY_LIKE = {
    "not_home",
    "moving",
    "driving",
    "in_transit",
    "on_bus",
    "on_bicycle",
    "on_foot",
    "stationary",  # iOS app uses this outside zones
    "parked",
    "near_home",
    "in_zone",  # but not home zone
    STATE_JUST_LEFT,  # person_location-specific state for recent transition to away
    STATE_EXTENDED_AWAY,  # person_location-specific state for long-running away status
}

# Items under target.this_entity_info:

INFO_GEOCODE_COUNT = "geocode_count"
INFO_LOCALITY = "locality"
INFO_TRIGGER_COUNT = "trigger_count"
INFO_LOCATION_LATITUDE = "location_latitide"
INFO_LOCATION_LONGITUDE = "location_longitide"

# --------------------------------------------
#  Data (structural configuration parameters)
# --------------------------------------------

ATTR_GEOCODED = "geocoded"  # - indicates if the geolocation template sensors should be created ("Radar", "Google Maps", etc.)

CONF_CREATE_SENSORS = "create_sensors"
VALID_CREATE_SENSORS = [
    ATTR_ALTITUDE,
    ATTR_BREAD_CRUMBS,
    ATTR_DIRECTION,
    ATTR_DRIVING_KM,
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
# CONF_USE_WAZE = "use_waze"
# CONF_WAZE_REGION = "waze_region"

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
