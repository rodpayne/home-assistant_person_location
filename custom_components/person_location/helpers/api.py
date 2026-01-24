"""API Client Wrapper with retries and exponential backoff."""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import socket
import traceback

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    CONF_LANGUAGE,
    CONF_REGION,
    DATA_CONFIGURATION,
    DATA_INTEGRATION,
    DEFAULT_API_KEY_NOT_SET,
    DOMAIN,
    PERSON_LOCATION_INTEGRATION,
    STATE_ABBREVIATIONS,
    SWITCH_GOOGLE_GEOCODING_API,
    SWITCH_MAPBOX_STATIC_IMAGE_API,
    SWITCH_MAPQUEST_GEOCODING_API,
    SWITCH_OSM_NOMINATIM_GEOCODING_API,
    SWITCH_RADAR_GEOCODING_API,
    get_home_coordinates,
)
from ..switch import (
    record_api_error,
    record_api_success,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)

HEADERS = {"Content-type": "application/json; charset=UTF-8"}
RETRIES = 2  # number of retry attempts
TIMEOUT = 10  # seconds

# ------- Entry point for a generic API call:


async def async_person_location_get_api_data(
    hass: HomeAssistant,
    method: str,
    url: str,
    data: dict = {},
    *,
    headers: dict = HEADERS,
    provider_id: str = None,
    retries: int = RETRIES,
    timeout: float = TIMEOUT,
) -> dict:
    """Wrap call to PERSON_LOCATION_CLIENT.async_get_api_data."""
    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data(method, url, data, headers, timeout, retries)
    if provider_id:
        authentication_failed = resp.get("status") == 401
        if resp["ok"]:
            record_api_success(hass, provider_id)
            return resp
        else:
            record_api_error(
                hass, provider_id, resp["error"], turn_off=authentication_failed
            )
            return resp
    return resp


# ------- Entry points for specific API calls and error checking:


