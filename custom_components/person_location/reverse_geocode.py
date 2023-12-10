""" The person_location integration reverse_geocode service."""

import asyncio
import json
import logging
import math
import time
import traceback
from datetime import datetime

from homeassistant.components.device_tracker.const import ATTR_SOURCE_TYPE
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    CONF_FRIENDLY_NAME_TEMPLATE,
    STATE_ON,
)
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.location import distance
# from requests import get
import httpx
from pywaze.route_calculator import WazeRouteCalculator,WRCError

from .const import (
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_GEOCODED,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    CONF_CREATE_SENSORS,
    CONF_GOOGLE_API_KEY,
    CONF_LANGUAGE,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_REGION,
    DEFAULT_API_KEY_NOT_SET,
    DOMAIN,
    INTEGRATION_LOCK,
    METERS_PER_KM,
    METERS_PER_MILE,
    MIN_DISTANCE_TRAVELLED_TO_GEOCODE,
    PERSON_LOCATION_ENTITY,
    TARGET_LOCK,
    THROTTLE_INTERVAL,
    WAZE_MIN_METERS_FROM_HOME,
)

_LOGGER = logging.getLogger(__name__)


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
            - create/update additional sensors if requested
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
                        attribution = ""

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

                            if speed_during_interval <= 0.5:
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

                                target.attributes[
                                    "Open_Street_Map"
                                ] = display_name.replace(", ", " ")

                                if "licence" in osm_decoded:
                                    osm_attribution = '"' + osm_decoded["licence"] + '"'
                                    attribution += osm_attribution + "; "
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
                                            target.attributes[
                                                "Google_Maps"
                                            ] = formatted_address
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

                                        google_attribution = '"powered by Google"'
                                        attribution += google_attribution + "; "

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
                                _LOGGER.debug(
                                    "("
                                    + entity_id
                                    + ") response - "
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
                                        mapquest_location = mapquest_decoded["results"][
                                            0
                                        ]["locations"][0]

                                        formatted_address = ""
                                        if "street" in mapquest_location:
                                            formatted_address += (
                                                mapquest_location["street"] + ", "
                                            )
                                        if "adminArea5" in mapquest_location:  # city
                                            locality = mapquest_location["adminArea5"]
                                            formatted_address += locality + ", "
                                        elif (
                                            "adminArea4" in mapquest_location
                                            and "adminArea4Type" in mapquest_location
                                        ):  # county
                                            locality = (
                                                mapquest_location["adminArea4"]
                                                + " "
                                                + mapquest_location["adminArea4Type"]
                                            )
                                            formatted_address += locality + ", "
                                        if "adminArea3" in mapquest_location:  # state
                                            formatted_address += (
                                                mapquest_location["adminArea3"] + " "
                                            )
                                        if "postalCode" in mapquest_location:  # zip
                                            formatted_address += (
                                                mapquest_location["postalCode"] + " "
                                            )
                                        if (
                                            "adminArea1" in mapquest_location
                                            and mapquest_location["adminArea1"] != "US"
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
                                        target.attributes[
                                            "MapQuest"
                                        ] = formatted_address

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
                                        attribution += mapquest_attribution + "; "

                                        if (
                                            ATTR_GEOCODED
                                            in pli.configuration[CONF_CREATE_SENSORS]
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

                            if template != "NONE":
                                target.attributes["friendly_name"] = template.replace(
                                    "<locality>", locality
                                )
                            target.this_entity_info["locality"] = locality
                            target.this_entity_info["geocode_count"] += 1
                            target.this_entity_info["location_latitude"] = new_latitude
                            target.this_entity_info[
                                "location_longitude"
                            ] = new_longitude
                            target.this_entity_info[
                                "reverse_geocode_location_time"
                            ] = new_location_time

                            if "reported_state" in target.attributes:
                                if target.attributes["reported_state"] != "Away":
                                    newBreadCrumb = target.attributes["reported_state"]
                                else:
                                    newBreadCrumb = locality
                            else:
                                newBreadCrumb = locality
                            if ATTR_BREAD_CRUMBS in target.attributes:
                                oldBreadCrumbs = target.attributes[ATTR_BREAD_CRUMBS]
                                if not oldBreadCrumbs.endswith(newBreadCrumb):
                                    target.attributes[ATTR_BREAD_CRUMBS] = (
                                        oldBreadCrumbs + "> " + newBreadCrumb
                                    )[-255:]
                            else:
                                target.attributes[ATTR_BREAD_CRUMBS] = newBreadCrumb

                            # Call WazeRouteCalculator if not at Home:
                            if not pli.configuration["use_waze"]:
                                pass
                            elif (
                                target.attributes[ATTR_METERS_FROM_HOME]
                                < WAZE_MIN_METERS_FROM_HOME
                            ):
                                target.attributes[
                                    ATTR_DRIVING_MILES
                                ] = target.attributes[ATTR_MILES_FROM_HOME]
                                target.attributes[ATTR_DRIVING_MINUTES] = "0"
                            else:
                                try:
                                    """
                                    Figure it out from:
                                    https://github.com/home-assistant/core/blob/dev/homeassistant/components/waze_travel_time/sensor.py
                                    https://github.com/kovacsbalu/WazeRouteCalculator
                                    """
                                    _LOGGER.debug(
                                        "(" + entity_id + ") Waze calculation"
                                    )

                                    async def async_get_waze_route (
                                            from_location,
                                            to_location,
                                            waze_region,
                                        ):
                                        client = WazeRouteCalculator(
                                            region=waze_region, 
                                            client=get_async_client(pli.hass),
                                        )
                                        route = await client.calc_route_info(
                                            from_location,
                                            to_location,
                                            avoid_toll_roads=True,
                                        )
                                        return route
                                        
                                    from_location = (
                                        str(new_latitude) + "," + str(new_longitude)
                                    )
                                    to_location = (
                                        str(pli.attributes["home_latitude"])
                                        + ","
                                        + str(pli.attributes["home_longitude"])
                                    )
                                    routeTime, routeDistance = asyncio.run_coroutine_threadsafe(
                                        async_get_waze_route(
                                        from_location,
                                        to_location,
                                        pli.configuration["waze_region"].upper(),
                                #        "US",
                                        ),pli.hass.loop
                                    ).result()
                                    _LOGGER.debug(
                                        "("
                                        + entity_id
                                        + ") Waze routeDistance "
                                        + str(routeDistance)
                                    )  # km
                                    routeDistance = (
                                        routeDistance * METERS_PER_KM / METERS_PER_MILE
                                    )  # miles
                                    if routeDistance >= 100:
                                        target.attributes[ATTR_DRIVING_MILES] = str(
                                            round(routeDistance, 0)
                                        )
                                    elif routeDistance >= 10:
                                        target.attributes[ATTR_DRIVING_MILES] = str(
                                            round(routeDistance, 1)
                                        )
                                    else:
                                        target.attributes[ATTR_DRIVING_MILES] = str(
                                            round(routeDistance, 2)
                                        )
                                    _LOGGER.debug(
                                        "("
                                        + entity_id
                                        + ") Waze routeTime "
                                        + str(routeTime)
                                    )  # minutes
                                    target.attributes[ATTR_DRIVING_MINUTES] = str(
                                        round(routeTime, 1)
                                    )
                                    attribution += (
                                        '"Data by Waze App. https://waze.com"; '
                                    )
                                except Exception as e:
                                    _LOGGER.error(
                                        "("
                                        + entity_id
                                        + ") Waze Exception "
                                        + type(e).__name__
                                        + ": "
                                        + str(e)
                                    )
                                    _LOGGER.debug(traceback.format_exc())
                                    pli.attributes["waze_error_count"] += 1

                                    target.attributes[
                                        ATTR_DRIVING_MILES
                                    ] = target.attributes[ATTR_MILES_FROM_HOME]

                        target.attributes[ATTR_ATTRIBUTION] = attribution

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
