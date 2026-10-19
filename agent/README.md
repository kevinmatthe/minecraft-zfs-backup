# Local snapshot host agent

Run the agent as a Docker container alongside the server-side mod. It manages local snapshots; configure Storage replication independently in native TrueNAS. The source must already be the dedicated ZFS dataset mounted at the configured world path, with `level.dat` present.

Build the image from the repository root:

```sh
docker build -t minecraft-zfs-agent:local .
```

The image uses Debian trixie, Python 3.13, Linux `findmnt`, and ZFS 2.3 userspace from [Debian's `contrib` repository](https://packages.debian.org/trixie/zfsutils-linux). It uses the host's existing ZFS kernel module and does not install a kernel module. Builds reject an unexpected ZFS userspace major/minor. If Docker's build network cannot resolve Debian mirrors on your host, build with `docker build --network=host -t minecraft-zfs-agent:local .`; the running service still has networking disabled.

Use [deploy/compose.example.yaml](../deploy/compose.example.yaml). Set `AGENT_CONFIG` to the absolute path of your root-owned JSON configuration; set `WORLD_PATH`, `MAILBOX_PATH`, and `STATE_DIR` to the exact absolute host paths also used in that JSON. Pre-create the mailbox for the game UID and the private root-owned state directory. Then run from the repository root:

```sh
docker compose -f deploy/compose.example.yaml up -d --no-build
docker compose -f deploy/compose.example.yaml logs --tail=50 agent
docker compose -f deploy/compose.example.yaml exec agent python3 -m minecraft_zfs_agent --config /etc/minecraft-zfs-agent.json status
```

The Compose example mounts the world read-only at its exact host absolute path so strict dataset/mount verification also works in the container namespace. It mounts the mailbox and state read/write at their configured absolute paths, and configuration read-only at `/etc/minecraft-zfs-agent.json`. Missing bind sources are rejected instead of being created as directories. It exposes `/dev/zfs`, drops default capabilities, and adds `SYS_ADMIN` for ZFS ioctls plus `DAC_OVERRIDE` to write the game-owned private mailbox. The container has a read-only root filesystem, no network, and no Docker socket, host PID namespace or privileged mode. These permissions still allow host ZFS administration through `/dev/zfs`; only trusted administrators should control the image and configuration. The heartbeat health check reports whether the exact source is ready.

For development, the Python 3.11+ package can also run directly from the checkout:

```sh
PYTHONPATH=/absolute/repository/agent python3 -m minecraft_zfs_agent --config /absolute/config.json run
PYTHONPATH=/absolute/repository/agent python3 -m minecraft_zfs_agent --config /absolute/config.json status
```

Administrator JSON configuration fields:

| Field | Meaning | Default |
| --- | --- | --- |
| `source_dataset` | Dedicated existing world dataset | required |
| `world_path` | Exact absolute source mount path on the host | required |
| `mailbox_path` | Pre-created shared host directory corresponding to the mod mailbox | required |
| `state_dir` | Private host ledger/status directory outside the mailbox | required |
| `keep` | Newest managed published local snapshots retained | `24` |
| `poll_seconds` | Request polling interval, greater than zero and at most one second | `0.2` |
| `min_interval_seconds` | Minimum interval between accepted checkpoints | `60` |

No target dataset is required. The configuration file must be root-owned and not writable by group or others. The state directory must be root-owned with mode `0700`; the daemon creates it if absent. Create the mailbox so the game UID can write it before starting Compose. Root writes shared responses and heartbeat with mode `0644`, including under a private service umask. Persistent ledger and retention status use mode `0600`. The production interval policy is at least 60 seconds; lower values are intended for isolated validation only. The agent never creates or changes the source dataset or its mount properties.

`heartbeat.json` refreshes every half second independently of snapshot commands. Its readiness requires fresh successful source preflight. Requests are strict v1 objects, limited to 16 KiB, with canonical UUIDs, integer timestamps, an at most 60-second freeze window, and exact phase-specific fields. The host never uses mailbox messages for commands, datasets, or filesystem paths.

The durable ledger records intent before creating an unpublished `pending-mc-hourly-*` snapshot. Snapshot completion must pass a second check of the same still-current FROZEN request. A matching COMMIT is accepted only for its original timestamp window and recorded GUID, within 60 seconds after its original deadline. A durable publish intent makes a crash around the ZFS rename recoverable. Repeated COMMIT requests are idempotent. Cancellation and superseding FROZEN requests remove the recorded pending snapshot; acknowledged pending snapshots are retained through the 60-second COMMIT receive grace before timeout cleanup. Startup removes strictly named orphan pending snapshots. A changed pending GUID requires administrator investigation and is never destroyed automatically.

COMMITTED acknowledges the local rename. Local retention runs after completion, including if response publication temporarily fails. The host retains the newest `keep` managed published snapshots and preserves foreign snapshots. It checks external ZFS holds before destruction and skips snapshots that become held or busy during destruction. Retention uses individual non-forced, non-recursive `zfs destroy` calls. Held replication bases can therefore extend retention beyond the configured count; independent replication tasks control their own hold and destination retention policies. TrueNAS hold-pending is retention bookkeeping and does not replace real ZFS holds when this agent prunes snapshots. Replication tasks should select published `mc-hourly-*` snapshots and exclude `pending-*` snapshots.

`status` prints the heartbeat, lifecycle ledger, retention result, and whether the heartbeat is currently fresh and ready. Status files are `state_dir/ledger.json` and `state_dir/retention.json`. Standard logging goes to stderr for Docker logs. An exclusive `flock` prevents duplicate host agents.

Run isolated tests without ZFS or Minecraft (Docker Compose is needed for packaging-contract checks):

```sh
cd agent
python3 -m unittest discover -s tests -v
```

Tests use temporary mailboxes and injectable in-memory ZFS backends. Real ZFS and Minecraft save-barrier validation belong in a disposable integration environment.

Docker validation on the inspected TrueNAS host used ZFS userspace 2.3.9 against kernel module 2.3.0. Exact-path read-only bind preflight passed with `SYS_ADMIN`; writing a game-owned mode0700 mailbox additionally required `DAC_OVERRIDE`. Repeat disposable-dataset validation when changing host ZFS or container userspace versions. The mod schedules checkpoints; Docker keeps the agent running. No host scheduler is needed for local snapshots.
