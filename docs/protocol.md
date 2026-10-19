# Shared directory protocol v1

This document is the interface contract between `mod/` and `agent/`. Do not change field names or semantics without updating both sides and tests.

## Location and atomicity

The mod chooses a mailbox directory relative to its server root, default `.zfs-snapshot-bridge`. The host administrator configures the corresponding absolute host path. The host obtains source/target dataset names, world mount path and state directory only from its own configuration. Message fields never select a dataset or command.

Files: `heartbeat.json` (host writes), `request.json` (mod writes), `response.json` (host writes). Writers publish UTF-8 JSON through a unique temporary file and atomic rename in the same directory. All JSON numbers below are integer Unix epoch milliseconds, except protocol version. UUIDs are canonical lowercase UUID strings.

Host heartbeat, refreshed at least every second:

```json
{"protocol":1,"updated_ms":1792380000000,"ready":true}
```

`ready` means this exact source dataset is mounted at the configured world path and the coordinator can accept snapshot requests. Replication may continue independently. Mod must not disable saving unless heartbeat is ready and no older than 5 seconds (allow at most 1 second future clock skew).

## Lifecycle

1. Every configured interval (default 3600 seconds), on the Minecraft server thread, the mod checks fresh host readiness and optional FTB-is-running reflection, preserves each dimension's `noSave` flag, disables automatic world saving, and executes a forced flush. Optional installed `local.asyncplayersave.AsyncPlayerSave.backupCheckpoint(server)` is awaited asynchronously through a completion callback scheduled onto the server thread, never `.join()` on that thread.
2. Once all save barriers succeed, mod publishes a FROZEN request with a new UUID. Default host acknowledgement timeout is 15 seconds. No interval exceeds a configured maximum freeze bound; failure restores saving and retries after at least 60 seconds.

```json
{"protocol":1,"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","phase":"FROZEN","created_ms":1792380000000,"deadline_ms":1792380015000}
```

3. Host validates exact protocol, UUID, phase and timestamp bounds. It records a root-owned pending ledger BEFORE creating a pending snapshot. It rechecks current matching FROZEN request and deadline after snapshot creation. It sends SNAPSHOT_READY only when the snapshot completed before expiry and the request is still current. Other message IDs cannot cancel, commit or publish that pending snapshot.

```json
{"protocol":1,"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","phase":"SNAPSHOT_READY","updated_ms":1792380000250,"snapshot":"mc-hourly-20261019T060000Z"}
```

4. Mod accepts only matching SNAPSHOT_READY before its deadline. On the server thread it restores the previously captured saving flags BEFORE publishing COMMIT. It does not wait with saving disabled for replication.

```json
{"protocol":1,"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","phase":"COMMIT","created_ms":1792380000000,"deadline_ms":1792380015000,"committed_ms":1792380000300}
```

5. Host publishes the pending snapshot only after matching COMMIT. `committed_ms` must be between created_ms and deadline_ms and cannot be in the future beyond 1 second; host must receive it within 60 seconds after deadline_ms. Pending ledger must match this id, snapshot and timing. Published names `mc-hourly-YYYYMMDDTHHMMSSZ` are UTC, compatible with existing snapshots. Pending names are `pending-` + published name. A repeated committed request is idempotent.

```json
{"protocol":1,"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","phase":"COMMITTED","updated_ms":1792380000400,"snapshot":"mc-hourly-20261019T060000Z"}
```

6. Host enqueues background compressed ZFS full/incremental replication. Source and target retain newest 24 managed published snapshots after successful GUID-verified replication. A failed replication does not block new local snapshots or delete the last common base. Target is read-only and unmounted. Never use `zfs receive -F`, recursive destruction or from-scratch overwrite on live targets.

## Failures and restart

Mod restores saving on any save error, stale/mismatched response, timeout, failure to publish COMMIT, or server stopping. It publishes CANCEL when possible; absence of a working host must not leave saving disabled. It can ignore unrelated response IDs while awaiting its own deadline. Timeout covers waiting for the optional asynchronous barrier too; tick continues while waiting.

```json
{"protocol":1,"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","phase":"CANCEL","created_ms":1792380000000,"deadline_ms":1792380015000,"cancelled_ms":1792380015000}
```

Host removes owned unpublished pending snapshots after cancellation, expiry, or a newer request superseding the old one. COMMIT is never accepted without a matching recorded pending snapshot. On host restart it reconciles the persisted pending ledger and current request; it can complete a valid COMMIT, otherwise expire/drop only its own pending snapshot. It can resume replication from source/target snapshot metadata without game console access.

ERROR responses have matching id, protocol, phase ERROR, updated_ms and an error string. Mod restores saving on a matching error. Malformed/oversized files fail closed; maximum message size 16 KiB. A host policy enforces at least 60 seconds between accepted snapshots. For isolated tests this rate limit may be configured lower.

After a successful acknowledgement, mod schedules next interval. Expose operator-only `/zfsbackup now` and `/zfsbackup status`. Scheduling uses elapsed wall time, not a fixed tick count. A manual trigger cannot overlap an existing checkpoint.
