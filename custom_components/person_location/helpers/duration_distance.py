import asyncio
import logging
import os
import random
import traceback

import aiohttp
from pywaze.route_calculator import WazeRouteCalculator

from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.httpx_client import get_async_client

from ..const import (
    ATTR_ATTRIBUTION,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    CONF_DISTANCE_DURATION_SOURCE,
    CONF_GOOGLE_API_KEY,
    CONF_LANGUAGE,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
    CONF_REGION,
    DEFAULT_API_KEY_NOT_SET,
    METERS_PER_KM,
    METERS_PER_MILE,
    PERSON_LOCATION_INTEGRATION,
    SWITCH_GOOGLE_DISTANCE_API,
    SWITCH_MAPBOX_DIRECTIONS_API,
    SWITCH_RADAR_DISTANCE_API,
    SWITCH_WAZE_TRAVEL_TIME,
    WAZE_MIN_METERS_FROM_HOME,
    WAZE_REGIONS,
    error_once,
    get_home_coordinates,
)
from ..sensor import (
    PERSON_LOCATION_TARGET,
)
from ..switch import (
    is_provider_enabled,
    record_api_error,
    record_api_success,
)

_LOGGER = logging.getLogger(__name__)


def get_waze_region(country_code: str) -> str:
    """Determine Waze region from country code or Waze region setting."""
    country_code = country_code.lower()
    if country_code in ("us", "ca", "mx"):
        return "us"
    if country_code in WAZE_REGIONS:
        return country_code
    return "eu"


