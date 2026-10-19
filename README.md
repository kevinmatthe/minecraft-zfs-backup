# Minecraft ZFS Backup

A server-side NeoForge 1.21.1 mod coordinates live world checkpoints with a Python host agent. The mod owns backup timing and Minecraft save barriers; the host agent creates and retains local ZFS snapshots. Java 21 and Python 3.11 or newer are required.

Game ticks continue while the mod waits for the snapshot acknowledgement. The save flush itself can cause a short delay. Defaults are one checkpoint per hour and 24 local recovery points. Automatic checkpoints skip intervals without player activity since the last successful checkpoint; manual checkpoints always remain available. Configure optional replication to another pool or host separately, using native TrueNAS replication or another administrator-managed task.

The game container does not need ZFS privileges, a Docker socket, or a network backup endpoint. Both processes communicate through an explicitly shared directory using expiring request IDs and atomic JSON files.

The world must already have its own mounted ZFS dataset. The mod never creates datasets or requires a remote backup destination. A whole Minecraft service directory, including BlueMap and configuration, can have a separate parent dataset and an independent snapshot policy; this agent coordinates the dedicated world dataset only.

See [mod build and configuration](mod/README.md), [agent configuration](agent/README.md), [service installation](deploy/README.md), and [the protocol](docs/protocol.md). End-to-end validation is in progress; see [the implementation plan](docs/plans/2026-10-19-mod-and-agent.md).
