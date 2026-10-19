# HTTP snapshot and rollback companion

The server-side Mod sends authenticated HTTP requests to this Docker companion. The Mod owns save barriers and scheduling; the companion owns local ZFS snapshots and optional Crafty rollback. Native TrueNAS replication independently copies published `mc-hourly-*` snapshots. There are no shared mailbox files, heartbeat polling, host scheduler jobs, Docker sockets or host PID namespaces.

Build from the repository root:

```sh
docker build -t minecraft-zfs-agent:local .
```

If Docker's build network cannot resolve Debian mirrors, use `--network=host` for the build. The image includes Python 3.13, `findmnt`, and ZFS 2.3 userspace from [Debian trixie contrib](https://packages.debian.org/trixie/zfsutils-linux). It uses the host's existing ZFS kernel module. Version 2.3.9 userspace was validated against TrueNAS kernel module 2.3.0; repeat disposable validation after upgrades.

Copy [deploy/.env.example](../deploy/.env.example), choose the fixed host paths, and prepare a private configuration from [deploy/agent.example.json](../deploy/agent.example.json). For rollback use [deploy/agent.crafty.example.json](../deploy/agent.crafty.example.json). Configuration and secrets must be root-owned mode0600; the secrets and HTTP state directories must be root-owned mode0700. Generate an API token with at least32 characters, and install the same token in the Mod's configured token file with permissions allowing the game UID to read its own copy. Keep the API and Crafty tokens outside the world directory. Use a new HTTP state directory when upgrading from the old mailbox agent, preserving its ledger separately.

Connect the companion and Crafty to the same existing private Docker network. Set `AGENT_NETWORK` to its name. The Compose service has the DNS alias `minecraft-zfs-agent`; the Mod defaults to `http://minecraft-zfs-agent:8765`. No host port is published.

```sh
docker compose --env-file deploy/.env -f deploy/compose.example.yaml up -d --build
docker compose --env-file deploy/.env -f deploy/compose.example.yaml logs --tail=50 agent
```

The world bind must use the exact same absolute host path inside the container, so the companion can verify the actual source dataset and mount. Its `rslave` propagation receives future host snapshot automounts, making `.zfs/snapshot/<name>` visible during restoration without propagating container mounts back to the host. Before enabling rollback, check `findmnt -o TARGET,PROPAGATION --target /your/world`: the host world mount must have shared propagation (or a compatible shared master). A private bind can expose empty snapshot automount stubs even when the host sees snapshot files. It is writable for rollback; set `WORLD_READ_ONLY=true` for snapshot-only operation without Crafty. State is a separate writable private bind, while configuration and the secrets directory are read-only. Missing bind sources are rejected. The root filesystem is read-only, default capabilities are dropped, and only `SYS_ADMIN`, `DAC_OVERRIDE`, `CHOWN`, `FOWNER`, and `FSETID` are added. ZFS ioctls require `SYS_ADMIN`; restoration needs ownership, timestamps, modes and setgid metadata preserved. These capabilities were checked in disposable containers; the service uses no privileged mode. Access to `/dev/zfs` still grants substantial host ZFS authority, so the image and fixed administrator configuration must remain trusted.

## Configuration

| Field | Meaning | Default |
| --- | --- | --- |
| `source_dataset` | Existing dedicated world dataset | required |
| `world_path` | Exact absolute world mount | required |
| `state_dir` | Private durable HTTP operation state, outside world | required |
| `api_token_file` | Private API token file outside world | required |
| `listen_host` | Private container listen interface | `0.0.0.0` |
| `listen_port` | HTTP port | `8765` |
| `live_ttl_seconds` | Expiry after actual creation for each live snapshot | `3600` |
| `hourly_ttl_seconds` | Expiry after actual creation for each hourly snapshot | `86400` |
| `keep` | Deprecated compatibility setting; ignored for retention | `24` |
| `min_interval_seconds` | Minimum acceptance interval per normal or scheduled-hourly request class; recent live does not rate-limit hourly | `60` |
| `crafty` | Optional fixed Crafty integration | absent |

Crafty configuration includes `base_url`, `server_id`, `token_file`, `verify_tls` (defaulttrue), optional `ca_file`, and stop/start timeouts (default180 seconds). Use a dedicated restricted account and key for the selected server. Trust its private CA and use a Docker DNS alias that exactly matches a valid certificate SAN. Rollback requires a writable world bind and verified Crafty server control. Without Crafty, the checkpoint API remains available and rollback endpoints report that integration is disabled.

## API and persistence

See the frozen [HTTP API contract](../docs/http-api.md). Every `/v1/` request requires constant-time Bearer authentication. The unauthenticated `/healthz` reveals only readiness. Requests have a 16KiB body limit, bounded socket reads, and at most eight concurrent workers. Authorization headers and tokens are never logged. The Mod checks readiness by preparing a checkpoint, rather than periodically polling it.

