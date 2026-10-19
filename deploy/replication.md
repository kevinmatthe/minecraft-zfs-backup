# Optional TrueNAS replication

The Mod and Docker agent create local world snapshots. Off-pool replication is configured entirely in TrueNAS **Data Protection → Replication Tasks**. No custom synchronization daemon, host timer or cron job is required.

Create a local PUSH replication task with these settings:

| Setting | Value |
| --- | --- |
| Source | The dedicated world dataset from agent configuration |
| Destination | A dedicated backup dataset on the storage pool |
| Recursive | Disabled for this single-world source |
| Snapshot selection | Additional naming schema `mc-hourly-%Y%m%dT%H%M%SZ` only |
| Schedule | For example, every five minutes |
| Destination retention | For example, Custom: 1 week, independently of local retention |
| Read-only destination | Set |
| Allow from scratch | Disabled |
| Compressed stream | Enabled |

Verify the installed retention engine's clock handling when names contain UTC timestamps. Some TrueNAS/zettarepl versions compare parsed name times against the local wall clock even when the suffix is Z; an Asia/Shanghai deployment needs eight hours of lifetime compensation to retain seven actual days. Keep such storage-specific compensation in the native task, outside Mod/agent configuration.

Keep the destination unmounted: `mountpoint=none`, `canmount=off`. Exclude `pending-*` snapshots by selecting only the published naming pattern. TrueNAS permits custom destination lifetimes with a naming schema; its name-regex selector only permits Same as Source or None. The equivalent schema preserves existing snapshot names and supports an independent one-week destination history while the companion independently expires local live snapshots after one hour and hourly snapshots after24 hours. Live snapshots are deliberately not selected for replication. The replication task copies completed recovery points after the game has restored saving; it does not trigger game backups or communicate with the Mod.

If an existing backup destination already contains snapshots, compare its latest snapshot GUID with the corresponding source GUID before adopting it. Keep this destination exclusive to the replication task. Run the native task manually once, then verify matching source and destination GUIDs. Destination retention, scheduling and failure alerts remain visible in TrueNAS.

Local retention and replication are independent. Keep enough local recovery points for the longest outage you intend to tolerate. TrueNAS's hold-pending option controls its own retention planner; it does not create kernel ZFS holds that prevent this Docker agent from pruning. The agent preserves real external holds, but does not manage replication baselines. If every common source recovery point expires, keep the old destination for recovery and investigate before re-seeding; do not enable from-scratch replacement merely to clear the error.

TrueNAS's replication implementation controls receive flags, including its use of `receive -F`. Disabling allow-from-scratch prevents automatic recreation of the target; it does not remove that receive flag. Use a dedicated, read-only destination and do not place independent writable data there.

The whole Minecraft service-directory dataset, including BlueMap and configuration, can have a separate native snapshot/replication policy. The agent's game-consistent checkpoint contract covers the configured dedicated world dataset only.

Game commands and snapshot-name completion list local published points only. Remote-only snapshots retained on Storage are not offered for in-game rollback. Recover those separately through the storage administrator.
