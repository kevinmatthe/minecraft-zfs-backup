# Minecraft ZFS Backup

A server-side NeoForge 1.21.1 mod coordinates live world checkpoints with a host-side ZFS agent. The mod owns backup timing and Minecraft save barriers; the host agent creates snapshots and replicates changed blocks to another pool.

Game ticks continue while the mod waits for the snapshot acknowledgement. The save flush itself can cause a short delay. Replication runs after automatic saving is restored.

The game container does not need ZFS privileges, a Docker socket, or a network backup endpoint. Both processes communicate through an explicitly shared directory using expiring request IDs and atomic JSON files.

Implementation and verification are in progress. See [the protocol](docs/protocol.md) and [the implementation plan](docs/plans/2026-10-19-mod-and-agent.md).
