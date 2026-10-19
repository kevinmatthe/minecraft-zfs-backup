# Local snapshot host agent

Python 3.11 or newer; runtime uses only the standard library. The host needs `zfs`, Linux `findmnt`, and root privileges. The source must already be the dedicated dataset mounted at the configured world path, with `level.dat` present. The agent manages local snapshots. Configure replication separately in native TrueNAS tasks.

Run directly from the checkout:

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

No target dataset is required. The configuration file must be root-owned and not writable by group or others. The state directory must be root-owned with mode `0700`; the daemon creates it if absent. The installer must create the mailbox so the game UID can write it. Root writes shared responses and heartbeat with mode `0644`, including under a private service umask. Persistent ledger and retention status use mode `0600`. The production interval policy is at least 60 seconds; lower values are intended for isolated validation only. The agent never creates or changes the source dataset or its mount properties.

`heartbeat.json` refreshes every half second independently of snapshot commands. Its readiness requires fresh successful source preflight. Requests are strict v1 objects, limited to 16 KiB, with canonical UUIDs, integer timestamps, an at most 60-second freeze window, and exact phase-specific fields. The host never uses mailbox messages for commands, datasets, or filesystem paths.

The durable ledger records intent before creating an unpublished `pending-mc-hourly-*` snapshot. Snapshot completion must pass a second check of the same still-current FROZEN request. A matching COMMIT is accepted only for its original timestamp window and recorded GUID, within 60 seconds after its original deadline. A durable publish intent makes a crash around the ZFS rename recoverable. Repeated COMMIT requests are idempotent. Cancellation and superseding FROZEN requests remove the recorded pending snapshot; acknowledged pending snapshots are retained through the 60-second COMMIT receive grace before timeout cleanup. Startup removes strictly named orphan pending snapshots. A changed pending GUID requires administrator investigation and is never destroyed automatically.

COMMITTED acknowledges the local rename. Local retention runs after completion, including if response publication temporarily fails. The host retains the newest `keep` managed published snapshots and preserves foreign snapshots. It checks external ZFS holds before destruction and skips snapshots that become held or busy during destruction. Retention uses individual non-forced, non-recursive `zfs destroy` calls. Held replication bases can therefore extend retention beyond the configured count; native TrueNAS replication tasks control their own hold and destination retention policies. Native tasks should select published `mc-hourly-*` snapshots and exclude `pending-*` snapshots.

`status` prints the heartbeat, lifecycle ledger, retention result, and whether the heartbeat is currently fresh and ready. Status files are `state_dir/ledger.json` and `state_dir/retention.json`. Standard logging goes to stderr for the service journal. An exclusive `flock` prevents duplicate host agents.

Run isolated tests without ZFS or Minecraft:

```sh
cd agent
python3 -m unittest discover -s tests -v
```

Tests use temporary mailboxes and injectable in-memory ZFS backends. Real ZFS and Minecraft save-barrier validation belong in a disposable integration environment.
