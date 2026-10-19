# NeoForge save checkpoint bridge

Dedicated-server-only Java 21 mod for Minecraft 1.21.1 / NeoForge 21.1.251. It communicates with the host agent using [protocol v1](../docs/protocol.md); it has no ZFS, Docker, shell, or dataset-selection capabilities.

## Build and test

With a Java 21 JDK:

```sh
cd mod
./gradlew build
```

The standard build pins ModDevGradle 2.0.148, NeoForge 21.1.251 and Gradle 8.14.3. Wrapper distribution integrity is checked with SHA-256. Tests are executable pure Java programs, run by Gradle `check` as well as the portable builder. Gradle produces `build/libs/local-zfs-backup-1.21.1-1.0.0.jar`.

To build without downloading Minecraft dependencies, point the portable builder at existing **matching** server libraries:

```sh
python3 build.py --libraries-dir /path/to/server/libraries --java /path/to/java21/bin/java
```

Alternatively, compilation and tests can run in an existing container. Only a unique temporary build directory under `/tmp` is created and removed; server files are read, and the resulting artifact is copied to this checkout. Nothing is installed or restarted:

```sh
python3 build.py --container minecraft-test --server-dir /path/to/server --java /path/to/java21/bin/java
```

Python 3.11+ is required. This build uses `java -m jdk.compiler/com.sun.tools.javac.Main`, putting the mapped `*-srg.jar` before remaining server libraries. It produces `build/local-zfs-backup-1.21.1-1.0.0.jar`, containing production classes and mod metadata, excluding test classes. Both build paths run lifecycle, transport and dimension-saving tests.

## Configuration and commands

Copy `local-zfs-backup.properties.example` to the dedicated server's `config/local-zfs-backup.properties` before startup. Missing configuration uses defaults: interval 3600 seconds, maximum freeze 15 seconds, mailbox `.zfs-snapshot-bridge` relative to server root. Restart to apply configuration. An invalid configuration disables the bridge and logs the reason.

`interval_seconds` accepts 60..31536000; `freeze_timeout_seconds` accepts 1..15. `bridge_directory` must be relative and remain below server root. The host administrator must map the same mailbox directory into the host agent. Configure shared-directory permissions so the server and agent can read each other's files and publish atomic replacements.

`/zfsbackup now` and `/zfsbackup status` require operator permission level 4. A manual request bypasses the ordinary interval, while respecting an existing request and the minimum 60-second failure/defer backoff. The host separately enforces its snapshot acceptance rate. The first automatic request occurs after a full interval; restarting the server starts a new interval.

## Save lifecycle and limits

The scheduler uses elapsed real time (`System.nanoTime`), so low TPS does not stretch the interval, and clock rollback cannot extend a freeze. Epoch milliseconds are used for heartbeat checks and protocol timestamps. A ready host heartbeat must be at most 5 seconds old and at most 1 second in the future before freezing.

On the server thread the mod captures every dimension's `noSave` value, sets all to true, then calls `saveEverything(false, true, true)` for a forced flush. It optionally invokes `local.asyncplayersave.AsyncPlayerSave.backupCheckpoint(MinecraftServer)` by reflection; the returned completion stage schedules its completion onto the server thread. The game thread never waits on that stage using `join` or `get`. The deadline includes forced flush and asynchronous barriers. Pending stages and host messages are checked on server ticks.

A matching `SNAPSHOT_READY` restores the captured flags before publishing `COMMIT`. Only matching `COMMITTED` records a success and schedules the next normal interval. Unrelated responses are ignored until the deadline. Failures, malformed messages, timeout and shutdown restore saving, attempt `CANCEL`, and defer retries by at least 60 seconds. A late asynchronous callback cannot revive a cancelled request. A running optional FTB `BackupHandler.isRunning()` defers the checkpoint; a present but incompatible optional integration fails closed.

Saving flags are changed on the server thread, including partial-freeze failures. Forced Minecraft flushing and installed mods' synchronous save hooks can delay a server tick; no mod can safely interrupt disk I/O or restore flags while the server thread is blocked. Once the thread resumes, the elapsed deadline prevents requesting a snapshot after the freeze budget. Ticks continue while waiting for asynchronous barriers or host acknowledgements. Reflection integration and full startup/world behavior still need validation on a separate disposable Minecraft server before deployment.

Transport reads are capped at 16 KiB and reject malformed UTF-8, lenient JSON syntax, duplicate fields, incorrect scalar types, unsupported protocol versions and noncanonical UUIDs. Publication uses a forced temporary file plus an atomic same-directory rename. The mailbox must reside on a filesystem supporting atomic replacement.
