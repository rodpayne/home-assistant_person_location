
import aiohttp
import asyncio
import logging
import os
import random
import traceback

from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.httpx_client import get_async_client
from pywaze.route_calculator import WazeRouteCalculator

from ..const import (
    ATTR_ATTRIBUTION,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    CONF_DISTANCE_DURATION_SOURCE,
    CONF_GOOGLE_API_KEY,
    CONF_LANGUAGE,
    CONF_MAPQUEST_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
    CONF_REGION,
    DEFAULT_API_KEY_NOT_SET,
    FAR_AWAY_METERS,
    get_waze_region,
    INFO_LOCATION_LATITUDE,
    INFO_LOCATION_LONGITUDE,
    METERS_PER_KM,
    METERS_PER_MILE,
    MIN_DISTANCE_TRAVELLED_TO_GEOCODE,
    THROTTLE_INTERVAL,
    WAZE_MIN_METERS_FROM_HOME,
)

_LOGGER = logging.getLogger(__name__)

RADAR_BASE_URL = "https://api.radar.io/v1/route/distance"

async def update_driving_miles_and_minutes(
    pli,
    target,
    new_latitude,
    new_longitude,
    waze_country_code,
):
    """ 
    Input target._attr_extra_state_attributes:
        ATTR_METERS_FROM_HOME
        ATTR_MILES_FROM_HOME

    Updates target._attr_extra_state_attributes:
        ATTR_DRIVING_MILES
        ATTR_DRIVING_MINUTES
        ATTR_ATTRIBUTION

    May update pli.attributes:
        "waze_error_count"
    """

    try:
        distance_duration_source = pli.configuration[CONF_DISTANCE_DURATION_SOURCE]
        entity_id = target.entity_id

        _LOGGER.debug("[update_driving_miles_and_minutes] (%s) Source=%s",
            entity_id,
            distance_duration_source,
            )

        if distance_duration_source == "none":
            return

        # If we’re already “home,” skip routing
        if target._attr_extra_state_attributes[ATTR_METERS_FROM_HOME] < WAZE_MIN_METERS_FROM_HOME:
            target._attr_extra_state_attributes[ATTR_DRIVING_MILES] = target._attr_extra_state_attributes[ATTR_MILES_FROM_HOME]
            target._attr_extra_state_attributes[ATTR_DRIVING_MINUTES] = "0"
            _LOGGER.debug("[update_driving_miles_and_minutes] Too close to home for lookup to matter.")
            return

        from_location = f"{new_latitude},{new_longitude}"
        _LOGGER.debug("[update_driving_miles_and_minutes] from_location: " + from_location)

        to_location = (
            f"{pli.attributes['home_latitude']},"
            f"{pli.attributes['home_longitude']}"
        )
        _LOGGER.debug("[update_driving_miles_and_minutes] to_location: " + to_location)

        #------- Waze --------------------------------------------------

        if distance_duration_source == "waze":

            waze_region = get_waze_region(waze_country_code)
            _LOGGER.debug("[update_driving_miles_and_minutes] waze_region: " + waze_region)

            # First attempt: HA-managed service
            try:
                if not pli.hass.services.has_service("waze_travel_time", "get_travel_times"):
                    raise ServiceNotFound("waze_travel_time", "get_travel_times")

                data = await pli.hass.services.async_call(
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
                routes = data.get("routes", [])
                if not routes:
                    raise ValueError("No routes from HA service")

                # Pick first or apply your street‐name filter here
                best = routes[0]
                duration_min = best["duration"]
                distance_km = best["distance"]

                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] (%s) Waze service returned duration (%s), distance_km (%s)",
                    entity_id,
                    duration_min,
                    distance_km,
                )

            except Exception as service_err:
                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] (%s) Waze service failed (%s), falling back to pywaze",
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
                    routes = await client.calc_routes(
                        from_location,
                        to_location,
                        avoid_toll_roads=True,
                        avoid_subscription_roads=True,
                        avoid_ferries=True,
                    )
                    if not routes:
                        raise ValueError("No routes from pywaze")

                    route = routes[0]
                    duration_min = route.duration
                    distance_km = route.distance

                    _LOGGER.debug(
                        "[update_driving_miles_and_minutes] (%s) pywaze returned duration (%s), distance_km (%s)",
                        entity_id,
                        duration_min,
                        distance_km,
                    )

                except Exception as pw_err:
                    _LOGGER.error(
                        "[update_driving_miles_and_minutes] (%s) pywaze fallback failed %s: %s",
                        entity_id,
                        type(pw_err).__name__,
                        pw_err,
                    )
                    pli.attributes["waze_error_count"] = (
                        pli.attributes.get("waze_error_count", 0) + 1
                    )
                    target._attr_extra_state_attributes[ATTR_DRIVING_MILES] = target._attr_extra_state_attributes[ATTR_MILES_FROM_HOME]
                    return
            # Waze was not used in reverse_geocode, so give attribution if used here
            target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += '"Data by Waze App. https://waze.com"; '

        #------- Radar -------------------------------------------------

        elif distance_duration_source == "radar":

            async with aiohttp.ClientSession() as session:
                data = await radar_calc_distance(pli,from_location, to_location, modes="car", units="metric", session=session)
                duration_min, distance_m = extract_duration_distance(data)
            distance_km = distance_m / METERS_PER_KM

        # ------- Google --------------------------------------------------

        elif distance_duration_source == "google_maps":

            GOOGLE_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

            api_key = pli.configuration.get(CONF_GOOGLE_API_KEY, DEFAULT_API_KEY_NOT_SET)
            if not api_key or api_key == DEFAULT_API_KEY_NOT_SET:
                _LOGGER.error("[update_driving_miles_and_minutes] CONF_GOOGLE_API_KEY not set")
                return

            async with aiohttp.ClientSession() as session:
                params = {
                    "origins": from_location,
                    "destinations": to_location,
                    "mode": "driving",
                    "units": "metric",
                    "key": api_key,
                }
                async with session.get(GOOGLE_DISTANCE_MATRIX_URL, params=params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Google API error {resp.status}: {text}")
                    data = await resp.json()
                    rows = data.get("rows", [])
                    if not rows or not rows[0].get("elements"):
                        raise ValueError("No routes returned by Google API")

                    element = rows[0]["elements"][0]
                    duration_sec = element["duration"]["value"]   # seconds
                    distance_m = element["distance"]["value"]     # meters

                    duration_min = duration_sec / 60.0
                    distance_km = distance_m / 1000.0

        # ------- Mapbox --------------------------------------------------

        elif distance_duration_source == "mapbox":

            async with aiohttp.ClientSession() as session:
                minutes, km = await mapbox_calc_distance(pli, from_location, to_location, session=session)
                duration_min = minutes
                distance_km = km

        #------- Unknown -------------------------------------------------

        else:

            _LOGGER.debug("[update_driving_miles_and_minutes] Source (%s) not handled.",
                distance_duration_source,
                )
            return

        #------- Common post‐processing

        miles = distance_km * METERS_PER_KM / METERS_PER_MILE
        if miles <= 0:
            display_miles = target._attr_extra_state_attributes[ATTR_MILES_FROM_HOME]
        elif miles >= 100:
            display_miles = round(miles, 0)
        elif miles >= 10:
            display_miles = round(miles, 1)
        else:
            display_miles = round(miles, 2)

        target._attr_extra_state_attributes[ATTR_DRIVING_MILES] = str(display_miles)
        target._attr_extra_state_attributes[ATTR_DRIVING_MINUTES] = str(round(duration_min, 1))

        _LOGGER.debug(
            "[update_driving_miles_and_minutes] %s returned duration=%.1f minutes, distance=%s miles",
            distance_duration_source,
            duration_min,
            display_miles,
        )

    except Exception as e:
        _LOGGER.error(
            "[update_driving_miles_and_minutes] Exception %s: %s" % (type(e).__name__, str(e))
        )
        _LOGGER.debug(traceback.format_exc())
        pli.attributes["api_error_count"] += 1

#------- Radar -------------------------------------------------

async def radar_calc_distance(
    pli,
    origin: str,
    destination: str,
    modes: str = "car",
    units: str = "metric",
    session: aiohttp.ClientSession = None,
    max_retries: int = 3,
    base_backoff: float = 1.0,
) -> dict:
    """
    Async Radar Distance API call with retry/backoff for 429 and network errors.
    Returns JSON with duration and distance for the given mode.
    """
    
    api_key = pli.configuration.get(CONF_RADAR_API_KEY, DEFAULT_API_KEY_NOT_SET)

    if not api_key or api_key == DEFAULT_API_KEY_NOT_SET:
        raise RuntimeError("CONF_RADAR_API_KEY is not set.")

    params = {
        "origin": origin,
        "destination": destination,
        "modes": modes,
        "units": units,
    }
    headers = {"Authorization": api_key}

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        for attempt in range(max_retries):
            try:
                async with session.get(RADAR_BASE_URL, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        # Rate limit: exponential backoff with jitter
                        wait = base_backoff * (2 ** attempt) + random.uniform(0, 0.5)
                        print(f"Radar API rate-limited (429). Retrying in {wait:.1f}s...")
                        await asyncio.sleep(wait)
                    else:
                        text = await resp.text()
                        raise RuntimeError(f"Radar API error {resp.status}: {text}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Network error: retry with backoff
                wait = base_backoff * (2 ** attempt) + random.uniform(0, 0.5)
                print(f"Network error: {e}. Retrying in {wait:.1f}s...")
                await asyncio.sleep(wait)
        raise RuntimeError("Radar API request failed after retries.")
    finally:
        if close_session:
            await session.close()


def extract_duration_distance(data: dict, mode: str = "car") -> tuple:
    """
    Extract duration (seconds) and distance (meters) from Radar Distance API response.
    """
    routes = data.get("routes", {})
    mode_obj = routes.get(mode, {})
    duration = (mode_obj.get("duration") or {}).get("value", 0.0)  # minutes
    distance = (mode_obj.get("distance") or {}).get("value", 0.0)  # meters
    return duration, distance

# ------- Mapbox --------------------------------------------------

async def mapbox_calc_distance(
    pli,
    origin: str,
    destination: str,
    profile: str = "driving",
    session: aiohttp.ClientSession = None,
) -> tuple:
    """
    Query Mapbox Directions API for duration and distance.
    Returns (minutes, kilometers).
    """
    
    MAPBOX_DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox"

    api_key = pli.configuration.get(CONF_MAPBOX_API_KEY,DEFAULT_API_KEY_NOT_SET)
    if not api_key or api_key == DEFAULT_API_KEY_NOT_SET:
        _LOGGER.error("[update_driving_miles_and_minutes] Mapbox API key not set")
        return

    # Mapbox expects "lon,lat" order
    origin_lat, origin_lon = origin.split(",")
    dest_lat, dest_lon = destination.split(",")
    coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"

    url = f"{MAPBOX_DIRECTIONS_URL}/{profile}/{coords}"
    params = {
        "access_token": api_key,
        "geometries": "geojson",
        "overview": "simplified",
    }

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Mapbox API error {resp.status}: {text}")

            data = await resp.json()
            routes = data.get("routes", [])
            if not routes:
                raise ValueError("No routes returned by Mapbox API")

            route = routes[0]
            duration_sec = route["duration"]   # seconds
            distance_m = route["distance"]     # meters

            minutes = duration_sec / 60.0
            kilometers = distance_m / 1000.0

            _LOGGER.debug(
                "[update_driving_miles_and_minutes] Mapbox returned duration=%.1f min, distance=%.2f km",
                minutes,
                kilometers,
            )

            return minutes, kilometers
    finally:
        if close_session:
            await session.close()
