"""reverse_geocode.py - The person_location integration reverse_geocode service (async)."""

# pyright: reportMissingImports=false
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import PersonLocationIntegration

import asyncio
from datetime import datetime
import json
import logging
import math
import traceback

from jinja2 import Template

from homeassistant.components.device_tracker.const import ATTR_SOURCE_TYPE
from homeassistant.components.zone import DOMAIN as ZONE_DOMAIN
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_GPS_ACCURACY,
    ATTR_ICON,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.exceptions import TemplateError
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .const import (
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DIRECTION,
    ATTR_GEOCODED,
    ATTR_GOOGLE_MAPS,
    ATTR_LOCALITY,
    ATTR_LOCATION_TIMESTAMP,
    ATTR_MAPQUEST,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    ATTR_OPEN_STREET_MAP,
    ATTR_PERSON_NAME,
    ATTR_RADAR,
    ATTR_REPORTED_STATE,
    ATTR_SOURCE,
    ATTR_SPEED,
    ATTR_ZONE,
    CONF_CREATE_SENSORS,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_GOOGLE_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
    CONF_REGION,
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
    METERS_PER_MILE,
    MIN_DISTANCE_TRAVELLED_TO_GEOCODE,
    SWITCH_GOOGLE_GEOCODING_API,
    SWITCH_MAPQUEST_GEOCODING_API,
    SWITCH_OSM_NOMINATIM_GEOCODING_API,
    SWITCH_RADAR_GEOCODING_API,
    TARGET_ASYNCIO_LOCK,
    THROTTLE_INTERVAL,
)
from .helpers.api import (
    async_get_google_maps_geocoding,
    async_get_mapquest_reverse_geocoding,
    async_get_open_street_map_reverse_geocoding,
    async_get_radar_reverse_geocoding,
    get_home_coordinates,
)
from .helpers.duration_distance import update_driving_miles_and_minutes
from .helpers.timestamp import now_utc, parse_ts, to_iso
from .sensor import (
    PersonLocationTargetSensor,
    create_and_register_template_sensor,
    get_target_entity,
)
from .switch import is_provider_enabled

_LOGGER = logging.getLogger(__name__)