async def update_driving_miles_and_minutes(
    pli: PERSON_LOCATION_INTEGRATION,
    target: PERSON_LOCATION_TARGET,
    new_latitude: str,
    new_longitude: str,
    waze_country_code: str,
) -> None:
    """
    Update driving duration and distance.

    Input:
      target._attr_extra_state_attributes
        ATTR_METERS_FROM_HOME
        ATTR_MILES_FROM_HOME

    Update:
      target._attr_extra_state_attributes
        ATTR_DRIVING_MILES
        ATTR_DRIVING_MINUTES
        ATTR_ATTRIBUTION

    May update:
      pli.attributes
        "waze_error_count"
    """
    try:
        distance_duration_source = pli.configuration[CONF_DISTANCE_DURATION_SOURCE]
        entity_id = target.entity_id

        _LOGGER.debug(
            "[update_driving_miles_and_minutes] (%s) Source=%s",
            entity_id,
            distance_duration_source,
        )

        if distance_duration_source == "none":
            return

        # If we’re already “home,” skip routing
        if (
            target._attr_extra_state_attributes[ATTR_METERS_FROM_HOME]
            < WAZE_MIN_METERS_FROM_HOME
        ):
            target._attr_extra_state_attributes[ATTR_DRIVING_MILES] = (
                target._attr_extra_state_attributes[ATTR_MILES_FROM_HOME]
            )
            target._attr_extra_state_attributes[ATTR_DRIVING_MINUTES] = "0"
            _LOGGER.debug(
                "[update_driving_miles_and_minutes] Too close to home for lookup to matter."
            )
            return

        from_location = f"{new_latitude},{new_longitude}"
        _LOGGER.debug(
            "[update_driving_miles_and_minutes] from_location: " + from_location
        )

        home_latitude, home_longitude = get_home_coordinates(pli.hass)
        to_location = f"{home_latitude},{home_longitude}"

        _LOGGER.debug("[update_driving_miles_and_minutes] to_location: %s", to_location)
        if to_location == (None, None):
            return

        # ------- Waze --------------------------------------------------

        if distance_duration_source == "waze":
            provider_id = SWITCH_WAZE_TRAVEL_TIME
            if not is_provider_enabled(pli.hass, provider_id):
                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] %s not enabled",
                    provider_id,
                )
                return

            waze_region = get_waze_region(waze_country_code)
            _LOGGER.debug(
                "[update_driving_miles_and_minutes] waze_region: " + waze_region
            )

            # First attempt: HA-managed service
            try:
                if not pli.hass.services.has_service(
                    "waze_travel_time", "get_travel_times"
                ):
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
                    _LOGGER.debug(
                        "[update_driving_miles_and_minutes] (%s) pywaze fallback failed %s: %s",
                        entity_id,
                        type(pw_err).__name__,
                        pw_err,
                    )
                    pli.attributes["waze_error_count"] = (
                        pli.attributes.get("waze_error_count", 0) + 1
                    )
                    if pli.attributes["waze_error_count"] > 10:
                        error_count_exceeded = True
                    else:
                        error_count_exceeded = False
                    error_message = f"Wyze service and pywaze failed {type(pw_err).__name__}: {pw_err}"
                    record_api_error(
                        pli.hass,
                        provider_id,
                        error_message,
                        turn_off=error_count_exceeded,
                    )

                    target._attr_extra_state_attributes[ATTR_DRIVING_MILES] = (
                        target._attr_extra_state_attributes[ATTR_MILES_FROM_HOME]
                    )
                    return

            # Waze was not used in reverse_geocode, so give attribution if used here
            target._attr_extra_state_attributes[ATTR_ATTRIBUTION] += (
                '"Data by Waze App. https://waze.com"; '
            )

            record_api_success(pli.hass, provider_id)

        # ------- Radar -------------------------------------------------

        elif distance_duration_source == "radar":
            provider_id = SWITCH_RADAR_DISTANCE_API
            if not is_provider_enabled(pli.hass, provider_id):
                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] %s not enabled",
                    provider_id,
                )
                return

            async with aiohttp.ClientSession() as session:
                data = await radar_calc_distance(
                    pli,
                    from_location,
                    to_location,
                    modes="car",
                    units="metric",
                    session=session,
                )
                duration_min, distance_m = extract_duration_distance(data)
            distance_km = distance_m / METERS_PER_KM

            record_api_success(pli.hass, provider_id)

        # ------- Google --------------------------------------------------

        elif distance_duration_source == "google_maps":
            GOOGLE_DISTANCE_MATRIX_URL = (
                "https://maps.googleapis.com/maps/api/distancematrix/json"
            )

            api_key = pli.configuration.get(
                CONF_GOOGLE_API_KEY, DEFAULT_API_KEY_NOT_SET
            )
            if not api_key or api_key == DEFAULT_API_KEY_NOT_SET:
                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] CONF_GOOGLE_API_KEY not set"
                )
                return
            provider_id = SWITCH_GOOGLE_DISTANCE_API
            if not is_provider_enabled(pli.hass, provider_id):
                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] %s not enabled",
                    provider_id,
                )
                return

            async with aiohttp.ClientSession() as session:
                params = {
                    "origins": from_location,
                    "destinations": to_location,
                    "mode": "driving",
                    "units": "metric",
                    "key": api_key,
                }
                async with session.get(
                    GOOGLE_DISTANCE_MATRIX_URL, params=params
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Google API error {resp.status}: {text}")
                    data = await resp.json()
                    rows = data.get("rows", [])
                    if not rows or not rows[0].get("elements"):
                        raise ValueError("No routes returned by Google API")

                    element = rows[0]["elements"][0]
                    duration_sec = element["duration"]["value"]  # seconds
                    distance_m = element["distance"]["value"]  # meters

                    duration_min = duration_sec / 60.0
                    distance_km = distance_m / 1000.0

                record_api_success(pli.hass, provider_id)

        # ------- Mapbox --------------------------------------------------

        elif distance_duration_source == "mapbox":
            provider_id = SWITCH_MAPBOX_DIRECTIONS_API
            if not is_provider_enabled(pli.hass, provider_id):
                _LOGGER.debug(
                    "[update_driving_miles_and_minutes] %s not enabled",
                    provider_id,
                )
                return

            async with aiohttp.ClientSession() as session:
                minutes, km = await mapbox_calc_distance(
                    pli, from_location, to_location, session=session
                )
                duration_min = minutes
                distance_km = km

            record_api_success(pli.hass, provider_id)

        # ------- Unknown -------------------------------------------------

        else:
            error_once(
                _LOGGER,
                (
                    "[update_driving_miles_and_minutes] Source (%s) not handled.",
                    distance_duration_source,
                ),
            )
            return

        # ------- Common post‐processing

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
        target._attr_extra_state_attributes[ATTR_DRIVING_MINUTES] = str(
            round(duration_min, 1)
        )

        _LOGGER.debug(
            "[update_driving_miles_and_minutes] %s returned duration=%.1f minutes, distance=%s miles",
            distance_duration_source,
            duration_min,
            display_miles,
        )

    except Exception as e:
        _LOGGER.error(
            "[update_driving_miles_and_minutes] Exception %s: %s",
            type(e).__name__,
            str(e),
        )
        _LOGGER.debug(traceback.format_exc())
        pli.attributes["api_exception_count"] += 1


# ------- Radar -------------------------------------------------


async def radar_calc_distance(
    pli: PERSON_LOCATION_INTEGRATION,
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
    RADAR_BASE_URL = "https://api.radar.io/v1/route/distance"

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
                async with session.get(
                    RADAR_BASE_URL, headers=headers, params=params
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        # Rate limit: exponential backoff with jitter
                        wait = base_backoff * (2**attempt) + random.uniform(0, 0.5)
                        print(
                            f"Radar API rate-limited (429). Retrying in {wait:.1f}s..."
                        )
                        await asyncio.sleep(wait)
                    else:
                        text = await resp.text()
                        raise RuntimeError(f"Radar API error {resp.status}: {text}")
            except (TimeoutError, aiohttp.ClientError) as e:
                # Network error: retry with backoff
                wait = base_backoff * (2**attempt) + random.uniform(0, 0.5)
                print(f"Network error: {e}. Retrying in {wait:.1f}s...")
                await asyncio.sleep(wait)
        raise RuntimeError("Radar API request failed after retries.")
    finally:
        if close_session:
            await session.close()


def extract_duration_distance(data: dict, mode: str = "car") -> tuple:
    """Extract duration (seconds) and distance (meters) from Radar API response."""
    routes = data.get("routes", {})
    mode_obj = routes.get(mode, {})
    duration = (mode_obj.get("duration") or {}).get("value", 0.0)  # minutes
    distance = (mode_obj.get("distance") or {}).get("value", 0.0)  # meters
    return duration, distance


# ------- Mapbox --------------------------------------------------


async def mapbox_calc_distance(
    pli: PERSON_LOCATION_INTEGRATION,
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

    api_key = pli.configuration.get(CONF_MAPBOX_API_KEY, DEFAULT_API_KEY_NOT_SET)
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
            duration_sec = route["duration"]  # seconds
            distance_m = route["distance"]  # meters

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
