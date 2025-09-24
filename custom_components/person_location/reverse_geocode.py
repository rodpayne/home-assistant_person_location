"""The person_location integration reverse_geocode service."""

import asyncio
import json
import logging
import math
import time
import traceback
from datetime import datetime

import httpx
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
from homeassistant.exceptions import ServiceNotFound,TemplateError
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.location import distance
from jinja2 import Template
from pywaze.route_calculator import WazeRouteCalculator

from .const import (
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_GEOCODED,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
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
    DOMAIN,
    FAR_AWAY_METERS,
    IC3_STATIONARY_ZONE_PREFIX,
    INTEGRATION_LOCK,
    INTEGRATION_NAME,
    METERS_PER_KM,
    METERS_PER_MILE,
    MIN_DISTANCE_TRAVELLED_TO_GEOCODE,
    PERSON_LOCATION_ENTITY,
    TARGET_LOCK,
    THROTTLE_INTERVAL,
    WAZE_MIN_METERS_FROM_HOME,
    WAZE_REGIONS,
    ZONE_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def is_json(myjson):
    try:
        json.loads(myjson)
    except ValueError:
        return False
    return True


def setup_reverse_geocode(pli):
    """Initialize reverse_geocode service."""

    def calculate_initial_compass_bearing(pointA, pointB):
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
        if (type(pointA) != tuple) or (type(pointB) != tuple):
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
        # from -180° to + 180° which is not what we want for a compass bearing.
        initial_bearing = math.degrees(initial_bearing)
        compass_bearing = (initial_bearing + 360) % 360

        return compass_bearing

    def _get_waze_region(country_code: str) -> str:
        country_code = country_code.lower()
        if country_code in ("us", "ca", "mx"):
            return "us"
        if country_code in WAZE_REGIONS:
            return country_code
        return "eu"

    def _get_waze_driving_miles_and_minutes(
        target,
        new_latitude,
        new_longitude,
        waze_country_code,
    ):
        """ 
        Updates target.attributes:
            ATTR_DRIVING_MILES
            ATTR_DRIVING_MINUTES
            ATTR_ATTRIBUTION

        May update pli.attributes:
            "waze_error_count"
        """

        entity_id = target.entity_id
        if not pli.configuration["use_waze"]:
            return

        # If we’re already “home,” skip routing
        if target.attributes[ATTR_METERS_FROM_HOME] < WAZE_MIN_METERS_FROM_HOME:
            target.attributes[ATTR_DRIVING_MILES] = target.attributes[ATTR_MILES_FROM_HOME]
            target.attributes[ATTR_DRIVING_MINUTES] = "0"
            return

        from_location = f"{new_latitude},{new_longitude}"
        to_location = (
            f"{pli.attributes['home_latitude']},"
            f"{pli.attributes['home_longitude']}"
        )
        waze_region = _get_waze_region(waze_country_code)

        _LOGGER.debug("from_location: " + from_location)
        _LOGGER.debug("to_location: " + to_location)
        _LOGGER.debug("waze_region: " + waze_region)

        # First attempt: HA-managed service
        try:
            if not pli.hass.services.has_service("waze_travel_time", "get_travel_times"):
                raise ServiceNotFound("waze_travel_time", "get_travel_times")

            service_coro = pli.hass.services.async_call(
                "waze_travel_time",
                "get_travel_times",
                {
                    "origin": from_location,
                    "destination": to_location,
                    "region": waze_region,
                },
                blocking=True,
                return_response=True,
            )
            future = asyncio.run_coroutine_threadsafe(service_coro, pli.hass.loop)
            data = future.result()
            routes = data.get("routes", [])
            if not routes:
                raise ValueError("No routes from HA service")

            # pick first or apply your street‐name filter here
            best = routes[0]
            duration = best["duration"]
            distance_km = best["distance"]

        except Exception as service_err:
            _LOGGER.debug(
                "(%s) Waze service failed (%s), falling back to pywaze",
                entity_id,
                type(service_err).__name__,
            )
            # Fallback: direct pywaze call
            try:
                # pywaze expects an aiohttp client session
                client = WazeRouteCalculator(
                    region=waze_region.upper(),
                    client=get_async_client(pli.hass),
                )
                coro = client.calc_routes(
                    from_location,
                    to_location,
                    avoid_toll_roads=True,
                    avoid_subscription_roads=True,
                    avoid_ferries=True,
                )
                future = asyncio.run_coroutine_threadsafe(coro, pli.hass.loop)
                pywaze_routes = future.result()
                if not pywaze_routes:
                    raise ValueError("No routes from pywaze")

                route = pywaze_routes[0]
                duration = route.duration
                distance_km = route.distance

            except Exception as pw_err:
                _LOGGER.error(
                    "(%s) pywaze fallback failed %s: %s",
                    entity_id,
                    type(pw_err).__name__,
                    pw_err,
                )
                pli.attributes["waze_error_count"] = (
                    pli.attributes.get("waze_error_count", 0) + 1
                )
                target.attributes[ATTR_DRIVING_MILES] = target.attributes[ATTR_MILES_FROM_HOME]
                return

        # Common post‐processing
        miles = distance_km * METERS_PER_KM / METERS_PER_MILE
        if miles <= 0:
            display_miles = target.attributes[ATTR_MILES_FROM_HOME]
        elif miles >= 100:
            display_miles = round(miles, 0)
        elif miles >= 10:
            display_miles = round(miles, 1)
        else:
            display_miles = round(miles, 2)

        target.attributes[ATTR_DRIVING_MILES] = str(display_miles)
        target.attributes[ATTR_DRIVING_MINUTES] = str(round(duration, 1))
        target.attributes[ATTR_ATTRIBUTION] += '"Data by Waze App. https://waze.com"; '

    def handle_reverse_geocode(call):
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
            - determine <locality> for friendly_name
            - record full location from Google_Maps, MapQuest, and/or Open_Street_Map
            - calculate other location-based statistics, such as distance_from_home
            - add to bread_crumbs as locality changes
            - create/update additional sensors if requested
            - friendly_name: something like "Rod (i.e. Rod's watch) is at Drew's"
        """

        entity_id = call.data.get(CONF_ENTITY_ID, "NONE")
        template = call.data.get(CONF_FRIENDLY_NAME_TEMPLATE, "NONE")
        force_update = call.data.get("force_update", False)

        if entity_id == "NONE":
            {
                _LOGGER.warning(
                    "%s is required in call of %s.reverse_geocode service."
                    % (CONF_ENTITY_ID, DOMAIN)
                )
            }
            return False

        _LOGGER.debug(
            "(%s) === Start === %s = %s; %s = %s"
            % (
                entity_id,
                CONF_FRIENDLY_NAME_TEMPLATE,
                template,
                "force_update",
                force_update,
            )
        )

        with INTEGRATION_LOCK:
            """Lock while updating the pli(API_STATE_OBJECT)."""
            _LOGGER.debug("INTEGRATION_LOCK obtained")

            try:
                currentApiTime = datetime.now()

                if pli.state.lower() != STATE_ON:
                    """Allow API calls to be paused."""
                    pli.attributes["api_calls_skipped"] += 1
                    _LOGGER.debug(
                        "(%s) api_calls_skipped = %d"
                        % (entity_id, pli.attributes["api_calls_skipped"])
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
                            "(%s) wait_time = %05.3f; api_calls_throttled = %d"
                            % (
                                entity_id,
                                wait_time,
                                pli.attributes["api_calls_throttled"],
                            )
                        )
                        time.sleep(wait_time)
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

                    with TARGET_LOCK:
                        """Lock while updating the target(entity_id)."""
                        _LOGGER.debug("TARGET_LOCK obtained")

                        target = PERSON_LOCATION_ENTITY(entity_id, pli)
                        target.entity_id = entity_id
                        target.attributes[ATTR_ATTRIBUTION] = ""

                        if ATTR_LATITUDE in target.attributes:
                            new_latitude = target.attributes[ATTR_LATITUDE]
                        else:
                            new_latitude = "None"
                        if ATTR_LONGITUDE in target.attributes:
                            new_longitude = target.attributes[ATTR_LONGITUDE]
                        else:
                            new_longitude = "None"

                        if "location_latitude" in target.this_entity_info:
                            old_latitude = target.this_entity_info["location_latitude"]
                        else:
                            old_latitude = "None"
                        if "location_longitude" in target.this_entity_info:
                            old_longitude = target.this_entity_info[
                                "location_longitude"
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

                            if (
                                pli.attributes["home_latitude"] != "None"
                                and pli.attributes["home_longitude"] != "None"
                            ):
                                old_distance_from_home = round(
                                    distance(
                                        float(old_latitude),
                                        float(old_longitude),
                                        float(pli.attributes["home_latitude"]),
                                        float(pli.attributes["home_longitude"]),
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

                        target.attributes[ATTR_COMPASS_BEARING] = compass_bearing

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
                            locality = "?"

                            if "location_time" in target.attributes:
                                new_location_time = datetime.strptime(
                                    str(target.attributes["location_time"]),
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
                            target.attributes[ATTR_SPEED] = round(
                                speed_during_interval, 1
                            )

                            if (
                                "reported_state" in target.attributes
                                and target.attributes["reported_state"].lower()
                                == "home"
                            ):
                                distance_from_home = 0  # clamp it down since "Home" is not a single point
                            elif (
                                new_latitude != "None"
                                and new_longitude != "None"
                                and pli.attributes["home_latitude"] != "None"
                                and pli.attributes["home_longitude"] != "None"
                            ):
                                distance_from_home = round(
                                    distance(
                                        float(new_latitude),
                                        float(new_longitude),
                                        float(pli.attributes["home_latitude"]),
                                        float(pli.attributes["home_longitude"]),
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
                            target.attributes[ATTR_METERS_FROM_HOME] = round(
                                distance_from_home, 1
                            )
                            target.attributes[ATTR_MILES_FROM_HOME] = round(
                                distance_from_home / METERS_PER_MILE, 1
                            )

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
                            target.attributes["direction"] = direction

                            # default the waze country code from waze_region config
                            waze_country_code = pli.configuration["waze_region"].upper()

                            if (
                                pli.configuration[CONF_RADAR_API_KEY]
                                != DEFAULT_API_KEY_NOT_SET
                            ):
                                """Call the Radar API if CONF_RADAR_API_KEY is configured"""
                                radar_url = (
                                    "https://api.radar.io/v1/geocode/reverse?coordinates="
                                    + str(new_latitude)
                                    + ","
                                    + str(new_longitude)
                                    + '&layer=address'
                                )
                                headers = {
                                    'Authorization': pli.configuration[CONF_RADAR_API_KEY],
                                    'Content-Type': 'application/json'
                                }

                                radar_decoded = {}
                                radar_response = httpx.get(radar_url, headers=headers)
                                radar_json_input = radar_response.text
                                radar_decoded = json.loads(radar_json_input)

                                if "city" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["city"]
                                elif "town" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["town"]
                                elif "village" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["village"]
                                elif "municipality" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["municipality"]
                                elif "county" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["county"]
                                elif "state" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["state"]
                                elif "country" in radar_decoded["addresses"][0]:
                                    locality = radar_decoded["addresses"][0]["country"]
                                _LOGGER.debug(
                                    "(" + entity_id + ") Radar locality = " + locality
                                )

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
                                    formatted_address = locality
                                _LOGGER.debug(
                                    "("
                                    + entity_id
                                    + ") RADAR formatted_address = "
                                    + formatted_address
                                )

                                target.attributes["Radar"] = formatted_address

                                radar_attribution = '"Powered by Radar"'
                                target.attributes[ATTR_ATTRIBUTION] += (
                                    radar_attribution + "; "
                                )

                                if (
                                    ATTR_GEOCODED
                                    in pli.configuration[CONF_CREATE_SENSORS]
                                ):
                                    target.make_template_sensor(
                                        "Radar",
                                        [
                                            {ATTR_COMPASS_BEARING: compass_bearing},
                                            ATTR_LATITUDE,
                                            ATTR_LONGITUDE,
                                            ATTR_SOURCE_TYPE,
                                            ATTR_GPS_ACCURACY,
                                            "icon",
                                            {"locality": locality},
                                            {
                                                "location_time": new_location_time.strftime(
                                                    "%Y-%m-%d %H:%M:%S"
                                                )
                                            },
                                            {ATTR_ATTRIBUTION: radar_attribution},
                                        ],
                                    )

                            if (
                                pli.configuration[CONF_OSM_API_KEY]
                                != DEFAULT_API_KEY_NOT_SET
                            ):
                                """Call the Open Street Map (Nominatim) API if CONF_OSM_API_KEY is configured"""
                                if (
                                    pli.configuration[CONF_OSM_API_KEY]
                                    == DEFAULT_API_KEY_NOT_SET
                                ):
                                    osm_url = (
                                        "https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat="
                                        + str(new_latitude)
                                        + "&lon="
                                        + str(new_longitude)
                                        + "&addressdetails=1&namedetails=1&zoom=18&limit=1"
                                    )
                                else:
                                    osm_url = (
                                        "https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat="
                                        + str(new_latitude)
                                        + "&lon="
                                        + str(new_longitude)
                                        + "&addressdetails=1&namedetails=1&zoom=18&limit=1&email="
                                        + pli.configuration[CONF_OSM_API_KEY]
                                    )

                                osm_decoded = {}
                                osm_response = httpx.get(osm_url)
                                osm_json_input = osm_response.text
                                osm_decoded = json.loads(osm_json_input)

                                if "city" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["city"]
                                elif "town" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["town"]
                                elif "villiage" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["village"]
                                elif "municipality" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["municipality"]
                                elif "county" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["county"]
                                elif "state" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["state"]
                                elif "country" in osm_decoded["address"]:
                                    locality = osm_decoded["address"]["country"]
                                _LOGGER.debug(
                                    "(" + entity_id + ") OSM locality = " + locality
                                )

                                if "country_code" in osm_decoded["address"]:
                                    waze_country_code = osm_decoded["address"]["country_code"].upper()
                                    _LOGGER.debug(
                                        "(" + entity_id + ") OSM waze_country_code = " + waze_country_code
                                    )

                                if "display_name" in osm_decoded:
                                    display_name = osm_decoded["display_name"]
                                else:
                                    display_name = locality
                                _LOGGER.debug(
                                    "("
                                    + entity_id
                                    + ") OSM display_name = "
                                    + display_name
                                )

                                target.attributes["Open_Street_Map"] = (
                                    display_name.replace(", ", " ")
                                )

                                if "licence" in osm_decoded:
                                    osm_attribution = '"' + osm_decoded["licence"] + '"'
                                    target.attributes[ATTR_ATTRIBUTION] += (
                                        osm_attribution + "; "
                                    )

                                else:
                                    osm_attribution = ""

                                if (
                                    ATTR_GEOCODED
                                    in pli.configuration[CONF_CREATE_SENSORS]
                                ):
                                    target.make_template_sensor(
                                        "Open_Street_Map",
                                        [
                                            {ATTR_COMPASS_BEARING: compass_bearing},
                                            ATTR_LATITUDE,
                                            ATTR_LONGITUDE,
                                            ATTR_SOURCE_TYPE,
                                            ATTR_GPS_ACCURACY,
                                            "icon",
                                            {"locality": locality},
                                            {
                                                "location_time": new_location_time.strftime(
                                                    "%Y-%m-%d %H:%M:%S"
                                                )
                                            },
                                            {ATTR_ATTRIBUTION: osm_attribution},
                                        ],
                                    )

                            if (
                                pli.configuration[CONF_GOOGLE_API_KEY]
                                != DEFAULT_API_KEY_NOT_SET
                            ):
                                """Call the Google Maps Reverse Geocoding API if CONF_GOOGLE_API_KEY is configured"""
                                """https://developers.google.com/maps/documentation/geocoding/overview?hl=en_US#ReverseGeocoding"""
                                google_url = (
                                    "https://maps.googleapis.com/maps/api/geocode/json?language="
                                    + pli.configuration[CONF_LANGUAGE]
                                    + "&region="
                                    + pli.configuration[CONF_REGION]
                                    + "&latlng="
                                    + str(new_latitude)
                                    + ","
                                    + str(new_longitude)
                                    + "&key="
                                    + pli.configuration[CONF_GOOGLE_API_KEY]
                                )
                                google_decoded = {}
                                google_response = httpx.get(google_url)
                                google_json_input = google_response.text
                                google_decoded = json.loads(google_json_input)

                                google_status = google_decoded["status"]
                                if google_status != "OK":
                                    _LOGGER.error(
                                        "("
                                        + entity_id
                                        + ") google_status = "
                                        + google_status
                                    )
                                else:
                                    if "results" in google_decoded:
                                        if (
                                            "formatted_address"
                                            in google_decoded["results"][0]
                                        ):
                                            formatted_address = google_decoded[
                                                "results"
                                            ][0]["formatted_address"]
                                            _LOGGER.debug(
                                                "("
                                                + entity_id
                                                + ") Google formatted_address = "
                                                + formatted_address
                                            )
                                            target.attributes["Google_Maps"] = (
                                                formatted_address
                                            )
                                        for component in google_decoded["results"][0][
                                            "address_components"
                                        ]:
                                            if "locality" in component["types"]:
                                                locality = component["long_name"]
                                                _LOGGER.debug(
                                                    "("
                                                    + entity_id
                                                    + ") Google locality = "
                                                    + locality
                                                )
                                            elif (locality == "?") and (
                                                "administrative_area_level_2"
                                                in component["types"]
                                            ):  # fall back to county
                                                locality = component["long_name"]
                                            elif (locality == "?") and (
                                                "administrative_area_level_1"
                                                in component["types"]
                                            ):  # fall back to state
                                                locality = component["long_name"]

                                            if "country" in component["types"]:
                                                waze_country_code = component["short_name"].upper()
                                                _LOGGER.debug(
                                                    "(" + entity_id + ") Google waze_country_code = " + waze_country_code
                                                )

                                        google_attribution = '"powered by Google"'
                                        target.attributes[ATTR_ATTRIBUTION] += (
                                            google_attribution + "; "
                                        )

                                        if (
                                            ATTR_GEOCODED
                                            in pli.configuration[CONF_CREATE_SENSORS]
                                        ):
                                            target.make_template_sensor(
                                                "Google_Maps",
                                                [
                                                    {
                                                        ATTR_COMPASS_BEARING: compass_bearing
                                                    },
                                                    ATTR_LATITUDE,
                                                    ATTR_LONGITUDE,
                                                    ATTR_SOURCE_TYPE,
                                                    ATTR_GPS_ACCURACY,
                                                    "icon",
                                                    {"locality": locality},
                                                    {
                                                        "location_time": new_location_time.strftime(
                                                            "%Y-%m-%d %H:%M:%S"
                                                        )
                                                    },
                                                    {
                                                        ATTR_ATTRIBUTION: google_attribution
                                                    },
                                                ],
                                            )

                            if (
                                pli.configuration[CONF_MAPQUEST_API_KEY]
                                != DEFAULT_API_KEY_NOT_SET
                            ):
                                """Call the Mapquest Reverse Geocoding API if CONF_MAPQUEST_API_KEY is configured"""
                                """https://developer.mapquest.com/documentation/geocoding-api/reverse/get/"""
                                mapquest_url = (
                                    "https://www.mapquestapi.com/geocoding/v1/reverse"
                                    + "?location="
                                    + str(new_latitude)
                                    + ","
                                    + str(new_longitude)
                                    + "&thumbMaps=false"
                                    + "&key="
                                    + pli.configuration[CONF_MAPQUEST_API_KEY]
                                )
                                mapquest_decoded = {}
                                mapquest_response = httpx.get(mapquest_url)
                                mapquest_json_input = mapquest_response.text
                                if not is_json(mapquest_json_input):
                                    _LOGGER.error(
                                        INTEGRATION_NAME
                                        + " ("
                                        + entity_id
                                        + ") mapquest response - "
                                        + mapquest_json_input
                                    )
                                else:
                                    _LOGGER.debug(
                                        "("
                                        + entity_id
                                        + ") mapquest response - "
                                        + mapquest_json_input
                                    )
                                    mapquest_decoded = json.loads(mapquest_json_input)

                                    mapquest_statuscode = mapquest_decoded["info"][
                                        "statuscode"
                                    ]
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
                                            and "locations"
                                            in mapquest_decoded["results"][0]
                                        ):
                                            mapquest_location = mapquest_decoded[
                                                "results"
                                            ][0]["locations"][0]

                                            formatted_address = ""
                                            if "street" in mapquest_location:
                                                formatted_address += (
                                                    mapquest_location["street"] + ", "
                                                )
                                            if (
                                                "adminArea5" in mapquest_location
                                            ):  # city
                                                locality = mapquest_location[
                                                    "adminArea5"
                                                ]
                                                formatted_address += locality + ", "
                                            elif (
                                                "adminArea4" in mapquest_location
                                                and "adminArea4Type"
                                                in mapquest_location
                                            ):  # county
                                                locality = (
                                                    mapquest_location["adminArea4"]
                                                    + " "
                                                    + mapquest_location[
                                                        "adminArea4Type"
                                                    ]
                                                )
                                                formatted_address += locality + ", "
                                            if (
                                                "adminArea3" in mapquest_location
                                            ):  # state
                                                formatted_address += (
                                                    mapquest_location["adminArea3"]
                                                    + " "
                                                )
                                            if "postalCode" in mapquest_location:  # zip
                                                formatted_address += (
                                                    mapquest_location["postalCode"]
                                                    + " "
                                                )
                                            if (
                                                "adminArea1" in mapquest_location
                                                and mapquest_location["adminArea1"]
                                                != "US"
                                            ):  # country
                                                formatted_address += mapquest_location[
                                                    "adminArea1"
                                                ]

                                            _LOGGER.debug(
                                                "("
                                                + entity_id
                                                + ") mapquest formatted_address = "
                                                + formatted_address
                                            )

                                            if ("adminArea1" in mapquest_location):
                                                waze_country_code = mapquest_location[adminArea1]
                                                _LOGGER.debug(
                                                    "(" + entity_id + ") mapquest waze_country_code = " + waze_country_code
                                                )

                                            target.attributes["MapQuest"] = (
                                                formatted_address
                                            )

                                            _LOGGER.debug(
                                                "("
                                                + entity_id
                                                + ") mapquest locality = "
                                                + locality
                                            )

                                            mapquest_attribution = (
                                                '"'
                                                + mapquest_decoded["info"]["copyright"][
                                                    "text"
                                                ]
                                                + '"'
                                            )
                                            target.attributes[ATTR_ATTRIBUTION] += (
                                                mapquest_attribution + "; "
                                            )

                                            if (
                                                ATTR_GEOCODED
                                                in pli.configuration[
                                                    CONF_CREATE_SENSORS
                                                ]
                                            ):
                                                target.make_template_sensor(
                                                    "MapQuest",
                                                    [
                                                        {
                                                            ATTR_COMPASS_BEARING: compass_bearing
                                                        },
                                                        ATTR_LATITUDE,
                                                        ATTR_LONGITUDE,
                                                        ATTR_SOURCE_TYPE,
                                                        ATTR_GPS_ACCURACY,
                                                        "icon",
                                                        {"locality": locality},
                                                        {
                                                            "location_time": new_location_time.strftime(
                                                                "%Y-%m-%d %H:%M:%S"
                                                            )
                                                        },
                                                        {
                                                            ATTR_ATTRIBUTION: mapquest_attribution
                                                        },
                                                    ],
                                                )

                            target.attributes["locality"] = locality
                            target.this_entity_info["geocode_count"] += 1
                            target.this_entity_info["location_latitude"] = new_latitude
                            target.this_entity_info["location_longitude"] = (
                                new_longitude
                            )
                            target.this_entity_info["reverse_geocode_location_time"] = (
                                new_location_time
                            )

                            # Call WazeRouteCalculator if not at Home:

                            _get_waze_driving_miles_and_minutes(
                                target,
                                new_latitude,
                                new_longitude,
                                waze_country_code,
                            )

                        # Determine friendly_name_location and new_bread_crumb:

                        if target.attributes["reported_state"].lower() in [
                            STATE_HOME,
                            STATE_ON,
                        ]:
                            new_bread_crumb = "Home"
                            friendly_name_location = "is Home"
                        elif target.attributes["reported_state"].lower() in [
                            "away",
                            STATE_NOT_HOME,
                            STATE_OFF,
                        ]:
                            new_bread_crumb = "Away"
                            friendly_name_location = "is Away"
                        else:
                            new_bread_crumb = target.attributes["reported_state"]
                            friendly_name_location = f"is at {new_bread_crumb}"

                        if "zone" in target.attributes:
                            reportedZone = target.attributes["zone"]
                            zoneStateObject = pli.hass.states.get(
                                ZONE_DOMAIN + "." + reportedZone
                            )
                            if (
                                zoneStateObject is not None
                                    and 
                                not reportedZone.startswith(IC3_STATIONARY_ZONE_PREFIX)
                            ):
                                zoneAttributesObject = zoneStateObject.attributes.copy()
                                if "friendly_name" in zoneAttributesObject:
                                    new_bread_crumb = zoneAttributesObject[
                                        "friendly_name"
                                    ]
                                    friendly_name_location = f"is at {new_bread_crumb}"

                        if (
                            new_bread_crumb == "Away"
                            and "locality" in target.attributes
                        ):
                            new_bread_crumb = target.attributes["locality"]
                            friendly_name_location = f"is in {new_bread_crumb}"

                        _LOGGER.debug(
                            "(%s) friendly_name_location = %s; new_bread_crumb = %s",
                            target.entity_id,
                            friendly_name_location,
                            new_bread_crumb,
                        )

                        # Append location to bread_crumbs attribute:

                        if ATTR_BREAD_CRUMBS in target.attributes:
                            old_bread_crumbs = target.attributes[ATTR_BREAD_CRUMBS]
                            if not old_bread_crumbs.endswith(new_bread_crumb):
                                target.attributes[ATTR_BREAD_CRUMBS] = (
                                    old_bread_crumbs + "> " + new_bread_crumb
                                )[-255:]
                        else:
                            target.attributes[ATTR_BREAD_CRUMBS] = new_bread_crumb

                        if template != "NONE":
                            # Format friendly_name attribute using the supplied friendly_name_template:

                            if (
                                "source" in target.attributes
                                and "." in target.attributes["source"]
                            ):
                                sourceEntity = target.attributes["source"]
                                sourceObject = pli.hass.states.get(sourceEntity)
                                if (
                                    sourceObject is not None
                                    and "source" in sourceObject.attributes
                                    and "." in target.attributes["source"]
                                ):
                                    # Find the source for a person entity:
                                    sourceEntity = sourceObject.attributes["source"]
                                    sourceObject = pli.hass.states.get(sourceEntity)
                            else:
                                sourceObject = target

                            friendly_name_variables = {
                                "friendly_name_location": friendly_name_location,
                                "person_name": target.attributes["person_name"],
                                "source": {
                                    "entity_id": sourceEntity,
                                    "state": sourceObject.state,
                                    "attributes": sourceObject.attributes,
                                },
                                "target": {
                                    "entity_id": target.entity_id,
                                    "state": target.state,
                                    "attributes": target.attributes,
                                },
                            }
                            #        _LOGGER.debug(f"friendly_name_variables = {friendly_name_variables}")

                            try:
                                target.attributes["friendly_name"] = (
                                    Template(
                                        pli.configuration[CONF_FRIENDLY_NAME_TEMPLATE]
                                    )
                                    .render(**friendly_name_variables)
                                    .replace("()", "")
                                    .replace("  ", " ")
                                )
                            except TemplateError as err:
                                _LOGGER.error(
                                    "Error parsing friendly_name_template: %s", err
                                )

                            target.set_state()

                            target.make_template_sensors()

                        _LOGGER.debug("TARGET_LOCK release...")
            except Exception as e:
                _LOGGER.error(
                    "(%s) Exception %s: %s" % (entity_id, type(e).__name__, str(e))
                )
                _LOGGER.debug(traceback.format_exc())
                pli.attributes["api_error_count"] += 1

            pli.set_state()
            _LOGGER.debug("INTEGRATION_LOCK release...")
        _LOGGER.debug("(%s) === Return ===", entity_id)

    pli.hass.services.register(DOMAIN, "reverse_geocode", handle_reverse_geocode)
    return True

