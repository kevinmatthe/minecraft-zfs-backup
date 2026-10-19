# Minecraft ZFS Backup

A server-side NeoForge 1.21.1 mod coordinates live world checkpoints with a Python companion container. The mod owns backup timing and Minecraft save barriers; the container creates and retains local ZFS snapshots. Java 21 and Python 3.11 or newer are required.

Game ticks continue while the mod waits for the snapshot acknowledgement. The save flush itself can cause a short delay. Defaults are one checkpoint per hour and 24 local recovery points. Automatic checkpoints skip intervals without player activity since the last successful checkpoint; manual checkpoints always remain available. Configure optional replication to another pool or host separately, using a [native TrueNAS replication task](deploy/replication.md).

The Mod actively calls the companion HTTP API on a private Docker network. Events distinguish periodic saves, manual saves and server shutdown. Requests use bearer authentication, expiring operation IDs and a prepare/snapshot/commit handshake. The game container receives no ZFS privileges or Docker socket.

Operators can request an immediate checkpoint, list recovery points and initiate a rollback. Optional Crafty integration stops the server, preserves a recovery point, restores the selected world and starts the server again. Failed recovery keeps the server stopped and retains recovery evidence.

The world must already have its own mounted ZFS dataset. The mod never creates datasets or requires a remote backup destination. A whole Minecraft service directory, including BlueMap and configuration, can have a separate parent dataset and an independent snapshot policy; this agent coordinates the dedicated world dataset only.

See [mod build and configuration](mod/README.md), [agent configuration](agent/README.md), [Docker deployment](deploy/README.md), [the protocol](docs/protocol.md), and [validation results and limits](docs/verification.md).
