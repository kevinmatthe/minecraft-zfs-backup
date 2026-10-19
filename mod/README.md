# NeoForge HTTP save checkpoint bridge

Version 2.1.0 is a dedicated-server-only Java 21 mod for Minecraft 1.21.1 / NeoForge 21.1.251. It actively calls the companion's [HTTP API v1](../docs/http-api.md). Shared mailbox IPC from 1.x is removed. The mod never executes ZFS, Docker, shell commands, replication, or dataset selection.

## Build and test

With a Java 21 JDK:

```sh
cd mod
./gradlew build
```

The standard build pins ModDevGradle 2.1.048, NeoForge 21.1.251 and Gradle 8.14.3. The wrapper distribution has a pinned SHA-256. Gradle produces `build/libs/local-zfs-backup-1.21.1-2.1.0.jar`.

The portable builder compiles and tests against existing matching server libraries, without downloading Minecraft dependencies:

```sh
python3 build.py --libraries-dir /path/to/server/libraries --java /path/to/java21/bin/java
```

Compilation can also run in an existing container. It reads server libraries and uses a unique temporary directory under `/tmp`; it does not install anything or restart services:

```sh
python3 build.py --container minecraft-test --server-dir /path/to/server --java /path/to/java21/bin/java
```

Python 3.11+ is required. The builder invokes the JDK compiler module, puts mapped `*-srg.jar` files before remaining dependencies, runs executable Java tests, and produces `build/local-zfs-backup-1.21.1-2.1.0.jar`. Tests are excluded from the artifact. The test suite covers lifecycle failure/timeouts/stale callbacks, activity generations, dimension saving flags, command rendering, and a real loopback HTTP server with malformed responses, authorization errors, redirects and stalled response bodies. Gradle `check` runs the same tests.

## Configuration

Copy `local-zfs-backup.properties.example` to the server's `config/local-zfs-backup.properties`. Restart to apply changes. Defaults:

| Property | Default | Meaning |
| --- | --- | --- |
| `agent_url` | `http://minecraft-zfs-agent:8765` | Private HTTP(S) origin, without credentials, path, query or fragment |
| `agent_token_file` | `config/local-zfs-backup.token` | Regular file containing the bearer token; relative to server root or an administrator-specified absolute path |
| `interval_seconds` | ignored | Legacy setting; automatic checkpoints follow successful vanilla autosaves |
| `freeze_timeout_seconds` | `15` | Total prepare/save/snapshot budget; 1..15 |
| `only_if_players_been_online` | `false` | Skip automatic checkpoints without player activity since the last successful checkpoint |
| `snapshot_on_shutdown` | `true` | Request an offline snapshot after verified successful shutdown saving/closure, respecting activity |

Booleans accept exactly `true` or `false`. The token must contain at least 32 printable, non-space ASCII characters, with an optional trailing newline. Keep its file private and readable by the server UID. Tokens and authorization headers never enter logs or command output. Missing credentials or invalid configuration disables the bridge with a generic diagnostic. TLS uses the JVM's normal trust configuration. Redirects are disabled.

There is no heartbeat polling or file IPC. Agent readiness is checked by preparing each checkpoint. Changing world/dataset/Crafty-server selection is a host administrator operation; no chat command can change it.

## Operator commands

All commands require operator permission level 4:

| Command | Behavior |
| --- | --- |
| `/zfsbackup save` | Force a save checkpoint, bypassing the idle filter |
| `/zfsbackup now` | Alias for `save` |
| `/zfsbackup status` | Local checkpoint/activity status and active HTTP agent status |
| `/zfsbackup list` | Managed published snapshots, event metadata and GUIDs |
| `/zfsbackup rollback <snapshot>` | Submit a one-click offline rollback through the configured agent/Crafty integration |
| `/zfsbackup rollback status` | Current or most recent rollback job |

Running-server commands return immediately and deliver completed responses on the server thread. A rollback response is an accepted background job, not completion: the host agent handles stopping the server, verifying offline state and the world lock, recording recovery points, restoring the existing world dataset, then restarting through Crafty. Snapshot names complete with Tab after `/zfsbackup rollback `, including typed prefixes. Completion fetches only managed published snapshot names asynchronously from the agent; concurrent requests share a lookup and results are cached for 500 milliseconds. It performs no background polling or world operations. A disabled/unavailable agent or malformed response returns no snapshot suggestions, while the `status` subcommand remains available. Job progress remains available after restart. The mod also queries the latest job on each startup.

