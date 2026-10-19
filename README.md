# Minecraft ZFS Backup

A server-side NeoForge 1.21.1 mod coordinates live world checkpoints with a Python companion container. The mod observes Minecraft autosaves and coordinates save barriers; the container creates and retains local ZFS snapshots. Java 21 and Python 3.11 or newer are required.

Game ticks continue while the mod waits for queued writes and the snapshot acknowledgement. Each successful vanilla autosave (normally every five minutes at20TPS) creates an independent `mc-live-*` checkpoint with a one-hour local expiry. The first successful autosave checkpoint in each UTC capture hour also creates a separate `mc-hourly-*` snapshot with a 24-hour local expiry, using the same save barrier. Normal cadence yields about12 live and24 hourly points. Saves without players are included, because forced-loaded chunks can still change. Manual, clean-shutdown and rollback-recovery checkpoints use the hourly family and its 24-hour expiry; they can add points beyond the normal cadence. Configure optional replication to another pool or host separately; a [native TrueNAS replication task](deploy/replication.md) is one deployment example, not a dependency of the Mod or companion.

The companion persists absolute expiry in ZFS user properties and independently removes expired local snapshots, including while Minecraft is stopped. No TrueNAS service is needed for local retention. Optional external replication selects hourly snapshots only; its destination retention is separate.

The Mod actively calls the companion HTTP API on a private Docker network. Events distinguish periodic saves, manual saves and server shutdown. Requests use bearer authentication, expiring operation IDs and a prepare/snapshot/commit handshake. The game container receives no ZFS privileges or Docker socket.

Operators can request an immediate checkpoint, list recovery points and initiate a rollback. Optional Crafty integration stops the server, preserves a recovery point, restores the selected world and starts the server again. Failed recovery keeps the server stopped and retains recovery evidence. A separate durable rollback audit records the requester, selected snapshot/GUID, phase intents, recovery points, failures and resumption outside the restored world.

The world must already have its own mounted ZFS dataset. The mod never creates datasets or requires a remote backup destination. A whole Minecraft service directory, including BlueMap and configuration, can have a separate parent dataset and an independent snapshot policy; this agent coordinates the dedicated world dataset only.

See [mod build and configuration](mod/README.md), [agent configuration](agent/README.md), [Docker deployment](deploy/README.md), [the protocol](docs/protocol.md), and [validation results and limits](docs/verification.md).

The Docker deployment targets Linux/OpenZFS. Pool names, datasets and host paths are administrator configuration. Only local published points appear in command completion and in-game rollback; remote backup catalogs are outside the product. Crafty is optional server-control integration for one-click rollback, not a requirement for snapshot creation.
