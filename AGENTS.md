# Minecraft ZFS Backup

This independent repository contains a server-side NeoForge 1.21.1 mod and a Docker-packaged Python 3.11+ ZFS agent. Deployment uses Docker Compose; do not introduce cron jobs, systemd units, or host boot hooks. Storage replication belongs to native TrueNAS tasks.
Read `docs/http-api.md` before modifying either side of the HTTP event protocol.

- Production worlds and infrastructure are outside this repository. Tests must use temporary directories, disposable ZFS datasets, or a separate validation Minecraft server. Never change production from a unit test or build script.
- Keep Java game lifecycle logic on the server thread. Never block the game thread waiting for host responses or replication. Restore every dimension's prior saving flag on success, error, timeout and shutdown.
- The mod must not execute host commands, access a Docker socket, or accept arbitrary datasets/paths from chat commands. Host paths/datasets come from administrator-owned configuration only.
- Only publish snapshots after the matching mod commit. Expired/cancelled requests cannot publish snapshots. The mod and agent do not execute replication or receive commands. Local retention prunes only owned published snapshots and preserves external holds. Replication belongs to separately configured tasks.
- Write meaningful failing tests before implementing lifecycle/protocol changes. Test timeout, cancellation, stale messages, process restart, and held snapshot retention.
- Build target: Java 21, Minecraft 1.21.1, NeoForge 21.1.251. Do not upgrade the live server's dependencies while building this project.
- Stage only files owned by your task; multiple agents may work in disjoint subdirectories. Do not commit another agent's pending changes.
