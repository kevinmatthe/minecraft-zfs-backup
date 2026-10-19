# Docker deployment

Install the Mod JAR into the Minecraft server's `mods` directory. The companion Docker container runs only the ZFS snapshot agent. The mod schedules checkpoints; the container has no scheduler for creating world snapshots and performs no replication.

Requirements: Linux Docker Compose, a host running OpenZFS 2.3, and an existing dedicated world dataset mounted before the container starts. The host supplies its ZFS kernel module; the image includes userspace tools, Python and findmnt. No host Python installation, cron job, systemd unit or boot hook is installed.

1. Build/download the Mod JAR following [mod instructions](../mod/README.md) and install it into `mods`. Its default mailbox is `.zfs-snapshot-bridge` below the server directory.
2. Copy `agent.example.json` to a persistent root-owned file, mode `0600`. Set the source dataset, exact host world mount, matching mailbox and private state directory. No destination dataset is required.
3. Pre-create the mailbox owned by the Minecraft UID/GID, mode `0700`; pre-create the private state directory owned by root, mode `0700`. The example binds deliberately refuse to create missing host paths.
4. Copy `.env.example` to `.env` and set its four paths to match the JSON configuration. Then run from the repository root:

```sh
docker compose --env-file .env -f deploy/compose.example.yaml up -d --build
docker compose --env-file .env -f deploy/compose.example.yaml ps
docker compose --env-file .env -f deploy/compose.example.yaml logs --tail 50 agent
```

For an existing Minecraft Compose project, copy the `agent` service into that project and adjust its build context. Only this companion container receives `/dev/zfs` and `SYS_ADMIN`/`DAC_OVERRIDE`. `DAC_OVERRIDE` permits root to access the game-owned private mailbox with all other capabilities dropped. The game container receives no additional privileges. Neither container needs a Docker socket for this backup protocol. The agent container has no network, a read-only root filesystem, and a small temporary filesystem. Docker's `unless-stopped` policy handles restarts.

Bind the world read-only and retain its exact absolute host path inside the agent container. This lets its mount preflight verify the configured dataset rather than an ordinary directory or the wrong filesystem. Bind the mailbox and private state read-write. Configuration is read-only. The daemon restores its durable ledger after restart; stale readiness prevents the mod from disabling saving.

Check the container is healthy, then use `/zfsbackup now` and `/zfsbackup status` in the server console. The local-only setup is complete once a matching published snapshot exists. Configure optional Storage copies using [TrueNAS replication](replication.md), independently of the Mod and agent.

The supplied image targets OpenZFS 2.3 userspace. A test container using userspace 2.3.9 successfully operated against a 2.3.0 host kernel module. Validate other host versions against a disposable dataset before adopting them.