Checkpoints prepare an HTTP operation, snapshot only after the Mod's save/IO barrier, and publish only after the Mod restores flags and commits. An autosave-driven Mod can freeze new save submissions before HTTP preparation to preserve an already completed save batch; manual and independent hourly capture prepare before their forced save. Pending snapshots remain invisible to managed listing and native replication. A durable root-owned intent precedes each snapshot; recorded GUIDs fence publication and cleanup. Commit timing must match the original window, with at most one second future skew and at most60 seconds receipt grace. Deadline timers clean abandoned checkpoints without file polling. Idempotent retries cannot create another published snapshot. Restart reconciles a persisted rename intent and removes strictly named orphan pending points. History is bounded while active/held recovery dependencies remain available.

Lifecycle events preserve startup/shutdown metadata. Shutdown backups additionally acquire a shared POSIX record lock on the actual existing `session.lock` through a read-only descriptor. A running JVM's exclusive lock prevents the snapshot, including when an old shutdown event arrives late. The lock inode is checked before publication. During rollback maintenance, lifecycle requests are recorded and skipped without cancelling or committing another operation.

`GET /v1/status` reports checkpoint, last success/error and current/last rollback. `GET /v1/snapshots` lists both published families with names, GUIDs, known event metadata, `family`, `created_at` and `expires_at` (Unix seconds). Old snapshots can have event `unknown`. Private `ledger.json`, `retention.json` and `rollback.json` remain in the state directory; logs go to Docker stderr. An exclusive process lock prevents two companions sharing the same state.

Rollback runs as a durable background job. It holds the selected point, disables Crafty automatic restart policy, confirms fresh stopped statistics and obtains the real exclusive session lock. It records a held pre-recovery snapshot, restores by bounded block-cloning copies into the existing world dataset, validates the result, and records a completion point before restarting. It preserves `session.lock`, existing dataset identity, newer snapshots, metadata and symlinks. It uses no recursive ZFS rollback, forced receive or live mount switch. Interrupted jobs resume from durable phases; failed jobs retain maintenance and recovery points for explicit administrative investigation. Read job status before taking further action. After correcting a failure, an authenticated administrator can explicitly request `POST /v1/rollbacks/<job-id>/resume` with an empty JSON object. It returns202 for the same durable job; retries preserve its selected source and original recovery point. Failed jobs are never retried automatically, and resume must be intentional because it can resume world restoration while the server remains stopped.

Each successful `periodic_save` creates exactly one `mc-live-*` point. The Mod independently schedules `hourly_save`, actively performs a complete save/flush and waits for supported queues; that event creates exactly one `mc-hourly-*` point. Manual saves, clean shutdown and recovery points also use hourly. Scheduled hourly acceptance uses its own rate-limit timestamp, so a recent live point cannot delay it; all events still share one active operation and rollback maintenance gate. Existing durable two-member bundle intents from older versions retain their GUID-fenced recovery behavior. Normal cadence is about12 live and24 hourly snapshots, with additional manual/held recovery points possible. Neither family is promoted, sampled or pruned by a shared count limit.

Snapshot user properties `org.minecraft-zfs-backup:family` and `org.minecraft-zfs-backup:local-expires-at` persist the family and absolute Unix expiry. Actual ZFS creation determines the deadline; restart and configuration changes do not extend an existing deadline. Legacy hourly points without properties receive their actual creation plus the hourly TTL. OpenZFS itself does not execute TTL destruction. The companion runs cleanup on startup, after publication and autonomously at most60 seconds apart, so cleanup continues while the game is stopped. A stopped companion resumes overdue cleanup on startup.

Local retention deletes only expired owned published snapshots through individual non-forced, non-recursive destruction. It preserves foreign names and real external holds, skipping busy/held races. TrueNAS's hold-pending setting is retention bookkeeping and does not create kernel holds against an external pruner; configure native replication and outage monitoring accordingly.

## Validation

```sh
cd agent
python3 -m unittest discover -s tests -v
```

Most tests use temporary state and injectable ZFS/Crafty backends. Docker packaging checks need Compose; Java lock compatibility and actual disposable ZFS checks run when their prerequisites are available. Production rollback is never a test target. The standalone Python CLI remains available for development and reading local durable status; Docker is the deployment path.

## Rollback audit

The companion appends rollback evidence to `state_dir/rollback-audit.jsonl`, separate from the bounded operational `rollback.json`. Each private mode0600 JSON line is locked, appended and fsynced before an associated phase can perform side effects. Records identify the configured dataset, server timestamp, unique event ID, job ID, requester claim, snapshot/GUID, phase, recovery points and sanitized outcome. Authenticated request rejection, failure, explicit retry and process-restart resumption are recorded. Player identity comes from the Mod command source; a console label or legacy authenticated API client cannot identify an individual human.

Audit history is not automatically trimmed when completed job history reaches64 entries and is outside the world restore scope. Unsafe permissions, symlinks, a torn final record or a failed durable append block further rollback work until an administrator resolves the audit error. Existing safe-stop compensation can still disable restart and stop a server after a failed start/finalization. Audit failure never authorizes an unrecorded world restore. Keep this state directory durable when replacing the container.
