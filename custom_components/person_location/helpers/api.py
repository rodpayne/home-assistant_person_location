"""API Client Wrapper with retries and exponential backoff."""

import asyncio
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
    DOMAIN,
    PERSON_LOCATION_INTEGRATION,
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
    headers: dict = HEADERS,
    timeout: float = TIMEOUT,
    retries: int = RETRIES,
) -> dict:
    """Wrap call to PERSON_LOCATION_CLIENT.async_get_api_data."""
    client = PERSON_LOCATION_CLIENT(hass)
    return await client.async_get_api_data(method, url, data, headers, timeout, retries)


# ------- Entry points for specific API calls and error checking:


async def async_get_google_maps_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Google Maps Geocoding API."""
    pli: PERSON_LOCATION_INTEGRATION = hass.data[DOMAIN]["integration"]
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
    if not resp["ok"]:
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200 and resp.get("data"):
        # resp["data"]["status"] -> Google API status field (e.g. "OK", "ZERO_RESULTS", "REQUEST_DENIED")
        api_status = resp["data"].get("status")
        if api_status == "OK":
            return resp
        _LOGGER.debug(
            "[async_get_google_maps_geocode] Google API status: %s", api_status
        )
        resp["error"] = f"API status: {api_status}"
    else:
        _LOGGER.debug(
            "[async_get_google_maps_geocode] Google HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data").replace(key, "********"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    return resp


async def async_get_mapbox_static_image(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Mapbox Static Image API."""
    url = f"https://api.mapbox.com/styles/v1/mapbox/streets-v11/static/{longitude},{latitude},5,0/300x200?access_token={key}"
    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data("get", url)
    if not resp["ok"]:
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200:
        return resp
    else:
        _LOGGER.debug(
            "[async_get_mapbox_geocode] Mapbox HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    return resp


async def async_get_mapquest_reverse_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Mapquest Reverse Geocoding API."""
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
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200:
        return resp
    else:
        _LOGGER.debug(
            "[async_get_mapquest_reverse_geocoding] Mapquest HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
    resp["ok"] = False
    return resp


async def async_get_open_street_map_reverse_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Nominatim Reverse Geocoding (OpenStreetMap) API."""
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
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200:
        return resp
    else:
        _LOGGER.debug(
            "[async_get_open_street_map_reverse_geocoding] Open Street Map HTTP status: %s, data: %s",
            resp.get("status"),
            resp.get("data"),
        )
        resp["error"] = f"HTTP status: {resp.get('status')}"
        resp["ok"] = False
    return resp


async def async_get_radar_reverse_geocoding(
    hass: HomeAssistant, key: str, latitude: str, longitude: str
) -> dict:
    """Call the Radar Reverse Geocoding API."""
    url = f"https://api.radar.io/v1/geocode/reverse?coordinates={latitude},{longitude}"
    headers = {"Authorization": key, "Content-Type": "application/json"}
    client = PERSON_LOCATION_CLIENT(hass)
    resp = await client.async_get_api_data("get", url, headers=headers)
    if not resp["ok"]:
        return resp
    # resp["status"] -> HTTP status code (e.g. 200, 404)
    if resp.get("status") == 200 and resp.get("data"):
        # resp["data"]["meta"]["code"] -> Radar API response code (e.g. 200, 400, 401, 403, 404, 429, 500)
        api_code = resp["data"].get("meta", {}).get("code")
        if api_code == 200:
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
                        _LOGGER.debug(
                            "Attempt %s/%s failed due to: %s",
                            attempt,
                            RETRIES,
                            last_error,
                        )

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
                delay = 2 ** (attempt - 1)
                _LOGGER.debug("Retrying in %s seconds...", delay)
                await asyncio.sleep(delay)

        # ------- Return error response:
        _LOGGER.debug("All %s attempts failed for %s", RETRIES, url)
        return {
            "status": None,
            "ok": False,
            "data": None,
            "error": last_error or "Unknown error",
        }
