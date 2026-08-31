# Async concurrency and locking

This document describes the runtime concurrency model used by the Person Location integration.

## Goals

The integration receives location updates from multiple Home Assistant entities and may perform several external API requests for each accepted update. The async design therefore needs to:

- keep Home Assistant's event loop free of blocking work;
- prevent concurrent updates for the **same target** from corrupting state;
- allow different targets to make progress independently;
- throttle external API traffic without using a global lock to serialize network I/O; and
- keep synchronization objects owned by the integration runtime rather than at module scope.

## Runtime-owned synchronization

`PersonLocationIntegration` owns its synchronization primitives:

- `_integration_lock` protects integration-wide bookkeeping used to reserve the next external API slot;
- `_target_locks` contains one `asyncio.Lock` per target entity.

Locks are created when the integration runtime is created. They are not module-level singletons, which avoids sharing event-loop-bound synchronization across independent Home Assistant runtimes or test fixtures.

## Per-target serialization

A target update uses its target-specific lock when changing target state and related runtime information. For example:

```text
Person A update ──> target-A lock ──> target-A state
Person B update ──> target-B lock ──> target-B state
```

Person A therefore does not block Person B merely because both are being processed by the integration.

The lock is deliberately per entity rather than global. A target can still have multiple updates queued behind it; this preserves ordering and consistency for that person's state.

## API throttling

External API calls are rate-limited separately from target synchronization.

The integration-wide lock is held only long enough to reserve the next API slot and update the associated counters/timestamp. If a request needs to wait, the lock is released before `asyncio.sleep()`:

```text
acquire integration lock
        │
        ├─ reserve API slot
        ├─ update counters
        │
release integration lock
        │
        ├─ sleep if required
        │
        └─ perform API request
```

This prevents a waiting request from blocking unrelated integration bookkeeping.

## Home Assistant event-loop operations

Home Assistant state inspection and event-listener registration are performed on the Home Assistant event loop. Synchronous operations that genuinely need an executor should be isolated to the synchronous portion of the operation; listener registration itself should not be moved to a worker thread.

In particular, the integration does not use `run_in_executor()` to register `async_track_state_change_event()` listeners.

## Important remaining limitation

Per-target locking has removed the **global** target serialization, but some reverse-geocoding workflows still hold the target lock while performing provider I/O. This is intentionally not described as fully concurrent provider processing.

The next concurrency refinement should change the workflow to:

1. acquire the target lock;
2. validate the trigger and capture an immutable request snapshot;
3. increment a target/location generation;
4. release the target lock;
5. perform external I/O;
6. reacquire the target lock;
7. apply the response only if its generation is still current; otherwise discard it as stale.

That design will allow external API requests for the same target to overlap safely while preventing an older response from overwriting newer location data.

## Lifecycle expectations

Any background task or timer created by this integration must have an explicit owner and cleanup path. The startup timer is owned by `PersonLocationIntegration` and is cancelled during config-entry unload. Config-entry unload and entity removal must be able to cancel work that the integration itself created.

## Config-entry reloads and entity registry

A normal options update reloads the config entry. Platform unload removes the active entity objects, but the integration must not manually remove those config-entry entities from Home Assistant's entity registry. Keeping registry entries preserves stable entity IDs and user configuration across reloads and avoids generating misleading historical `unavailable` transitions solely because the integration was reloaded.


## Home Assistant Core 2026.7/2026.8 compatibility

The implementation targets Home Assistant Core 2026.7 and 2026.8. Runtime state owned by a config entry is exposed through `ConfigEntry.runtime_data`; the legacy `hass.data[DOMAIN]` mirror remains only where the current integration architecture still requires it during this incremental migration. Home Assistant's async entity APIs are used directly on the event loop, and HTTP clients should use the HA-managed `aiohttp` session rather than creating per-request sessions.


### Person entity coordinates in Core 2026.7

Home Assistant Core 2026.7 changed `person` entities so they no longer expose home-zone latitude/longitude when the person's location comes from a presence scanner associated with the home zone. This integration therefore treats Home as a special case and resolves the coordinates from the Home Assistant `zone.home` entity instead of relying on `person` latitude/longitude attributes. This preserves the location sensor's Home coordinates on 2026.7 and 2026.8.
