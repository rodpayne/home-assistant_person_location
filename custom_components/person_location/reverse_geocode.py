"""reverse_geocode.py - The person_location integration reverse_geocode service."""

import asyncio
from datetime import datetime
import json
import logging
import math
import traceback

from jinja2 import Template
from pywaze.route_calculator import WazeRouteCalculator

from homeassistant.components.device_tracker.const import ATTR_SOURCE_TYPE
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.exceptions import ServiceNotFound, TemplateError
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.location import distance

from .const import (
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_GEOCODED,
    ATTR_GOOGLE_MAPS,
    ATTR_MAPQUEST,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    ATTR_OPEN_STREET_MAP,
    ATTR_PERSON_NAME,
    ATTR_RADAR,
    ATTR_REPORTED_STATE,
    ATTR_SOURCE,
    ATTR_SPEED,
    CONF_CREATE_SENSORS,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_GOOGLE_API_KEY,
    CONF_LANGUAGE,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
    CONF_REGION,
    DEFAULT_API_KEY_NOT_SET,
    DEFAULT_FRIENDLY_NAME_TEMPLATE,
    DEFAULT_LOCALITY_PRIORITY_OSM,
    DOMAIN,
    FAR_AWAY_METERS,
    IC3_STATIONARY_ZONE_PREFIX,
    INFO_GEOCODE_COUNT,
    INFO_LOCALITY,
    INFO_LOCATION_LATITUDE,
    INFO_LOCATION_LONGITUDE,
    INTEGRATION_ASYNCIO_LOCK,
    INTEGRATION_NAME,
    METERS_PER_MILE,
    MIN_DISTANCE_TRAVELLED_TO_GEOCODE,
    PERSON_LOCATION_INTEGRATION,
    SWITCH_GOOGLE_GEOCODING_API,
    SWITCH_MAPBOX_STATIC_IMAGE_API,
    SWITCH_MAPQUEST_GEOCODING_API,
    SWITCH_OSM_NOMINATIM_GEOCODING_API,
    SWITCH_RADAR_GEOCODING_API,
    TARGET_ASYNCIO_LOCK,
    THROTTLE_INTERVAL,
    ZONE_DOMAIN,
    get_home_coordinates,
)
from .helpers.api import (
    async_get_google_maps_geocoding,
    async_get_mapquest_reverse_geocoding,
    async_get_open_street_map_reverse_geocoding,
    async_get_radar_reverse_geocoding,
)
from .helpers.duration_distance import (
    update_driving_miles_and_minutes,
)
from .sensor import (
    PERSON_LOCATION_TARGET,
    create_and_register_template_sensor,
    get_target_entity,
)
from .switch import (
    is_provider_enabled,
    # record_api_error,
    # record_api_request,
)

_LOGGER = logging.getLogger(__name__)


def calculate_initial_compass_bearing(pointA: tuple, pointB: tuple) -> float:
    """
    Calculate the bearing between two points.

    The formulae used is the following:
        θ = atan2(sin(Δlong).cos(lat2),
                cos(lat1).sin(lat2) − sin(lat1).cos(lat2).cos(Δlong))
    :Parameters:
    - `pointA: The tuple representing the latitude/longitude for the
        first point. Latitude and longitude must be in decimal degrees
    - `pointB: The tuple representing the latitude/longitude for the
        second point. Latitude and longitude must be in decimal degrees
    :Returns:
    The bearing in degrees
    :Returns Type:
    float

    From https://gist.github.com/jeromer/2005586.
    """
    if (type(pointA) is not tuple) or (type(pointB) is not tuple):
        raise TypeError("Only tuples are supported as arguments")

    lat1 = math.radians(pointA[0])
    lat2 = math.radians(pointB[0])

    diffLong = math.radians(pointB[1] - pointA[1])

    x = math.sin(diffLong) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (
        math.sin(lat1) * math.cos(lat2) * math.cos(diffLong)
    )

    initial_bearing = math.atan2(x, y)

    # Now we have the initial bearing but math.atan2 return values
    #   from -180° to + 180° which is not what we want for a compass bearing.
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360

    return compass_bearing


def is_json(myjson: str) -> bool:
    """Determine whether myjson has a volid value."""
    try:
        json.loads(myjson)
    except ValueError:
        return False
    return True


def shorten_to_last_255(s: str) -> str:
    """
    Shorten a string to 255 characters or less.

        - If len(s) <= 255: return s unchanged.
        - If len(s) > 251:
            1) Take the last 251 characters.
            2) In that tail, remove up to & including the first ">" (if present).
            3) Prefix with "... " so the final string is <= 255 chars.
    """
    if s is None:
        return ""

    if len(s) <= 255:
        return s

    marker = "> "
    tail = s[-251:]  # 1) take last 251 chars first

    idx = tail.find(marker)  # 2) look for marker in the tail
    if idx != -1:
        tail = tail[idx + len(marker) :]

    # 3) add prefix; length will be <= 255 because tail is <= 251
    return "... " + tail