async def async_get_google_maps_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Google Maps Geocoding API."""
    pli: PERSON_LOCATION_INTEGRATION = hass.data[DOMAIN][DATA_INTEGRATION]
    provider_id = SWITCH_GOOGLE_GEOCODING_API
    url = (
        "https://maps.googleapis.com/maps/api/geocode/json?language="
        + pli.configuration[CONF_LANGUAGE]
        + "&region="
        + pli.configuration[CONF_REGION]
        + "&latlng="
        + str(latitude)
        + ","
        + str(longitude)
        + "&key="
        + key
    )
    client = PERSON_LOCATION_CLIENT(pli.hass)
    resp = await client.async_get_api_data("get", url)
    authentication_failed = resp.get("status") == 401
    if not resp["ok"]:
        record_api_error(
            hass, provider_id, resp["error"], turn_off=authentication_failed
        )
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200 and resp.get("data"):
        # resp["data"]["status"] -> Google API status field (e.g. "OK", "ZERO_RESULTS", "REQUEST_DENIED")
        api_status = resp["data"].get("status")
        if api_status == "OK":
            record_api_success(hass, provider_id)
            return resp
        _LOGGER.debug(
            "[async_get_google_maps_geocode] Google API status: %s", api_status
        )
        resp["error"] = f"API status: {api_status}"
        if api_status == "REQUEST_DENIED":
            authentication_failed = True
    else:
        _LOGGER.debug(
            "[async_get_google_maps_geocode] Google HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data").replace(key, "********"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    record_api_error(hass, provider_id, resp["error"], turn_off=authentication_failed)
    return resp


async def async_get_mapbox_static_image(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Mapbox Static Image API."""
    provider_id = SWITCH_MAPBOX_STATIC_IMAGE_API
    url = f"https://api.mapbox.com/styles/v1/mapbox/streets-v11/static/{longitude},{latitude},5,0/300x200?access_token={key}"
    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data("get", url)
    if not resp["ok"]:
        authentication_failed = resp.get("status") == 401
        record_api_error(
            hass, provider_id, resp["error"], turn_off=authentication_failed
        )
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200:
        record_api_success(hass, provider_id)
        return resp
    else:
        _LOGGER.debug(
            "[async_get_mapbox_geocode] Mapbox HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    record_api_error(hass, provider_id, resp["error"])
    return resp


async def async_get_mapquest_reverse_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Mapquest Reverse Geocoding API."""
    provider_id = SWITCH_MAPQUEST_GEOCODING_API

    url = (
        "https://www.mapquestapi.com/geocoding/v1/reverse"
        + "?location="
        + str(latitude)
        + ","
        + str(longitude)
        + "&thumbMaps=false"
        + "&key="
        + key
    )
    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data("get", url)
    if not resp["ok"]:
        authentication_failed = resp.get("status") == 401
        record_api_error(
            hass, provider_id, resp["error"], turn_off=authentication_failed
        )
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200:
        record_api_success(hass, provider_id)
        return resp
    else:
        _LOGGER.debug(
            "[async_get_mapquest_reverse_geocoding] Mapquest HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    record_api_error(hass, provider_id, resp["error"])
    return resp


async def async_get_open_street_map_reverse_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Nominatim Reverse Geocoding (OpenStreetMap) API."""
    provider_id = SWITCH_OSM_NOMINATIM_GEOCODING_API

    if key:
        url = (
            "https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat="
            + str(latitude)
            + "&lon="
            + str(longitude)
            + "&addressdetails=1&namedetails=1&zoom=18&limit=1&email="
            + key
        )
    else:
        url = (
            "https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat="
            + str(latitude)
            + "&lon="
            + str(longitude)
            + "&addressdetails=1&namedetails=1&zoom=18&limit=1"
        )

    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data("get", url)
    if not resp["ok"]:
        authentication_failed = resp.get("status") == 401
        record_api_error(
            hass, provider_id, resp["error"], turn_off=authentication_failed
        )
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200:
        record_api_success(hass, provider_id)
        return resp
    else:
        _LOGGER.debug(
            "[async_get_open_street_map_reverse_geocoding] Open Street Map HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
        resp["ok"] = False
    record_api_error(hass, provider_id, resp["error"])
    return resp


async def async_get_radar_reverse_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Radar Reverse Geocoding API."""
    provider_id = SWITCH_RADAR_GEOCODING_API

    url = f"https://api.radar.io/v1/geocode/reverse?coordinates={latitude},{longitude}"
    headers = {"Authorization": key, "Content-Type": "application/json"}
    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data("get", url, headers=headers)
    if not resp["ok"]:
        authentication_failed = resp.get("status") in (401, 403)
        record_api_error(
            hass, provider_id, resp["error"], turn_off=authentication_failed
        )
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200 and resp.get("data"):
        # resp["data"]["meta"]["code"] -> Radar API response code (e.g. 200, 400, 401, 403, 404, 429, 500)
        api_code = resp["data"].get("meta", {}).get("code")
        if api_code == 200:
            record_api_success(hass, provider_id)
            return resp
        api_message = resp["data"].get("meta").get("message")
        _LOGGER.debug(
            "[async_get_radar_reverse_geocoding] Radar API code: %s, message: %s",
            api_code,
            api_message,
        )
        resp["error"] = f"API status: {api_code} message: {api_message}"
        resp["status"] = api_code
    else:
        _LOGGER.debug(
            "[async_get_radar_reverse_geocoding] Radar HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    record_api_error(hass, provider_id, resp["error"])
    return resp


# -----------------------------------------------------------------------------
# Normalized API Response Schema
#
# All API calls return a dictionary with a consistent structure:
#
# {
#     "data": dict|None,    # Parsed JSON payload, or None if parsing failed
#     "error": str|None,    # Populated only if retries exhausted or unexpected error
#     "headers": dict,      # Response headers
#     "ok": bool,           # Convenience flag: True if status < 400
#     "status": int,        # HTTP status code (e.g. 200, 404, 500)
#     "url": str,           # Final request URL
# }
#
# This schema ensures downstream code can reliably check both HTTP status
# and parsed payload without guessing the failure mode.
# -----------------------------------------------------------------------------


async def _normalize_response(response: aiohttp.ClientResponse) -> dict:
    """Normalize aiohttp response into a consistent schema."""
    try:
        payload = await response.json(content_type=None)  # allow non-JSON gracefully
    except Exception:
        payload = None

    return {
        "ok": response.status < 400,  # quick boolean flag
        "status": response.status,  # HTTP status code
        "url": str(response.url),
        "headers": dict(response.headers),
        "data": payload,  # parsed JSON or None
    }


def get_retry_delay(headers: dict, default: float = 1.0) -> float:
    """Return delay in seconds based on Retry-After header."""
    retry_after = headers.get("Retry-After")
    if not retry_after:
        return default

    # Case 1: integer seconds
    if retry_after.isdigit():
        return float(retry_after)

    # Case 2: HTTP date
    try:
        retry_time = parsedate_to_datetime(retry_after)
        now = datetime.now(timezone.utc)
        delay = (retry_time - now).total_seconds()
        return max(delay, default)
    except Exception:
        return default


# ------- Make the actual API call:


class PERSON_LOCATION_CLIENT:
    """API Client Wrapper with retries and exponential backoff."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize class PersonLocationClient."""
        # Use HA-managed aiohttp client
        self._session: aiohttp.ClientSession = async_get_clientsession(hass)

    async def async_get_api_data(
        self,
        method: str,
        url: str,
        data: dict = {},
        headers: dict = HEADERS,
        timeout: float = TIMEOUT,
        retries: int = RETRIES,
    ) -> dict:
        """Get data from the API. Placeholder for cache, etc."""
        return await self._api_wrapper(method, url, data, headers, timeout, retries)

    async def _api_wrapper(
        self,
        method: str,
        url: str,
        data: dict = {},
        headers: dict = {},
        timeout: float = TIMEOUT,
        retries: int = RETRIES,
    ) -> dict:
        """Get information from the API with retries and exponential backoff."""
        _LOGGER.debug("[_api_wrapper] %s", url.split("?", 1)[0])

        last_error = None

        for attempt in range(1, RETRIES + 1):
            try:
                async with async_timeout.timeout(TIMEOUT):
                    if method == "get":
                        response = await self._session.get(url, headers=headers)
                    elif method == "put":
                        response = await self._session.put(
                            url, headers=headers, json=data
                        )
                    elif method == "patch":
                        response = await self._session.patch(
                            url, headers=headers, json=data
                        )
                    elif method == "post":
                        response = await self._session.post(
                            url, headers=headers, json=data
                        )
                    else:
                        raise ValueError(f"Unsupported method: {method}")

                    if response.status >= 400:
                        last_error = f"HTTP error fetching {url.split('?', 1)[0]} - status: {response.status}"
                        no_retry = response.status in (400, 401, 403, 404, 422)
                        _LOGGER.debug(
                            "Attempt %s/%s %sfailed due to: %s",
                            attempt,
                            RETRIES,
                            "(no retry) " if no_retry else "",
                            last_error,
                        )
                        if no_retry:
                            break
                    else:
                        # ------ Return normal response:
                        return await _normalize_response(response)

            except TimeoutError as exception:
                last_error = (
                    f"Timeout error fetching {url.split('?', 1)[0]} - {exception}"
                )
                _LOGGER.debug(
                    "Attempt %s/%s failed: %s",
                    attempt,
                    RETRIES,
                    last_error,
                )

            except (aiohttp.ClientError, socket.gaierror) as exception:
                last_error = f"HTTP error fetching {url.split('?', 1)[0]} - {exception}"
                _LOGGER.debug(
                    "Attempt %s/%s failed due to client error: %s",
                    attempt,
                    RETRIES,
                    last_error,
                )

            except Exception as e:  # pylint: disable=broad-except
                last_error = f"Unexpected error: {type(e).__name__}: {e}"
                _LOGGER.debug(
                    "Attempt %s/%s failed due to unexpected error: %s",
                    attempt,
                    RETRIES,
                    last_error,
                )
                _LOGGER.debug(traceback.format_exc())

            if attempt < RETRIES:
                # ------- Pause and then retry error:
                if response and response.status and response.status == 429:
                    delay = get_retry_delay(response.headers)
                else:
                    delay = 2 ** (attempt - 1)
                _LOGGER.debug("Retrying in %s seconds...", delay)
                await asyncio.sleep(delay)

        # ------- Return error response:
        _LOGGER.debug("All attempts failed for %s", url)
        return {
            "status": response.status,
            "ok": False,
            "data": None,
            "error": last_error or "Unknown error",
        }


# ----------------- API Key Validity Tests -----------------


async def async_test_google_api_key(hass: HomeAssistant, key: str) -> bool:
    """Test to see if the API key is valid."""
    pli = hass.data[DOMAIN].get(DATA_INTEGRATION, {})
    if key == DEFAULT_API_KEY_NOT_SET:
        return True
    latitude, longitude = get_home_coordinates(hass)
    if latitude is None:
        return False

    resp = await async_get_google_maps_geocoding(hass, key, latitude, longitude)
    if resp.get("ok"):
        # Save home_state and home_country_code for later comparisons
        google_decoded = resp.get("data")
        if "results" in google_decoded:
            for component in google_decoded["results"][0]["address_components"]:
                if "country" in component["types"]:
                    home_country_code = component["short_name"].upper()
                if "administrative_area_level_1" in component["types"]:
                    home_state = component["long_name"]
            _LOGGER.debug(
                "[async_test_google_api_key] Google home_state = %s, home_country_code = %s",
                home_state,
                home_country_code,
            )
            pli.home_country_code = home_country_code
            pli.home_state = home_state
        return True
    else:
        return False


async def async_test_mapbox_api_key(hass: HomeAssistant, key: str) -> bool:
    """Test to see if the API key is valid."""
    # pli = hass.data[DOMAIN].get(DATA_INTEGRATION, {})
    # cfg = hass.data[DOMAIN].get(DATA_CONFIGURATION, {})
    if key == DEFAULT_API_KEY_NOT_SET:
        return True
    latitude, longitude = get_home_coordinates(hass)
    if latitude is None:
        return False
    resp = await async_get_mapbox_static_image(hass, key, latitude, longitude)
    if resp.get("ok"):
        return True
    else:
        return False


async def async_test_mapquest_api_key(hass: HomeAssistant, key: str) -> bool:
    """Test to see if the API key is valid."""
    pli = hass.data[DOMAIN].get(DATA_INTEGRATION, {})
    if key == DEFAULT_API_KEY_NOT_SET:
        return True
    latitude, longitude = get_home_coordinates(hass)
    if latitude is None:
        return False
    resp = await async_get_mapquest_reverse_geocoding(hass, key, latitude, longitude)
    if resp.get("ok"):
        # Save home_state and home_country_code for later comparisons
        mapquest_decoded = resp.get("data")
        if (
            "results" in mapquest_decoded
            and "locations" in mapquest_decoded["results"][0]
        ):
            mapquest_location = mapquest_decoded["results"][0]["locations"][0]

            if "adminArea1" in mapquest_location and "adminArea3" in mapquest_location:
                home_country_code = mapquest_location["adminArea1"]
                home_state_code = mapquest_location["adminArea3"]
                home_state = STATE_ABBREVIATIONS.get(home_state_code, home_state_code)
                _LOGGER.debug(
                    "[async_test_mapquest_api_key] mapquest home_state = %s, home_country_code = %s",
                    home_state,
                    home_country_code,
                )
                pli.home_country_code = home_country_code
                pli.home_state = home_state
        return True
    else:
        return False


async def async_test_osm_api_key(hass: HomeAssistant, key: str) -> bool:
    """Test to see if the API key is valid."""
    pli = hass.data[DOMAIN].get(DATA_INTEGRATION, {})
    import re

    if key == DEFAULT_API_KEY_NOT_SET:
        return True
    try:
        regex = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$"
        if re.search(regex, key):
            latitude, longitude = get_home_coordinates(hass)
            if latitude is None:
                return False
            resp = await async_get_open_street_map_reverse_geocoding(
                hass, key, latitude, longitude
            )
            if resp.get("ok"):
                # Save home_state and home_countryCode for later comparisons
                osm_decoded = resp["data"]
                if (
                    "country_code" in osm_decoded["address"]
                    and "state" in osm_decoded["address"]
                ):
                    home_country_code = osm_decoded["address"]["country_code"].upper()
                    home_state = osm_decoded["address"]["state"]
                    _LOGGER.debug(
                        "[async_test_osm_api_key] OSM home_state = %s, home_country_code = %s",
                        home_state,
                        home_country_code,
                    )
                    pli.home_country_code = home_country_code
                    pli.home_state = home_state
                return True
    except Exception as e:
        _LOGGER.debug("[_test_osm_api_key] Mail format failed: %s", e)
    return False


async def async_test_radar_api_key(hass: HomeAssistant, key: str) -> bool:
    """Test to see if the API key is valid."""
    pli = hass.data[DOMAIN].get(DATA_INTEGRATION, {})
    if key == DEFAULT_API_KEY_NOT_SET:
        return True
    latitude, longitude = get_home_coordinates(hass)
    if latitude is None:
        return False
    resp = await async_get_radar_reverse_geocoding(hass, key, latitude, longitude)
    if resp.get("ok"):
        # Save home_state and home_country_code for later comparisons
        radar_decoded = resp["data"]
        if "addresses" in radar_decoded and len(radar_decoded["addresses"]) >= 1:
            address_entry = radar_decoded["addresses"][0]
            if "countryCode" in address_entry and "state" in address_entry:
                home_country_code = address_entry["countryCode"].upper()
                home_state = address_entry["state"]
                _LOGGER.debug(
                    "[async_test_radar_api_key] RADAR home_state = %s, home_country_code = %s",
                    home_state,
                    home_country_code,
                )
                pli.home_country_code = home_country_code
                pli.home_state = home_state
        return True
    else:
        return False
