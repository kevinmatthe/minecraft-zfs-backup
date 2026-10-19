# Docker deployment

Install the Mod JAR into the Minecraft server's `mods` directory. Run the companion as a separate Docker Compose service. The Mod sends HTTP events and schedules checkpoints; the companion executes local ZFS operations. Native TrueNAS replication handles optional copies to Storage independently.

Requirements: Linux Docker Compose, OpenZFS 2.3, an existing dedicated world dataset and Java21/NeoForge1.21.1. The image provides ZFS userspace tools; the host provides the kernel module. No cron job, systemd unit or host boot hook is installed.

1. Build the Mod following [its instructions](../mod/README.md) and install the JAR. Configure `agent_url` to the companion's private Docker hostname and port8765.
2. Generate a random bearer token of at least32 characters. Store identical copies in a private companion token file and the Mod's configured token file. Keep both out of source control; the Minecraft user must be able to read its copy.
3. Copy `agent.example.json` to a root-owned file, mode0600. Configure the dataset, exact host world path, private state directory and token file. Pre-create the state directory with mode0700.
4. Configure the bind paths in `.env` using `.env.example`, then start the companion:

```sh
docker compose --env-file .env -f deploy/compose.example.yaml up -d --build
docker compose --env-file .env -f deploy/compose.example.yaml ps
docker compose --env-file .env -f deploy/compose.example.yaml logs --tail 50 agent
```

Attach the Minecraft/Crafty service and companion to the same private Docker network. An existing game service may keep its other networks for normal connectivity. Publish no companion port to the host. Remove the old shared mailbox bind and `bridge_directory` Mod setting when migrating from1.x. Use a new private agent state directory and preserve the old ledger until the new deployment is verified.

Only the companion receives `/dev/zfs` and the capabilities listed in the example. The game receives no additional privileges. Neither service needs a Docker socket or host PID namespace. The companion has a read-only root filesystem and temporary filesystem; Docker handles restarts. Configuration and secret files are read-only binds.

Preserve the world's exact absolute host path inside the companion: its preflight verifies that this path is the configured mounted ZFS dataset. A writable world bind is required for rollback; a snapshot-only deployment can use a read-only bind. The agent does not create or migrate your datasets.

Once healthy, use `/zfsbackup save`, `/zfsbackup status` and `/zfsbackup list`. Automatic checkpoints default to hourly and skip periods without player activity. A separate server-stopped event can create a final checkpoint after the world closes. See [the HTTP contract](../docs/http-api.md) for timing and failure semantics.

## Optional Crafty rollback

Configure the optional `crafty` object with the Crafty API URL, one fixed server ID and a separate Crafty token file. Use HTTPS with certificate verification; configure a trusted CA file for a private CA. The hostname must match the certificate. For an existing self-signed certificate, add a private Docker network alias matching an explicit DNS name in its SAN and trust that certificate. Confirm the actual certificate names rather than relying on wildcard matching. The private example network does not expose the companion API externally.

Use a dedicated non-superuser Crafty account with a role assigned only to this server. Both its role and API key need COMMANDS and CONFIG permissions (`10000010` in Crafty4.11.0); the key needs no Crafty management permissions or full access. This permits stop/start and temporary restart-policy changes without sharing an administrator credential. The Mod bearer token and Crafty token are separate secrets.

`/zfsbackup rollback <snapshot>` starts a durable background job. The companion disables automatic restart, stops through Crafty, confirms that the world is unlocked, preserves a pre-rollback recovery point, restores the selected snapshot into the existing world mount, then starts through Crafty. It never switches a live mount or destroys later snapshots. `/zfsbackup rollback status` reports progress. Failure keeps the server stopped with automatic restart disabled and preserves recovery points; inspect the reported error before retrying.

Validate rollback against a disposable server and dataset before enabling it for a live world. Recovery copies require OpenZFS block cloning; unsupported file-copy operations fail rather than silently filling the pool with full copies. Snapshot retention is local and respects holds; replication remains a [native TrueNAS task](replication.md).

The image targets OpenZFS2.3 userspace. The previous snapshot implementation was validated with userspace2.3.9 against host module2.3.0; see [verification results](../docs/verification.md) for tested revisions and limits.