def setup_reverse_geocode(pli: PERSON_LOCATION_INTEGRATION) -> bool:
    """Initialize the reverse_geocode service."""
    # ---------------------------------------------------------------
    # "Global" variables for the following geocode routines
    # ---------------------------------------------------------------
    #   handle_reverse_geocode
    #       async_call_google_maps_geocoding
    #       async_call_mapquest_reverse_geocoding
    #       async_call_open_street_map_reverse_geocoding
    #       async_call_radar_reverse_geocoding
    # ---------------------------------------------------------------

    home_latitude: str = None
    home_longitude: str = None
    new_latitude: str = None
    new_longitude: str = None
    new_location_time: str = None
    new_locality: str = None
    waze_country_code: str = None

    # ------- Google Maps -------------------------------------------

    async def async_call_google_maps_geocoding(target: PERSON_LOCATION_TARGET) -> None:
        """Call the Google Maps Reverse Geocoding API."""
        """https://developers.google.com/maps/documentation/geocoding/overview?hl=en_US#ReverseGeocoding"""

        # nonlocal new_latitude
        # nonlocal new_longitude
        # nonlocal new_location_time
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

        google_decoded = {}
        google_response = await async_get_google_maps_geocoding(
            pli.hass,
            pli.configuration[CONF_GOOGLE_API_KEY],
            new_latitude,
            new_longitude,
        )
        if not google_response.get("ok"):
            _LOGGER.warning(
                "[async_call_google_maps_geocoding] %s",
                google_response.get("error"),
            )
            return

        google_decoded = google_response.get("data")

        if "results" in google_decoded:
            if "formatted_address" in google_decoded["results"][0]:
                formatted_address = google_decoded["results"][0]["formatted_address"]
                _LOGGER.debug(
                    "("
                    + entity_id
                    + ") Google formatted_address = "
                    + formatted_address
                )
                target._attr_extra_state_attributes[ATTR_GOOGLE_MAPS] = (
                    formatted_address
                )
            for component in google_decoded["results"][0]["address_components"]:
                if "locality" in component["types"]:
                    new_locality = component["long_name"]
                    _LOGGER.debug(
                        "(" + entity_id + ") Google new_locality = " + new_locality
                    )
                elif (new_locality == "?") and (
                    "administrative_area_level_2" in component["types"]
                ):  # Fall back to county
                    new_locality = component["long_name"]
                elif (new_locality == "?") and (
                    "administrative_area_level_1" in component["types"]
                ):  # Fall back to state
                    new_locality = component["long_name"]

                if "country" in component["types"]:
                    waze_country_code = component["short_name"].upper()
                    _LOGGER.debug(
                        "("
                        + entity_id
                        + ") Google waze_country_code = "
                        + waze_country_code
                    )

            google_attribution = '"powered by Google"'
            target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
                google_attribution + "; "
            )

            if ATTR_GEOCODED in pli.configuration[CONF_CREATE_SENSORS]:
                attrs = {
                    ATTR_LATITUDE: new_latitude,
                    ATTR_LONGITUDE: new_longitude,
                    ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                        ATTR_SOURCE_TYPE
                    ),
                    ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                        ATTR_GPS_ACCURACY
                    ),
                    "icon": target._attr_extra_state_attributes.get("icon"),
                    "locality": new_locality,
                    "location_time": new_location_time.strftime("%Y-%m-%d %H:%M:%S"),
                    ATTR_PERSON_NAME: target._person_name,
                    ATTR_ATTRIBUTION: google_attribution,
                }
                create_and_register_template_sensor(
                    pli.hass,
                    target,
                    ATTR_GOOGLE_MAPS,
                    formatted_address[:250],
                    attrs,
                )

    # ------- Mapquest ----------------------------------------------

    async def async_call_mapquest_reverse_geocoding(
        target: PERSON_LOCATION_TARGET,
    ) -> None:
        """Call the Mapquest Reverse Geocoding API."""
        """https://developer.mapquest.com/documentation/geocoding-api/reverse/get/"""

        # nonlocal new_latitude
        # nonlocal new_longitude
        # nonlocal new_location_time
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

        mapquest_decoded = {}
        mapquest_response = await async_get_mapquest_reverse_geocoding(
            pli.hass,
            pli.configuration[CONF_MAPQUEST_API_KEY],
            new_latitude,
            new_longitude,
        )
        if not mapquest_response.get("ok"):
            _LOGGER.warning(
                "[async_call_open_street_map_reverse_geocoding] %s",
                mapquest_response.get("error"),
            )
            return

        mapquest_decoded = mapquest_response.get("data")

        mapquest_statuscode = mapquest_decoded["info"]["statuscode"]
        if mapquest_statuscode != 0:
            _LOGGER.error(
                "("
                + entity_id
                + ") mapquest_statuscode = "
                + str(mapquest_statuscode)
                + " messages = "
                + mapquest_decoded["info"]["messages"]
            )
        else:
            if (
                "results" in mapquest_decoded
                and "locations" in mapquest_decoded["results"][0]
            ):
                mapquest_location = mapquest_decoded["results"][0]["locations"][0]

                formatted_address = ""
                if "street" in mapquest_location:
                    formatted_address += mapquest_location["street"] + ", "
                if "adminArea5" in mapquest_location:  # Like city
                    new_locality = mapquest_location["adminArea5"]
                    formatted_address += new_locality + ", "
                elif (
                    "adminArea4" in mapquest_location
                    and "adminArea4Type" in mapquest_location
                ):  # Like county
                    new_locality = (
                        mapquest_location["adminArea4"]
                        + " "
                        + mapquest_location["adminArea4Type"]
                    )
                    formatted_address += new_locality + ", "
                if "adminArea3" in mapquest_location:  # Like state
                    formatted_address += mapquest_location["adminArea3"] + " "
                if "postalCode" in mapquest_location:  # zip
                    formatted_address += mapquest_location["postalCode"] + " "
                if (
                    "adminArea1" in mapquest_location
                    and mapquest_location["adminArea1"] != "US"
                ):  # Like country
                    formatted_address += mapquest_location["adminArea1"]

                _LOGGER.debug(
                    "("
                    + entity_id
                    + ") mapquest formatted_address = "
                    + formatted_address
                )

                if "adminArea1" in mapquest_location:
                    waze_country_code = mapquest_location["adminArea1"]
                    _LOGGER.debug(
                        "("
                        + entity_id
                        + ") mapquest waze_country_code = "
                        + waze_country_code
                    )

                target._attr_extra_state_attributes[ATTR_MAPQUEST] = formatted_address

                _LOGGER.debug(
                    "(" + entity_id + ") mapquest new_locality = " + new_locality
                )

                mapquest_attribution = (
                    '"' + mapquest_decoded["info"]["copyright"]["text"] + '"'
                )
                target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
                    mapquest_attribution + "; "
                )

                if ATTR_GEOCODED in pli.configuration[CONF_CREATE_SENSORS]:
                    attrs = {
                        ATTR_LATITUDE: new_latitude,
                        ATTR_LONGITUDE: new_longitude,
                        ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                            ATTR_SOURCE_TYPE
                        ),
                        ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                            ATTR_GPS_ACCURACY
                        ),
                        "icon": target._attr_extra_state_attributes.get("icon"),
                        "locality": new_locality,
                        "location_time": new_location_time.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        ATTR_PERSON_NAME: target._person_name,
                        ATTR_ATTRIBUTION: mapquest_attribution,
                    }
                    create_and_register_template_sensor(
                        pli.hass,
                        target,
                        ATTR_MAPQUEST,
                        formatted_address[:250],
                        attrs,
                    )

    # ------- Open Street Map ---------------------------------------

    async def async_call_open_street_map_reverse_geocoding(
        target: PERSON_LOCATION_TARGET,
    ) -> None:
        """Call the Nominatim Reverse Geocoding (OpenStreetMap) API."""
        # nonlocal new_latitude
        # nonlocal new_longitude
        # nonlocal new_location_time
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

        osm_decoded = {}
        osm_response = await async_get_open_street_map_reverse_geocoding(
            pli.hass, pli.configuration[CONF_OSM_API_KEY], new_latitude, new_longitude
        )
        if not osm_response.get("ok"):
            _LOGGER.warning(
                "[async_call_open_street_map_reverse_geocoding] %s",
                osm_response.get("error"),
            )
            return

        osm_decoded = osm_response["data"]

        # Save home_state and home_countryCode for later comparisons
        if new_latitude == home_latitude and new_longitude == home_longitude:
            if (
                "country_code" in osm_decoded["address"]
                and "state" in osm_decoded["address"]
            ):
                home_country_code = osm_decoded["address"]["country_code"].upper()
                home_state = osm_decoded["address"]["state"]
                _LOGGER.debug(
                    "(%s) OSM home_state = %s, home_country_code = %s",
                    entity_id,
                    home_state,
                    home_country_code,
                )

        for key in DEFAULT_LOCALITY_PRIORITY_OSM:
            if key in osm_decoded["address"]:
                new_locality = osm_decoded["address"][key]
                break
        _LOGGER.debug("(" + entity_id + ") OSM new_locality = " + new_locality)

        if "country_code" in osm_decoded["address"]:
            waze_country_code = osm_decoded["address"]["country_code"].upper()
            _LOGGER.debug(
                "(" + entity_id + ") OSM waze_country_code = " + waze_country_code
            )

        if "display_name" in osm_decoded:
            display_name = osm_decoded["display_name"]
        else:
            display_name = new_locality
        _LOGGER.debug("(" + entity_id + ") OSM display_name = " + display_name)

        target._attr_extra_state_attributes[ATTR_OPEN_STREET_MAP] = (
            display_name.replace(", ", " ")
        )

        if "licence" in osm_decoded:
            osm_attribution = '"' + osm_decoded["licence"] + '"'
            target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
                osm_attribution + "; "
            )

        else:
            osm_attribution = ""

        if ATTR_GEOCODED in pli.configuration[CONF_CREATE_SENSORS]:
            # Create the template sensor entity
            attrs = {
                ATTR_LATITUDE: new_latitude,
                ATTR_LONGITUDE: new_longitude,
                ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                    ATTR_SOURCE_TYPE
                ),
                ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                    ATTR_GPS_ACCURACY
                ),
                "icon": target._attr_extra_state_attributes.get("icon"),
                "locality": new_locality,
                "location_time": new_location_time.strftime("%Y-%m-%d %H:%M:%S"),
                ATTR_PERSON_NAME: target._person_name,
                ATTR_ATTRIBUTION: osm_attribution,
            }
            create_and_register_template_sensor(
                pli.hass,
                target,
                ATTR_OPEN_STREET_MAP,
                display_name[:250],
                attrs,
            )

    # ------- Radar -------------------------------------------------

    async def async_call_radar_reverse_geocoding(
        target: PERSON_LOCATION_TARGET,
    ) -> None:
        """Call the Radar Reverse Geocoding API."""
        # nonlocal new_latitude
        # nonlocal new_longitude
        # nonlocal new_location_time
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

        radar_decoded = {}
        radar_response = await async_get_radar_reverse_geocoding(
            pli.hass, pli.configuration[CONF_RADAR_API_KEY], new_latitude, new_longitude
        )
        if not radar_response.get("ok"):
            _LOGGER.warning(
                "[async_call_radar_reverse_geocoding] %s", radar_response.get("error")
            )
            return

        # _LOGGER.debug(radar_response)
        radar_decoded = radar_response["data"]

        if "city" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["city"]
        elif "town" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["town"]
        elif "village" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["village"]
        elif "municipality" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["municipality"]
        elif "county" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["county"]
        elif "state" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["state"]
        elif "country" in radar_decoded["addresses"][0]:
            new_locality = radar_decoded["addresses"][0]["country"]
        _LOGGER.debug("(" + entity_id + ") Radar new_locality = " + new_locality)

        if "countryCode" in radar_decoded["addresses"][0]:
            waze_country_code = radar_decoded["addresses"][0]["countryCode"].upper()
            _LOGGER.debug(
                "(" + entity_id + ") Radar waze_country_code = " + waze_country_code
            )

        if "formattedAddress" in radar_decoded["addresses"][0]:
            formatted_address = radar_decoded["addresses"][0]["formattedAddress"]
        elif "addressLabel" in radar_decoded["addresses"][0]:
            formatted_address = radar_decoded["addresses"][0]["addressLabel"]
        else:
            formatted_address = new_locality
        _LOGGER.debug(
            "(" + entity_id + ") RADAR formatted_address = " + formatted_address
        )

        target._attr_extra_state_attributes[ATTR_RADAR] = formatted_address

        radar_attribution = '"Powered by Radar"'
        target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
            radar_attribution + "; "
        )

        if ATTR_GEOCODED in pli.configuration[CONF_CREATE_SENSORS]:
            # Create the template sensor entity
            attrs = {
                ATTR_LATITUDE: new_latitude,
                ATTR_LONGITUDE: new_longitude,
                ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                    ATTR_SOURCE_TYPE
                ),
                ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                    ATTR_GPS_ACCURACY
                ),
                "icon": target._attr_extra_state_attributes.get("icon"),
                "locality": new_locality,
                "location_time": new_location_time.strftime("%Y-%m-%d %H:%M:%S"),
                ATTR_PERSON_NAME: target._person_name,
                ATTR_ATTRIBUTION: radar_attribution,
            }
            create_and_register_template_sensor(
                pli.hass,
                target,
                ATTR_RADAR,
                formatted_address[:250],
                attrs,
            )

    async def handle_reverse_geocode(call: dict) -> bool:
        """
        Handle the reverse_geocode service.

        Input:
            - Parameters for the call:
                entity_id
                friendly_name_template (optional)
                force_update (optional)
            - Attributes of entity_id:
                - latitude
                - longitude
                - location_time (optional)
        Output:
            - determine <new_locality> for friendly_name
            - full location from Radar, Google_Maps, MapQuest, and/or Open_Street_Map
            - calculate other location-based statistics, such as distance_from_home
            - add to bread_crumbs as locality changes
            - create/update additional sensors if requested
            - friendly_name: something like "Rod (i.e. Rod's watch) is at Drew's"
        """
        nonlocal new_latitude
        nonlocal new_longitude
        nonlocal new_location_time
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = call.data.get(CONF_ENTITY_ID, "NONE")
        template = call.data.get(CONF_FRIENDLY_NAME_TEMPLATE, "NONE")
        force_update = call.data.get("force_update", False)

        if entity_id == "NONE":
            {
                _LOGGER.warning(
                    "%s is required in call of %s.reverse_geocode service.",
                    CONF_ENTITY_ID,
                    DOMAIN,
                )
            }
            return False

        _LOGGER.debug(
            "(%s) === Start === %s = %s; %s = %s",
            entity_id,
            CONF_FRIENDLY_NAME_TEMPLATE,
            template,
            "force_update",
            force_update,
        )

        async with INTEGRATION_ASYNCIO_LOCK:
            """Lock while updating the pli(API_STATE_OBJECT)."""
            _LOGGER.debug("INTEGRATION_ASYNCIO_LOCK obtained")

            try:
                currentApiTime = datetime.now()

                if pli.state.lower() != STATE_ON:
                    """Allow API calls to be paused."""
                    pli.attributes["api_calls_skipped"] += 1
                    _LOGGER.debug(
                        "(%s) api_calls_skipped = %d",
                        entity_id,
                        pli.attributes["api_calls_skipped"],
                    )
                else:
                    """Throttle the API calls so that we don't exceed policy."""
                    wait_time = (
                        pli.attributes["api_last_updated"]
                        - currentApiTime
                        + THROTTLE_INTERVAL
                    ).total_seconds()
                    if wait_time > 0:
                        pli.attributes["api_calls_throttled"] += 1
                        _LOGGER.debug(
                            "(%s) wait_time = %05.3f; api_calls_throttled = %d",
                            entity_id,
                            wait_time,
                            pli.attributes["api_calls_throttled"],
                        )
                        await asyncio.sleep(wait_time)
                        currentApiTime = datetime.now()

                    # Record the integration attributes in the API_STATE_OBJECT:

                    pli.attributes["api_last_updated"] = currentApiTime

                    pli.attributes["api_calls_requested"] += 1

                    counter_attribute = f"{entity_id} calls"
                    if counter_attribute in pli.attributes:
                        new_count = pli.attributes[counter_attribute] + 1
                    else:
                        new_count = 1
                    pli.attributes[counter_attribute] = new_count
                    _LOGGER.debug(
                        "("
                        + entity_id
                        + ") "
                        + counter_attribute
                        + " = "
                        + str(new_count)
                    )

                    # Handle the service call, updating the target(entity_id):

                    async with TARGET_ASYNCIO_LOCK:
                        """Lock while updating the target(entity_id)."""
                        _LOGGER.debug("TARGET_ASYNCIO_LOCK obtained")

                        target = get_target_entity(pli, entity_id)
                        if not target:
                            _LOGGER.warning("No target sensor found for %s", entity_id)
                            return False

                        home_latitude, home_longitude = get_home_coordinates(pli.hass)

                        # Reset attribution before updating
                        target._attr_extra_state_attributes[ATTR_ATTRIBUTION] = ""

                        if ATTR_LATITUDE in target._attr_extra_state_attributes:
                            new_latitude = target._attr_extra_state_attributes[
                                ATTR_LATITUDE
                            ]
                        else:
                            new_latitude = "None"
                        if ATTR_LONGITUDE in target._attr_extra_state_attributes:
                            new_longitude = target._attr_extra_state_attributes[
                                ATTR_LONGITUDE
                            ]
                        else:
                            new_longitude = "None"

                        if INFO_LOCATION_LATITUDE in target.this_entity_info:
                            old_latitude = target.this_entity_info[
                                INFO_LOCATION_LATITUDE
                            ]
                        else:
                            old_latitude = "None"
                        if INFO_LOCATION_LONGITUDE in target.this_entity_info:
                            old_longitude = target.this_entity_info[
                                INFO_LOCATION_LONGITUDE
                            ]
                        else:
                            old_longitude = "None"

                        if (
                            new_latitude != "None"
                            and new_longitude != "None"
                            and old_latitude != "None"
                            and old_longitude != "None"
                        ):
                            distance_traveled = round(
                                distance(
                                    float(new_latitude),
                                    float(new_longitude),
                                    float(old_latitude),
                                    float(old_longitude),
                                ),
                                3,
                            )
                            if home_latitude and home_longitude:
                                old_distance_from_home = round(
                                    distance(
                                        float(old_latitude),
                                        float(old_longitude),
                                        float(home_latitude),
                                        float(home_longitude),
                                    ),
                                    3,
                                )
                            else:
                                old_distance_from_home = 0

                            compass_bearing = round(
                                calculate_initial_compass_bearing(
                                    (float(old_latitude), float(old_longitude)),
                                    (float(new_latitude), float(new_longitude)),
                                ),
                                1,
                            )

                            _LOGGER.debug(
                                "("
                                + entity_id
                                + ") distance_traveled = "
                                + str(distance_traveled)
                                + "; compass_bearing = "
                                + str(compass_bearing)
                            )
                        else:
                            distance_traveled = 0
                            old_distance_from_home = 0
                            compass_bearing = 0

                        target._attr_extra_state_attributes[ATTR_COMPASS_BEARING] = (
                            compass_bearing
                        )

                        if new_latitude == "None" or new_longitude == "None":
                            _LOGGER.debug(
                                "("
                                + entity_id
                                + ") Skipping geocoding because coordinates are missing"
                            )
                        elif (
                            distance_traveled < MIN_DISTANCE_TRAVELLED_TO_GEOCODE
                            and old_latitude != "None"
                            and old_longitude != "None"
                            and not force_update
                        ):
                            _LOGGER.debug(
                                "("
                                + entity_id
                                + ") Skipping geocoding because distance_traveled < "
                                + str(MIN_DISTANCE_TRAVELLED_TO_GEOCODE)
                            )
                        else:
                            new_locality = "?"

                            if "location_time" in target._attr_extra_state_attributes:
                                new_location_time = datetime.strptime(
                                    str(
                                        target._attr_extra_state_attributes[
                                            "location_time"
                                        ]
                                    ),
                                    "%Y-%m-%d %H:%M:%S.%f",
                                )
                                _LOGGER.debug(
                                    "("
                                    + entity_id
                                    + ") new_location_time = "
                                    + str(new_location_time)
                                )
                            else:
                                new_location_time = currentApiTime

                            if (
                                "reverse_geocode_location_time"
                                in target.this_entity_info
                            ):
                                old_location_time = target.this_entity_info[
                                    "reverse_geocode_location_time"
                                ]
                                _LOGGER.debug(
                                    "("
                                    + entity_id
                                    + ") old_location_time = "
                                    + str(old_location_time)
                                )
                            else:
                                old_location_time = new_location_time

                            elapsed_seconds = (
                                new_location_time - old_location_time
                            ).total_seconds()
                            _LOGGER.debug(
                                "("
                                + entity_id
                                + ") elapsed_seconds = "
                                + str(elapsed_seconds)
                            )

                            if elapsed_seconds > 0:
                                speed_during_interval = (
                                    distance_traveled / elapsed_seconds
                                )
                                _LOGGER.debug(
                                    "("
                                    + entity_id
                                    + ") speed_during_interval = "
                                    + str(speed_during_interval)
                                    + " meters/sec"
                                )
                            else:
                                speed_during_interval = 0
                            target._attr_extra_state_attributes[ATTR_SPEED] = round(
                                speed_during_interval, 1
                            )

                            if (
                                ATTR_REPORTED_STATE
                                in target._attr_extra_state_attributes
                                and target._attr_extra_state_attributes[
                                    ATTR_REPORTED_STATE
                                ].lower()
                                == "home"
                            ):
                                distance_from_home = 0  # clamp it down since "Home" is not a single point
                            elif (
                                new_latitude != "None"
                                and new_longitude != "None"
                                and home_latitude
                                and home_longitude
                            ):
                                distance_from_home = round(
                                    distance(
                                        float(new_latitude),
                                        float(new_longitude),
                                        home_latitude,
                                        home_longitude,
                                    ),
                                    3,
                                )
                            else:
                                distance_from_home = (
                                    0  # could only happen if we don't have coordinates
                                )
                            _LOGGER.debug(
                                "("
                                + entity_id
                                + ") meters_from_home = "
                                + str(distance_from_home)
                            )
                            target._attr_extra_state_attributes[
                                ATTR_METERS_FROM_HOME
                            ] = round(distance_from_home, 1)
                            target._attr_extra_state_attributes[
                                ATTR_MILES_FROM_HOME
                            ] = round(distance_from_home / METERS_PER_MILE, 1)

                            if distance_from_home >= FAR_AWAY_METERS:
                                direction = "far away"
                            elif speed_during_interval <= 0.5:
                                direction = "stationary"
                            elif old_distance_from_home > distance_from_home:
                                direction = "toward home"
                            elif old_distance_from_home < distance_from_home:
                                direction = "away from home"
                            else:
                                direction = "stationary"
                            _LOGGER.debug(
                                "(" + entity_id + ") direction = " + direction
                            )
                            target._attr_extra_state_attributes["direction"] = direction

                            # Default the waze country code from Google region config
                            waze_country_code = pli.configuration["region"].upper()

                            # ------- Radar -------------------------------------------

                            if is_provider_enabled(
                                pli.hass, SWITCH_RADAR_GEOCODING_API
                            ):
                                await async_call_radar_reverse_geocoding(target)
                            else:
                                # Remove the soon-to-be outdated information.
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_RADAR, None
                                )
                                if previous:
                                    # TODO: remove the geocode template sensor if it exists
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_RADAR"
                                    )

                            # ------- Open Street Map ---------------------------------

                            if is_provider_enabled(
                                pli.hass, SWITCH_OSM_NOMINATIM_GEOCODING_API
                            ):
                                await async_call_open_street_map_reverse_geocoding(
                                    target
                                )
                            else:
                                # Remove the soon-to-be outdated information.
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_OPEN_STREET_MAP, None
                                )
                                if previous:
                                    # TODO: remove the geocode template sensor if it exists
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_OPEN_STREET_MAP"
                                    )

                            # ------- Google Maps -------------------------------------

                            if is_provider_enabled(
                                pli.hass, SWITCH_GOOGLE_GEOCODING_API
                            ):
                                await async_call_google_maps_geocoding(target)
                            else:
                                # Remove the soon-to-be outdated information.
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_GOOGLE_MAPS, None
                                )
                                if previous:
                                    # TODO: remove the geocode template sensor if it exists
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_GOOGLE_MAPS"
                                    )

                            # ------- Mapquest ----------------------------------------

                            if is_provider_enabled(
                                pli.hass, SWITCH_MAPQUEST_GEOCODING_API
                            ):
                                await async_call_mapquest_reverse_geocoding(target)
                            else:
                                # Remove the soon-to-be outdated information.
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_MAPQUEST, None
                                )
                                if previous:
                                    # TODO: remove the geocode template sensor if it exists
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_MAPQUEST"
                                    )

                            # ------- All ---------------------------------------------

                            target._attr_extra_state_attributes["locality"] = (
                                new_locality
                            )
                            target.this_entity_info[INFO_LOCALITY] = new_locality
                            target.this_entity_info[INFO_GEOCODE_COUNT] += 1
                            target.this_entity_info[INFO_LOCATION_LATITUDE] = (
                                new_latitude
                            )
                            target.this_entity_info[INFO_LOCATION_LONGITUDE] = (
                                new_longitude
                            )
                            target.this_entity_info["reverse_geocode_location_time"] = (
                                new_location_time
                            )

                            # Call WazeRouteCalculator or alternate:

                            await update_driving_miles_and_minutes(
                                pli,
                                target,
                                new_latitude,
                                new_longitude,
                                waze_country_code,
                            )

                        # Determine friendly_name_location and new_bread_crumb:

                        if target._attr_extra_state_attributes[
                            ATTR_REPORTED_STATE
                        ].lower() in [
                            STATE_HOME,
                            STATE_ON,
                        ]:
                            new_bread_crumb = "Home"
                            friendly_name_location = "is Home"
                        elif target._attr_extra_state_attributes[
                            ATTR_REPORTED_STATE
                        ].lower() in [
                            "away",
                            STATE_NOT_HOME,
                            STATE_OFF,
                        ]:
                            new_bread_crumb = "Away"
                            friendly_name_location = "is Away"
                        else:
                            new_bread_crumb = target._attr_extra_state_attributes[
                                ATTR_REPORTED_STATE
                            ]
                            friendly_name_location = f"is at {new_bread_crumb}"

                        if "zone" in target._attr_extra_state_attributes:
                            reportedZone = target._attr_extra_state_attributes["zone"]
                            zoneStateObject = pli.hass.states.get(
                                ZONE_DOMAIN + "." + reportedZone
                            )
                            if (
                                zoneStateObject is not None
                                and not reportedZone.startswith(
                                    IC3_STATIONARY_ZONE_PREFIX
                                )
                            ):
                                zoneAttributesObject = zoneStateObject.attributes.copy()
                                if "friendly_name" in zoneAttributesObject:
                                    new_bread_crumb = zoneAttributesObject[
                                        "friendly_name"
                                    ]
                                    friendly_name_location = f"is at {new_bread_crumb}"

                        if (
                            new_bread_crumb == "Away"
                            and "locality" in target._attr_extra_state_attributes
                        ):
                            new_bread_crumb = target._attr_extra_state_attributes[
                                "locality"
                            ]
                            friendly_name_location = f"is in {new_bread_crumb}"

                        _LOGGER.debug(
                            "(%s) friendly_name_location = %s; new_bread_crumb = %s",
                            target.entity_id,
                            friendly_name_location,
                            new_bread_crumb,
                        )

                        # Append location to bread_crumbs attribute:

                        if ATTR_BREAD_CRUMBS in target._attr_extra_state_attributes:
                            old_bread_crumbs = target._attr_extra_state_attributes[
                                ATTR_BREAD_CRUMBS
                            ]
                            if not old_bread_crumbs.endswith(new_bread_crumb):
                                target._attr_extra_state_attributes[
                                    ATTR_BREAD_CRUMBS
                                ] = shorten_to_last_255(
                                    old_bread_crumbs + "> " + new_bread_crumb
                                )
                        else:
                            target._attr_extra_state_attributes[ATTR_BREAD_CRUMBS] = (
                                new_bread_crumb
                            )

                        if template != "NONE":
                            # Format friendly_name attribute using the supplied friendly_name_template:

                            if (
                                ATTR_SOURCE in target._attr_extra_state_attributes
                                and "."
                                in target._attr_extra_state_attributes[ATTR_SOURCE]
                            ):
                                sourceEntity = target._attr_extra_state_attributes[
                                    ATTR_SOURCE
                                ]
                                sourceObject = pli.hass.states.get(sourceEntity)
                                if (
                                    sourceObject is not None
                                    and ATTR_SOURCE in sourceObject.attributes
                                    and "."
                                    in target._attr_extra_state_attributes[ATTR_SOURCE]
                                ):
                                    # Find the source for a person entity:
                                    sourceEntity = sourceObject.attributes[ATTR_SOURCE]
                                    sourceObject = pli.hass.states.get(sourceEntity)
                            else:
                                sourceEntity = target.entity_id
                                sourceObject = pli.hass.states.get(sourceEntity)

                            friendly_name_variables = {
                                "friendly_name_location": friendly_name_location,
                                "person_name": target._attr_extra_state_attributes[
                                    "person_name"
                                ],
                                "source": {
                                    "entity_id": sourceEntity,
                                    "state": sourceObject.state,
                                    "attributes": sourceObject.attributes,
                                },
                                "target": {
                                    "entity_id": target.entity_id,
                                    "state": target.state,
                                    "attributes": target._attr_extra_state_attributes,
                                },
                            }
                            # _LOGGER.debug(f"friendly_name_variables = {friendly_name_variables}")

                            try:
                                new_friendly_name = (
                                    Template(
                                        pli.configuration.get(
                                            CONF_FRIENDLY_NAME_TEMPLATE,
                                            DEFAULT_FRIENDLY_NAME_TEMPLATE,
                                        )
                                    )
                                    .render(**friendly_name_variables)
                                    .replace("()", "")
                                    .replace("  ", " ")
                                )
                                target._attr_extra_state_attributes["friendly_name"] = (
                                    new_friendly_name
                                )
                                target._attr_name = new_friendly_name
                                _LOGGER.debug(
                                    f"new_friendly_name = {new_friendly_name}"
                                )

                            except TemplateError as err:
                                _LOGGER.error(
                                    "Error parsing friendly_name_template: %s", err
                                )

                            target.set_state()

                            target.make_template_sensors()

                        _LOGGER.debug("TARGET_ASYNCIO_LOCK release...")
            except Exception as e:
                _LOGGER.error(
                    "(%s) Exception %s: %s", entity_id, type(e).__name__, str(e)
                )
                _LOGGER.debug(traceback.format_exc())
                pli.attributes["api_exception_count"] += 1

            pli.set_state()
            _LOGGER.debug("INTEGRATION_ASYNCIO_LOCK release...")
        _LOGGER.debug("(%s) === Return ===", entity_id)
        return True

    pli.hass.services.async_register(DOMAIN, "reverse_geocode", handle_reverse_geocode)
    return True
