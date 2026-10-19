# Optional independent synchronization task

The mod and host agent create local checkpoints. Replication is an optional, separately configured deployment task; it is never invoked by the game or host daemon.

`replicate.py` is a standalone Python 3.11+ standard-library utility for non-forced synchronization to a separate ZFS filesystem. Invoke it manually once before enabling local snapshot retention, then schedule it separately, for example with a five-minute systemd timer:

```sh
python3 /absolute/repository/deploy/replicate.py --config /absolute/sync.json run
python3 /absolute/repository/deploy/replicate.py --config /absolute/sync.json status
```

Root-owned JSON configuration (replace these generic values):

```json
{
  "source_dataset": "primary/minecraft-world",
  "target_dataset": "backup/minecraft-world",
  "state_dir": "/var/lib/minecraft-zfs-sync",
  "keep": 24
}
```

The configuration must be a root-owned regular file, not writable by group or others. Run as root. The task creates its state directory with mode `0700`; an existing directory must be root-owned and private. An exclusive `replication.lock` prevents overlapping runs. `replication.json` records success, errors, GUID, transfer mode, base, hold tag and destination retention results; files are mode `0600`. Configure a separate state directory for each source/target pair. Source and target must be distinct and neither can be a descendant of the other. The target parent dataset must already exist.

The selected source checkpoint is the newest published `mc-hourly-YYYYMMDDTHHMMSSZ` snapshot. Pending checkpoints are never sent. If the target already has snapshots, its latest snapshot must have exactly the same name and GUID on the source. Divergent targets, missing common bases and unexpected source changes fail closed. There is no forced receive, rollback, destructive from-scratch reset, source snapshot pruning, recursive destruction, or console access. An existing target must already be unmounted with `readonly=on`, `canmount=off`, and `mountpoint=none`; the task does not change unsafe existing targets. Initial receive applies these properties.

Transfers use `zfs send -c`, with `-i` for a verified incremental base, piped to `zfs receive -u -o mountpoint=none -o canmount=off -o readonly=on`. The receiver uses no force flag. After receipt, both source and destination must still have the selected GUID. Only then may the task prune old managed destination snapshots. It preserves foreign snapshot names and skips held or busy snapshots. It never prunes source snapshots.

Each source/target pair has a deterministic reserved hold tag, `mc-zfs-sync-<16 hex characters>`. Before transfer, the task places a real persistent ZFS hold on the verified common source base and the send candidate. It checks source GUIDs before and after acquiring each hold, failing safely if agent retention wins a race. Successful receipt retains the hold on the newest common source checkpoint before releasing obsolete holds with this exact task-owned tag. Other hold tags are preserved. Failed transfer or ambiguous post-receive verification retains both the previous common-base and candidate holds; the next verified successful run reconciles extra task-owned holds, including after a crash. The host agent respects these real holds, allowing one protected common base to remain beyond its normal local retention count. A failed task does not stop local snapshots, but an extended outage can retain old blocks through the held base and consume source pool space.

Monitor synchronization failures and pool capacity. Run the initial synchronization and confirm its source hold before enabling the local agent's retention. A five-minute timer normally protects the current base well before 24 hourly snapshots age out, but scheduling alone provides no guarantee. If the source base disappeared before the first successful run, or someone released its hold, the task refuses overwrite. Preserve the existing destination and re-seed into a new empty destination dataset under a separately reviewed configuration. Do not remove unrelated snapshots or release foreign holds to force a run. Before retiring this optional task, disable its timer and deliberately review its reserved holds; removing those holds can allow the local agent to prune the last old incremental base.

## Native TrueNAS replication caveats

On the inspected TrueNAS installation, `hold_pending_snapshots` is an input to zettarepl's retention planner. It does not create kernel ZFS holds and therefore cannot protect snapshots from an external agent's `zfs destroy` calls. Do not claim that this setting supplies the holds described above.

The installed zettarepl receive builder constructs `zfs recv -s -F`. Disabling `allow_from_scratch` prevents one explicit dataset recreation path; it does not remove the receive force flag. Consequently, native replication on that version does not meet this deployment's non-forced receive contract. The optional utility provides a separate task without modifying TrueNAS system packages. Reassess these implementation details when changing TrueNAS versions.

## Isolated validation

```sh
python3 -m unittest discover -s deploy/tests -p test_replicate.py -v
```

The tests use temporary state directories and in-memory metadata, covering full/incremental selection, missing or forged bases, GUID changes, persistent hold ordering, failures before/after verification, hold rotation, foreign snapshot preservation, busy retention, overlapping task locks and exact non-forced stream arguments. Validate actual ZFS streaming separately with disposable datasets before production deployment.
