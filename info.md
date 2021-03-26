# Home Assistant Person Location Custom Integration

## Combine the status of multiple device trackers
This custom integration will look at all device trackers for a particular person and combine them into a single person location sensor, `sensor.<name>_location`. Device tracker state change events are monitored rather than being polled, making a composite, averaging the states, or calculating a probability.

## Make presence detection not so binary
When a person is detected as moving between `Home` and `Away`, instead of going straight to `Home` or `Away`, this will temporarily set the person's location state to `Just Arrived` or `Just Left` so that automations can be triggered appropriately.

## Reverse geocode the location and make distance calculations
When the person location sensor changes it can be reverse geocoded using Open Street Map, Google Maps, or MapQuest and the distance from home (miles and minutes) calculated with `WazeRouteCalculator`.

### [Open repository README](https://github.com/rodpayne/home-assistant_person_location#home-assistant-person-location-custom-integration) for all available installation and configuration details.