def calculate_initial_compass_bearing(pointA: tuple, pointB: tuple) -> float:
    """
    Calculate the bearing between two points.

    θ = atan2(sin(Δlong).cos(lat2),
              cos(lat1).sin(lat2) − sin(lat1).cos(lat2).cos(Δlong))

    From https://gist.github.com/jeromer/2005586
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

    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing


def is_json(myjson: str) -> bool:
    """Return True if myjson is valid JSON."""
    try:
        json.loads(myjson)
    except ValueError:
        return False
    return True


def shorten_to_last_255(s: str) -> str:
    """
    Shorten a string to <= 255 characters.

    - If len(s) <= 255: return unchanged.
    - Else:
      1) take last 251 chars
      2) drop up to (and including) first '> ' inside that tail (if present)
      3) prefix with '... '
    """
    if s is None:
        return ""
    if len(s) <= 255:
        return s

    marker = "> "
    tail = s[-251:]
    idx = tail.find(marker)
    if idx != -1:
        tail = tail[idx + len(marker) :]
    return "... " + tail


async def async_setup_reverse_geocode(pli: PersonLocationIntegration) -> bool:
    """Initialize the reverse_geocode service (async with naive datetime)."""
    # ---------------------------------------------------------------
    # Variables shared across inner coroutines
    # ---------------------------------------------------------------
    #   handle_reverse_geocode
    #       async_call_google_maps_geocoding
    #       async_call_mapquest_reverse_geocoding
    #       async_call_open_street_map_reverse_geocoding
    #       async_call_radar_reverse_geocoding
    # ---------------------------------------------------------------
    home_latitude: float | None = None
    home_longitude: float | None = None
    new_latitude: str | None = None
    new_longitude: str | None = None
    new_location_time: datetime | None = None
    new_locality: str | None = None
    waze_country_code: str | None = None

    # ------- Google Maps -------------------------------------------

    async def async_call_google_maps_geocoding(
        target: PersonLocationTargetSensor,
    ) -> None:
        """Call the Google Maps Reverse Geocoding API."""
        """https://developers.google.com/maps/documentation/geocoding/overview?hl=en_US#ReverseGeocoding"""
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

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

        google_decoded = google_response.get("data", {})

        formatted_address = None
        if "results" in google_decoded and google_decoded["results"]:
            first = google_decoded["results"][0]
            formatted_address = first.get("formatted_address")
            if formatted_address:
                _LOGGER.debug(
                    "(%s) Google formatted_address = %s", entity_id, formatted_address
                )
                target._attr_extra_state_attributes[ATTR_GOOGLE_MAPS] = (
                    formatted_address
                )

            for component in first.get("address_components", []):
                types = component.get("types", [])
                if "locality" in types:
                    new_locality = component.get("long_name", new_locality or "?")
                    _LOGGER.debug(
                        "(%s) Google new_locality = %s", entity_id, new_locality
                    )
                elif (new_locality == "?") and ("administrative_area_level_2" in types):
                    new_locality = component.get("long_name", new_locality or "?")
                elif (new_locality == "?") and ("administrative_area_level_1" in types):
                    new_locality = component.get("long_name", new_locality or "?")

                if "country" in types:
                    waze_country_code = (component.get("short_name") or "").upper()
                    _LOGGER.debug(
                        "(%s) Google waze_country_code = %s",
                        entity_id,
                        waze_country_code,
                    )

        google_attribution = '"powered by Google"'
        target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
            google_attribution + "; "
        )

        if ATTR_GEOCODED in pli.configuration.get(CONF_CREATE_SENSORS, []):
            attrs = {
                ATTR_LATITUDE: new_latitude,
                ATTR_LONGITUDE: new_longitude,
                ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                    ATTR_SOURCE_TYPE
                ),
                ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                    ATTR_GPS_ACCURACY
                ),
                ATTR_ICON: target._attr_extra_state_attributes.get(ATTR_ICON),
                ATTR_LOCALITY: new_locality,
                ATTR_LOCATION_TIMESTAMP: new_location_time.isoformat(),
                ATTR_PERSON_NAME: target._person_name,
                ATTR_ATTRIBUTION: google_attribution,
            }
            create_and_register_template_sensor(
                pli.hass,
                target,
                ATTR_GOOGLE_MAPS,
                (formatted_address or "")[:250],
                attrs,
            )

    # ------- MapQuest ----------------------------------------------

    async def async_call_mapquest_reverse_geocoding(
        target: PersonLocationTargetSensor,
    ) -> None:
        """Call the MapQuest Reverse Geocoding API."""
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

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

        mapquest_decoded = mapquest_response.get("data", {})

        status_code = mapquest_decoded.get("info", {}).get("statuscode", -1)
        if status_code != 0:
            _LOGGER.error(
                "(%s) mapquest_statuscode = %s messages = %s",
                entity_id,
                status_code,
                mapquest_decoded.get("info", {}).get("messages"),
            )
            return

        loc = mapquest_decoded.get("results", [{}])[0].get("locations", [{}])[0]

        formatted_address_parts = []
        if "street" in loc:
            formatted_address_parts.append(f"{loc['street']}, ")
        if "adminArea5" in loc:
            new_locality = loc["adminArea5"]
            formatted_address_parts.append(f"{new_locality}, ")
        elif "adminArea4" in loc and "adminArea4Type" in loc:
            new_locality = f"{loc['adminArea4']} {loc['adminArea4Type']}"
            formatted_address_parts.append(f"{new_locality}, ")
        if "adminArea3" in loc:
            formatted_address_parts.append(f"{loc['adminArea3']} ")
        if "postalCode" in loc:
            formatted_address_parts.append(f"{loc['postalCode']} ")
        if "adminArea1" in loc and loc["adminArea1"] != "US":
            formatted_address_parts.append(loc["adminArea1"])

        formatted_address = "".join(formatted_address_parts)
        _LOGGER.debug(
            "(%s) mapquest formatted_address = %s", entity_id, formatted_address
        )

        if "adminArea1" in loc:
            waze_country_code = loc["adminArea1"]
            _LOGGER.debug(
                "(%s) mapquest waze_country_code = %s", entity_id, waze_country_code
            )

        target._attr_extra_state_attributes[ATTR_MAPQUEST] = formatted_address
        _LOGGER.debug("(%s) mapquest new_locality = %s", entity_id, new_locality)

        mapquest_attribution = f'"{mapquest_decoded.get("info", {}).get("copyright", {}).get("text", "MapQuest")}"'
        target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
            mapquest_attribution + "; "
        )

        if ATTR_GEOCODED in pli.configuration.get(CONF_CREATE_SENSORS, []):
            attrs = {
                ATTR_LATITUDE: new_latitude,
                ATTR_LONGITUDE: new_longitude,
                ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                    ATTR_SOURCE_TYPE
                ),
                ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                    ATTR_GPS_ACCURACY
                ),
                ATTR_ICON: target._attr_extra_state_attributes.get(ATTR_ICON),
                ATTR_LOCALITY: new_locality,
                ATTR_LOCATION_TIMESTAMP: new_location_time.isoformat(),
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

    # ------- OpenStreetMap (Nominatim) ------------------------------

    async def async_call_open_street_map_reverse_geocoding(
        target: PersonLocationTargetSensor,
    ) -> None:
        """Call the Nominatim Reverse Geocoding (OpenStreetMap) API."""
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

        osm_response = await async_get_open_street_map_reverse_geocoding(
            pli.hass, pli.configuration[CONF_OSM_API_KEY], new_latitude, new_longitude
        )
        if not osm_response.get("ok"):
            _LOGGER.warning(
                "[async_call_open_street_map_reverse_geocoding] %s",
                osm_response.get("error"),
            )
            return

        osm_decoded = osm_response.get("data", {})

        # Save home_state and home_countryCode for later comparisons
        if (
            new_latitude == home_latitude
            and new_longitude == home_longitude
            and "address" in osm_decoded
        ):
            addr = osm_decoded["address"]
            if "country_code" in addr and "state" in addr:
                home_country_code = addr["country_code"].upper()
                home_state = addr["state"]
                _LOGGER.debug(
                    "(%s) OSM home_state = %s, home_country_code = %s",
                    entity_id,
                    home_state,
                    home_country_code,
                )

        address = osm_decoded.get("address", {})
        for key in DEFAULT_LOCALITY_PRIORITY_OSM:
            if key in address:
                new_locality = address[key]
                break
        _LOGGER.debug("(%s) OSM new_locality = %s", entity_id, new_locality)

        if "country_code" in address:
            waze_country_code = address["country_code"].upper()
            _LOGGER.debug(
                "(%s) OSM waze_country_code = %s", entity_id, waze_country_code
            )

        display_name = osm_decoded.get("display_name", new_locality or "")
        _LOGGER.debug("(%s) OSM display_name = %s", entity_id, display_name)

        target._attr_extra_state_attributes[ATTR_OPEN_STREET_MAP] = (
            display_name.replace(", ", " ")
        )

        osm_attribution = ""
        if "licence" in osm_decoded:
            osm_attribution = f'"{osm_decoded["licence"]}"'
            target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
                osm_attribution + "; "
            )

        if ATTR_GEOCODED in pli.configuration.get(CONF_CREATE_SENSORS, []):
            attrs = {
                ATTR_LATITUDE: new_latitude,
                ATTR_LONGITUDE: new_longitude,
                ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                    ATTR_SOURCE_TYPE
                ),
                ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                    ATTR_GPS_ACCURACY
                ),
                ATTR_ICON: target._attr_extra_state_attributes.get(ATTR_ICON),
                ATTR_LOCALITY: new_locality,
                ATTR_LOCATION_TIMESTAMP: new_location_time.isoformat(),
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

    # ------- Radar --------------------------------------------------

    async def async_call_radar_reverse_geocoding(
        target: PersonLocationTargetSensor,
    ) -> None:
        """Call the Radar Reverse Geocoding API."""
        nonlocal new_locality
        nonlocal waze_country_code

        entity_id = target._entity_id

        radar_response = await async_get_radar_reverse_geocoding(
            pli.hass, pli.configuration[CONF_RADAR_API_KEY], new_latitude, new_longitude
        )
        if not radar_response.get("ok"):
            _LOGGER.warning(
                "[async_call_radar_reverse_geocoding] %s", radar_response.get("error")
            )
            return

        radar_decoded = radar_response.get("data", {})
        first = (radar_decoded.get("addresses") or [{}])[0]

        # Derive locality
        for key in (
            "city",
            "town",
            "village",
            "municipality",
            "county",
            "state",
            "country",
        ):
            if key in first:
                new_locality = first[key]
                break
        _LOGGER.debug("(%s) Radar new_locality = %s", entity_id, new_locality)

        if "countryCode" in first:
            waze_country_code = first["countryCode"].upper()
            _LOGGER.debug(
                "(%s) Radar waze_country_code = %s", entity_id, waze_country_code
            )

        formatted_address = (
            first.get("formattedAddress")
            or first.get("addressLabel")
            or (new_locality or "")
        )
        _LOGGER.debug("(%s) RADAR formatted_address = %s", entity_id, formatted_address)

        target._attr_extra_state_attributes[ATTR_RADAR] = formatted_address

        radar_attribution = '"Powered by Radar"'
        target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
            radar_attribution + "; "
        )

        if ATTR_GEOCODED in pli.configuration.get(CONF_CREATE_SENSORS, []):
            attrs = {
                ATTR_LATITUDE: new_latitude,
                ATTR_LONGITUDE: new_longitude,
                ATTR_SOURCE_TYPE: target._attr_extra_state_attributes.get(
                    ATTR_SOURCE_TYPE
                ),
                ATTR_GPS_ACCURACY: target._attr_extra_state_attributes.get(
                    ATTR_GPS_ACCURACY
                ),
                ATTR_ICON: target._attr_extra_state_attributes.get(ATTR_ICON),
                ATTR_LOCALITY: new_locality,
                ATTR_LOCATION_TIMESTAMP: new_location_time.isoformat(),
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
        nonlocal home_latitude, home_longitude
        nonlocal new_latitude, new_longitude
        nonlocal new_location_time, new_locality
        nonlocal waze_country_code

        entity_id = call.data.get(CONF_ENTITY_ID, "NONE")
        template_arg = call.data.get(CONF_FRIENDLY_NAME_TEMPLATE, "NONE")
        force_update = call.data.get("force_update", False)

        if entity_id == "NONE":
            _LOGGER.warning(
                "%s is required in call of %s.reverse_geocode service.",
                CONF_ENTITY_ID,
                DOMAIN,
            )
            return False

        _LOGGER.debug(
            "(%s) === Start === %s = %s; force_update = %s",
            entity_id,
            CONF_FRIENDLY_NAME_TEMPLATE,
            template_arg,
            force_update,
        )

        async with INTEGRATION_ASYNCIO_LOCK:
            _LOGGER.debug("INTEGRATION_ASYNCIO_LOCK obtained")
            try:
                # Use naive datetime.now() for throttling/tracking (reverted behavior)
                currentApiTime = dt_util.utcnow()

                if str(pli._attr_native_value).lower() != STATE_ON:
                    pli._attr_extra_state_attributes["api_calls_skipped"] += 1
                    _LOGGER.debug(
                        "(%s) api_calls_skipped = %d",
                        entity_id,
                        pli._attr_extra_state_attributes["api_calls_skipped"],
                    )
                else:
                    last_updated: datetime | None = (
                        pli._attr_extra_state_attributes.get("api_last_updated")
                    )
                    last_updated_raw = pli._attr_extra_state_attributes.get(
                        "api_last_updated"
                    )
                    last_updated: datetime | None = None
                    if isinstance(last_updated_raw, str):
                        last_updated = dt_util.parse_datetime(last_updated_raw)
                    elif isinstance(last_updated_raw, datetime):
                        last_updated = last_updated_raw

                    if last_updated is None:
                        last_updated = currentApiTime - THROTTLE_INTERVAL

                    # Compute wait time in seconds: (last + interval) - now
                    wait_time = (
                        last_updated + THROTTLE_INTERVAL - currentApiTime
                    ).total_seconds()
                    if wait_time > 0:
                        pli._attr_extra_state_attributes["api_calls_throttled"] += 1
                        _LOGGER.debug(
                            "(%s) wait_time = %05.3f; api_calls_throttled = %d",
                            entity_id,
                            wait_time,
                            pli._attr_extra_state_attributes["api_calls_throttled"],
                        )
                        await asyncio.sleep(wait_time)
                        currentApiTime = now_utc()

                    # Record the integration attributes in the API_STATE_OBJECT:
                    pli._attr_extra_state_attributes["api_last_updated"] = to_iso(
                        currentApiTime
                    )
                    pli._attr_extra_state_attributes["api_calls_requested"] += 1

                    counter_attribute = f"{entity_id} calls"
                    pli._attr_extra_state_attributes[counter_attribute] = (
                        pli._attr_extra_state_attributes.get(counter_attribute, 0) + 1
                    )
                    _LOGGER.debug(
                        "(%s) %s = %s",
                        entity_id,
                        counter_attribute,
                        pli._attr_extra_state_attributes[counter_attribute],
                    )

                    # ---- handle the service call, updating the target(entity_id)
                    async with TARGET_ASYNCIO_LOCK:
                        _LOGGER.debug("TARGET_ASYNCIO_LOCK obtained")

                        target = get_target_entity(pli, entity_id)
                        if not target:
                            _LOGGER.warning("No target sensor found for %s", entity_id)
                            return False

                        home_latitude, home_longitude = get_home_coordinates(pli.hass)

                        # Reset attribution before updating
                        target._attr_extra_state_attributes[ATTR_ATTRIBUTION] = ""

                        new_latitude = target._attr_extra_state_attributes.get(
                            ATTR_LATITUDE, "None"
                        )
                        new_longitude = target._attr_extra_state_attributes.get(
                            ATTR_LONGITUDE, "None"
                        )

                        old_latitude = target.this_entity_info.get(
                            INFO_LOCATION_LATITUDE, "None"
                        )
                        old_longitude = target.this_entity_info.get(
                            INFO_LOCATION_LONGITUDE, "None"
                        )

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
                                old_distance_from_home = 0.0

                            compass_bearing = round(
                                calculate_initial_compass_bearing(
                                    (float(old_latitude), float(old_longitude)),
                                    (float(new_latitude), float(new_longitude)),
                                ),
                                1,
                            )
                            _LOGGER.debug(
                                "(%s) distance_traveled = %s; compass_bearing = %s",
                                entity_id,
                                distance_traveled,
                                compass_bearing,
                            )
                        else:
                            distance_traveled = 0.0
                            old_distance_from_home = 0.0
                            compass_bearing = 0.0

                        target._attr_extra_state_attributes[ATTR_COMPASS_BEARING] = (
                            compass_bearing
                        )

                        # Geocode gate conditions
                        if new_latitude == "None" or new_longitude == "None":
                            _LOGGER.debug(
                                "(%s) Skipping geocoding due to missing coordinates",
                                entity_id,
                            )
                        elif (
                            distance_traveled < MIN_DISTANCE_TRAVELLED_TO_GEOCODE
                            and old_latitude != "None"
                            and old_longitude != "None"
                            and not force_update
                        ):
                            _LOGGER.debug(
                                "(%s) Skipping geocoding because distance_traveled < %s",
                                entity_id,
                                MIN_DISTANCE_TRAVELLED_TO_GEOCODE,
                            )
                        else:
                            new_locality = "?"

                            raw = target._attr_extra_state_attributes.get(
                                ATTR_LOCATION_TIMESTAMP
                            )
                            new_location_time = parse_ts(raw or currentApiTime)

                            old_location_time = target.this_entity_info.get(
                                "reverse_geocode_location_time", new_location_time
                            )
                            _LOGGER.debug(
                                "(%s) old_location_time = %s",
                                entity_id,
                                old_location_time,
                            )

                            elapsed_seconds = (
                                new_location_time - old_location_time
                            ).total_seconds()
                            _LOGGER.debug(
                                "(%s) elapsed_seconds = %s", entity_id, elapsed_seconds
                            )

                            if elapsed_seconds > 0:
                                speed_during_interval = (
                                    distance_traveled / elapsed_seconds
                                )
                                _LOGGER.debug(
                                    "(%s) speed_during_interval = %s meters/sec",
                                    entity_id,
                                    speed_during_interval,
                                )
                            else:
                                speed_during_interval = 0.0

                            target._attr_extra_state_attributes[ATTR_SPEED] = round(
                                speed_during_interval, 1
                            )

                            if (
                                target._attr_extra_state_attributes.get(
                                    ATTR_REPORTED_STATE, ""
                                ).lower()
                                == STATE_HOME
                            ):
                                # Clamp: "Home" is not a single point
                                distance_from_home = 0.0
                            elif (
                                new_latitude != "None"
                                and new_longitude != "None"
                                and home_latitude is not None
                                and home_longitude is not None
                            ):
                                distance_from_home = round(
                                    distance(
                                        float(new_latitude),
                                        float(new_longitude),
                                        float(home_latitude),
                                        float(home_longitude),
                                    ),
                                    3,
                                )
                            else:
                                distance_from_home = 0.0

                            _LOGGER.debug(
                                "(%s) meters_from_home = %s",
                                entity_id,
                                distance_from_home,
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
                            _LOGGER.debug("(%s) direction = %s", entity_id, direction)
                            target._attr_extra_state_attributes[ATTR_DIRECTION] = (
                                direction
                            )

                            # Default Waze country code from configuration region
                            waze_country_code = (
                                pli.configuration.get(CONF_REGION) or "US"
                            ).upper()

                            # ------- Radar -------------------------------------------
                            if is_provider_enabled(
                                pli.hass, SWITCH_RADAR_GEOCODING_API
                            ):
                                await async_call_radar_reverse_geocoding(target)
                            else:
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_RADAR, None
                                )
                                if previous:
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_RADAR"
                                    )

                            # ------- OpenStreetMap -----------------------------------
                            if is_provider_enabled(
                                pli.hass, SWITCH_OSM_NOMINATIM_GEOCODING_API
                            ):
                                await async_call_open_street_map_reverse_geocoding(
                                    target
                                )
                            else:
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_OPEN_STREET_MAP, None
                                )
                                if previous:
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_OPEN_STREET_MAP"
                                    )

                            # ------- Google Maps -------------------------------------
                            if is_provider_enabled(
                                pli.hass, SWITCH_GOOGLE_GEOCODING_API
                            ):
                                await async_call_google_maps_geocoding(target)
                            else:
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_GOOGLE_MAPS, None
                                )
                                if previous:
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_GOOGLE_MAPS"
                                    )

                            # ------- MapQuest ----------------------------------------
                            if is_provider_enabled(
                                pli.hass, SWITCH_MAPQUEST_GEOCODING_API
                            ):
                                await async_call_mapquest_reverse_geocoding(target)
                            else:
                                previous = target._attr_extra_state_attributes.pop(
                                    ATTR_MAPQUEST, None
                                )
                                if previous:
                                    _LOGGER.debug(
                                        "[handle_reverse_geocode] Removing attribute ATTR_MAPQUEST"
                                    )

                            # ------- All ---------------------------------------------
                            target._attr_extra_state_attributes[ATTR_LOCALITY] = (
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

                            # Driving distance/time (Waze or alternate)
                            await update_driving_miles_and_minutes(
                                pli,
                                target,
                                new_latitude,
                                new_longitude,
                                waze_country_code,
                            )

                        # ---- Determine friendly_name_location and new_bread_crumb
                        reported_state_lower = target._attr_extra_state_attributes.get(
                            ATTR_REPORTED_STATE, ""
                        ).lower()

                        if reported_state_lower in [STATE_HOME, STATE_ON]:
                            new_bread_crumb = "Home"
                            friendly_name_location = "is Home"
                        elif reported_state_lower in [
                            STATE_NOT_HOME,
                            STATE_NOT_HOME,
                            STATE_OFF,
                        ]:
                            new_bread_crumb = STATE_NOT_HOME
                            friendly_name_location = "is Away"
                        else:
                            new_bread_crumb = target._attr_extra_state_attributes.get(
                                ATTR_REPORTED_STATE, ""
                            )
                            friendly_name_location = f"is at {new_bread_crumb}"

                        if ATTR_ZONE in target._attr_extra_state_attributes:
                            current_zone = target._attr_extra_state_attributes[
                                ATTR_ZONE
                            ]
                            current_zone_obj = pli.hass.states.get(
                                f"{ZONE_DOMAIN}.{current_zone}"
                            )
                            if (
                                current_zone_obj is not None
                                and not current_zone.startswith(
                                    IC3_STATIONARY_ZONE_PREFIX
                                )
                            ):
                                current_zone_attrs = current_zone_obj.attributes.copy()
                                if "friendly_name" in current_zone_attrs:
                                    new_bread_crumb = current_zone_attrs[
                                        "friendly_name"
                                    ]
                                    friendly_name_location = f"is at {new_bread_crumb}"

                        if (
                            new_bread_crumb == STATE_NOT_HOME
                            and ATTR_LOCALITY in target._attr_extra_state_attributes
                        ):
                            new_bread_crumb = target._attr_extra_state_attributes[
                                ATTR_LOCALITY
                            ]
                            friendly_name_location = f"is in {new_bread_crumb}"

                        _LOGGER.debug(
                            "(%s) friendly_name_location = %s; new_bread_crumb = %s",
                            target.entity_id,
                            friendly_name_location,
                            new_bread_crumb,
                        )

                        # Append to bread_crumbs
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

                        # Friendly name template (use arg if provided; else fallback to config/default)
                        if template_arg != "NONE":
                            selected_template = template_arg
                        else:
                            selected_template = pli.configuration.get(
                                CONF_FRIENDLY_NAME_TEMPLATE,
                                DEFAULT_FRIENDLY_NAME_TEMPLATE,
                            )

                        # Resolve source entity (handle person indirection)
                        if (
                            ATTR_SOURCE in target._attr_extra_state_attributes
                            and "." in target._attr_extra_state_attributes[ATTR_SOURCE]
                        ):
                            sourceEntity = target._attr_extra_state_attributes[
                                ATTR_SOURCE
                            ]
                            sourceObject = pli.hass.states.get(sourceEntity)
                            if (
                                sourceObject is not None
                                and ATTR_SOURCE in sourceObject.attributes
                                and "." in sourceObject.attributes.get(ATTR_SOURCE, "")
                            ):
                                sourceEntity = sourceObject.attributes[ATTR_SOURCE]
                                sourceObject = pli.hass.states.get(sourceEntity)
                        else:
                            sourceEntity = target.entity_id
                            sourceObject = pli.hass.states.get(sourceEntity)

                        # Prepare template variables
                        friendly_name_variables = {
                            "friendly_name_location": friendly_name_location,
                            "person_name": target._attr_extra_state_attributes.get(
                                "person_name"
                            ),
                            "source": {
                                "entity_id": sourceEntity,
                                "state": getattr(sourceObject, "state", None),
                                "attributes": getattr(sourceObject, "attributes", {}),
                            },
                            "target": {
                                "entity_id": target.entity_id,
                                "state": target.state,
                                "attributes": target._attr_extra_state_attributes,
                            },
                        }

                        try:
                            new_friendly_name = (
                                Template(selected_template)
                                .render(**friendly_name_variables)
                                .replace("()", "")
                                .replace("  ", " ")
                            )
                            target._attr_extra_state_attributes["friendly_name"] = (
                                new_friendly_name
                            )
                            target._attr_name = new_friendly_name
                            _LOGGER.debug("new_friendly_name = %s", new_friendly_name)
                        except TemplateError as err:
                            _LOGGER.error(
                                "Error parsing friendly_name_template: %s", err
                            )

                        await target.async_set_state()
                        target.make_template_sensors()

                        _LOGGER.debug("TARGET_ASYNCIO_LOCK release...")

            except Exception as e:
                _LOGGER.error(
                    "(%s) Exception %s: %s", entity_id, type(e).__name__, str(e)
                )
                _LOGGER.debug(traceback.format_exc())
                pli._attr_extra_state_attributes["api_exception_count"] += 1

            await pli.async_set_state()
            _LOGGER.debug("INTEGRATION_ASYNCIO_LOCK release...")

        _LOGGER.debug("(%s) === Return ===", entity_id)
        return True

    pli.hass.services.async_register(DOMAIN, "reverse_geocode", handle_reverse_geocode)
    return True
