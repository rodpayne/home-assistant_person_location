"""helpers/template.py - Helpers for template validation"""

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# ----------------- normalize_template -----------------


def normalize_template(s: str) -> str:
    import re

    if not isinstance(s, str):
        return s
    # Replace literal backslash-n first (one or more in a row)
    s = re.sub(r"(\\n)+", " ", s)
    # Replace real newlines of any flavor
    s = re.sub(r"[\r\n]+", " ", s)
    # Collapse runs of whitespace
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


# ----------------- validate_template -----------------


async def validate_template(
    hass: HomeAssistant,
    template_str: str,
    template_variables: dict,
    *,
    expected: str = "text",  # "text" or "url"
    variables: dict[str, Any] | None = None,
    check_entities: bool = True,
    strict: bool = True,
) -> dict[str, Any]:
    """Validate a Jinja template in HA.

    Returns:
        {
        "ok": bool,
        "error": Optional[str],
        "rendered": Optional[str],
        "entities": set[str],
        "domains": set[str],
        "all_states": bool,
        "missing_entities": list[str]
        }
    """
    import inspect
    from urllib.parse import urlparse

    from homeassistant.exceptions import TemplateError
    from homeassistant.helpers.template import Template

    result: dict[str, Any] = {
        "ok": False,
        "error": None,
        "rendered": None,
        "entities": set(),
        "domains": set(),
        "all_states": False,
        "missing_entities": [],
    }

    # Note: always set `error` if returning `ok` False

    tpl_text = normalize_template(template_str)
    tpl = Template(
        tpl_text, hass
    )  # HA's sandboxed Template class [1](https://deepwiki.com/home-assistant/core/2.3-event-system-and-templating)
    try:
        # Call once; if it's awaitable, await it; otherwise use it directly. For legacy Core versions.
        maybe = tpl.async_render_to_info(
            variables=template_variables or {}, strict=strict
        )
        info = await maybe if inspect.isawaitable(maybe) else maybe

        # If the engine captured an exception, treat as failure
        exc = getattr(info, "exception", None)
        if exc:
            result["error"] = f"{exc.__class__.__name__}: {exc}"
            return result

        # Result can be a method or an attribute depending on HA version
        rendered_attr = getattr(info, "result", None)
        if callable(rendered_attr):
            rendered = rendered_attr()  # result() method
        elif rendered_attr is not None:
            rendered = rendered_attr  # result attribute
        else:
            rendered = getattr(info, "_result", None)  # legacy fallback
        if isinstance(rendered, str):
            rendered = rendered.strip()

        result.update(
            rendered=rendered,
            entities=set(getattr(info, "entities", set())),
            domains=set(getattr(info, "domains", set())),
            all_states=bool(getattr(info, "all_states", False)),
        )

        # Optional type checks
        if expected == "url":
            if not isinstance(rendered, str) or not rendered:
                raise ValueError("Rendered value is empty or not a string")
            u = urlparse(rendered)
            if u.scheme not in ("http", "https") or not u.netloc:
                raise ValueError(f"Rendered value is not a valid URL: {rendered!r}")

        # Optional entity existence check
        if check_entities and not result["all_states"]:
            missing = [e for e in result["entities"] if hass.states.get(e) is None]
            result["missing_entities"] = missing

        result["ok"] = True
        _LOGGER.debug("[validate_template] result=%s", result)
        return result

    except TemplateError as te:
        # Jinja/HA template errors (syntax, undefined vars) bubble up as TemplateError
        first_line = str(te).splitlines()[0]
        result["error"] = f"{first_line}"
        _LOGGER.debug("[validate_template] TemplateError result=%s", result)
        return result
    except Exception as ex:
        result["error"] = f"{type(ex).__name__}: {ex}"
        _LOGGER.debug("[validate_template] Exception result=%s", result)
        return result


# ----------------- Friendly Name Template Test -----------------


async def test_friendly_name_template(hass: HomeAssistant, template_str: str) -> dict:
    """Render a preview of friendly_name for the supplied template_str"""
    from homeassistant.core import State
    from homeassistant.helpers.template import Template as HATemplate

    _LOGGER.debug("HATemplate type = %s", type(HATemplate))

    if not isinstance(template_str, str) or not template_str.strip():
        return None

    # This rendering is using the following example states.
    # TODO: we could use actual live state after triggers are configured
    #       (if we could decide which one to show).

    friendly_name_location = "is in Spanish Fork"

    target_state = State(
        "sensor.rod_location",  # entity_id
        "Just Left",  # state
        {
            "source_type": "gps",
            "latitude": 40.12703438635704,
            "longitude": -111.63607706862837,
            "gps_accuracy": 6,
            "altitude": 1434,
            "vertical_accuracy": 30,
            "entity_picture": "/local/rod-phone.png",
            "source": "device_tracker.rod_iphone_16",
            "reported_state": "Away",
            "person_name": "Rod",
            "location_time": "2025-10-26 18:31:21.062200",
            "icon": "mdi:help-circle",
            "zone": "away",
            "bread_crumbs": "Home> Spanish Fork",
            "compass_bearing": 246.6,
            "direction": "away from home",
            "version": "person_location 2025.10.25",
            "attribution": '"Powered by Radar"; "Data © OpenStreetMap contributors, ODbL 1.0. http://osm.org/copyright"; "powered by Google"; "Data by Waze App. https://waze.com"; ',
            "meters_from_home": 2641.1,
            "miles_from_home": 1.6,
            "Radar": "1386 N Canyon Creek Pkwy, Spanish Fork, UT 84660 US",
            "Open_Street_Map": "North Marketplace Drive Spanish Fork Utah County Utah 84660 United States of America",
            "Google_Maps": "1386 N Cyn Crk Pkwy, Spanish Fork, UT 84660, USA",
            "locality": "Spanish Fork",
            "driving_miles": "2.52",
            "driving_minutes": "5.2",
            "friendly_name": "Rod (Rod-iPhone-16) is in Spanish Fork",
            "speed": 1.0,
        },
    )

    sourceObject = State(
        "device_tracker.rod_iphone_16",
        "not_home",
        {
            "source_type": "gps",
            "battery_level": 85,
            "latitude": 40.12703438635704,
            "longitude": -111.63607706862837,
            "gps_accuracy": 6,
            "altitude": 1433.783642578125,
            "vertical_accuracy": 30,
            "friendly_name": "Rod-iPhone-16",
            "person_name": "rod",
            "entity_picture": "/local/rod-phone.png",
        },
    )

    friendly_name_variables = {
        "friendly_name_location": friendly_name_location,
        "person_name": target_state.attributes["person_name"],
        "source": sourceObject,
        "target": target_state,
    }
    _LOGGER.debug(f"friendly_name_variables = {friendly_name_variables}")

    result = await validate_template(
        hass,
        template_str,
        friendly_name_variables,
        expected="text",
    )

    return result