Manual checkpoints cannot overlap another checkpoint or bypass the minimum 60-second failure/defer backoff. The host separately enforces its acceptance policy. Automatic checkpoints follow whole successful vanilla autosave batches, normally about every five minutes at20TPS. There is no second timer or repeated save. If the optional idle filter is enabled, an idle batch is skipped and the next actual autosave is the next opportunity. Login and ticks with online players advance an activity generation. Only matching `COMMITTED` clears the generation captured at checkpoint start; failed operations and activity during an in-flight checkpoint remain pending, including after the last player leaves. Activity tracking starts fresh when the server process starts.

## Save and HTTP lifecycle

For `periodic_save`, the Mod observes the complete native `tickServer` autosave invocation, including its existing asynchronous-save integration. After a successful batch it captures saving flags and temporarily postpones new background save submissions while asynchronously preparing the HTTP operation. It does not call `saveEverything` a second time. For an explicit `manual_save`, preparation still precedes capturing flags and the single `saveEverything(false, true, true)` call.

If installed, `local.asyncplayersave.AsyncPlayerSave.backupCheckpoint(MinecraftServer)` is invoked by reflection. Its completion schedules onto the server thread, followed by nonblocking synchronization of the native chunk, POI and entity region workers across all dimensions. The running game thread never calls `join` or `get`. Background dirty/section-unload save submissions remain queued during capture and resume after flags are restored; gameplay continues. An observed save failure rejects the checkpoint even if its failed IO future already left the native pending queue. An external forced save cancels the frozen checkpoint before proceeding. A successful barrier sends the snapshot request. A matching `SNAPSHOT_READY` restores all captured saving flags before sending `commit`. Only matching `COMMITTED`, including the same snapshot identity, records success. Commit acknowledgement has a separate bounded wait while saving is enabled.

The entire prepare/save/snapshot budget uses epoch deadlines and a monotonic elapsed-time guard. Preparation errors, HTTP failures, invalid responses, timeouts and shutdown attempt cancellation and restore all captured flags. A late response or barrier cannot revive a cancelled or newer request. Forced Minecraft flushing and synchronous hooks in other mods can still delay a tick; they cannot safely be interrupted. The deadline is checked immediately when control resumes.

HTTP uses Java 21 `HttpClient.sendAsync`, four bounded daemon workers, at most eight requests in flight, and an independent deadline timer. Both response headers and bodies are bounded in time; responses are capped at 16 KiB, UTF-8/JSON is strict, duplicate keys are rejected, and protocol scalar types/UUIDs are validated. This includes slow bodies after headers, which Java's request timeout alone does not reliably cover.

`ServerStartedEvent` sends `server_started` with `backup_requested=false`. `ServerStoppingEvent` first cancels/restores any in-flight checkpoint. At `ServerStoppedEvent`, the mod always sends `server_stopped`. It requests a shutdown recovery point only when `snapshot_on_shutdown`, activity policy and positive clean-shutdown evidence all permit. Missing or failed evidence sends `backup_requested=false` and logs a generic warning. The host always records the event and skips automatic snapshot creation during rollback maintenance.

The stopped listener runs at `LOWEST` priority, after normal-priority companion save drains. Only this fully stopped hook may wait for HTTP: the request has a 15-second deadline, and the outer wait is capped at 20 seconds. The HTTP client is then shut down without waiting for running network operations.

The ordering was checked against the actual NeoForge 21.1.251 patched server JAR: `MinecraftServer.runServer` calls `stopServer`, whose normal path flushes/closes `ServerLevel` instances and closes `LevelStorageAccess`, before `ServerLifecycleHooks.handleServerStopped` posts `ServerStoppedEvent`. NeoForge also posts the event on exceptional shutdown paths, and vanilla catches/logs individual world-close and storage-close `IOException`s. Neither `isStopped`, `isCurrentlySaving` nor an unlocked `session.lock` proves successful saving and closure.

A narrow optional Mixin observes the actual pinned `stopServer` call sequence. It requires a successful `saveAllChunks` result, successful closure of every originally loaded dimension, successful storage closure and a normal method return, with no observed failure. Its wrappers preserve original arguments, return values and exception identities; vanilla retains its own exception handling. Missing injection targets, an absent Mixin or an incomplete sequence leave evidence false rather than authorizing a backup. The Mixin has no required injections and does not participate in ordinary HTTP checkpoints. It uses NeoForge's existing Mixin support without another dependency. Tests cover caught close exceptions, thrown/false saves and missing instrumentation/normal returns. This observes Minecraft's public save/close outcomes; an unrelated mod that silently hides its own write errors cannot provide positive durability evidence to this bridge.

Validate the complete modpack on a disposable server before deployment; forced process termination cannot deliver an exit event.

The checkpoint represents completed writes submitted by that autosave batch. It is not a transaction over all game memory or arbitrary third-party direct file writes. Independent logout saves keep their normal semantics. Snapshot completion and rollback use only the configured local dataset; remote retention and restore catalogs belong to separately configured storage tooling.
