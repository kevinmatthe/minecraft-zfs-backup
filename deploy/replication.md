# Optional TrueNAS replication

The Mod and Docker agent create local world snapshots. Off-pool replication is configured entirely in TrueNAS **Data Protection → Replication Tasks**. No custom synchronization daemon, host timer or cron job is required.

Create a local PUSH replication task with these settings:

| Setting | Value |
| --- | --- |
| Source | The dedicated world dataset from agent configuration |
| Destination | A dedicated backup dataset on the storage pool |
| Recursive | Disabled for this single-world source |
| Snapshot selection | Name regex `^mc-hourly-[0-9]{8}T[0-9]{6}Z$` |
| Schedule | For example, every five minutes |
| Destination retention | Same as source (`SOURCE`) |
| Read-only destination | Set |
| Allow from scratch | Disabled |
| Compressed stream | Enabled |

Keep the destination unmounted: `mountpoint=none`, `canmount=off`. Exclude `pending-*` snapshots by selecting only the published naming pattern. The replication task copies completed recovery points after the game has restored saving; it does not trigger game backups or communicate with the Mod.

If an existing backup destination already contains snapshots, compare its latest snapshot GUID with the corresponding source GUID before adopting it. Keep this destination exclusive to the replication task. Run the native task manually once, then verify matching source and destination GUIDs. Destination retention, scheduling and failure alerts remain visible in TrueNAS.

Local retention and replication are independent. Keep enough local recovery points for the longest outage you intend to tolerate. TrueNAS's hold-pending option controls its own retention planner; it does not create kernel ZFS holds that prevent this Docker agent from pruning. The agent preserves real external holds, but does not manage replication baselines. If every common source recovery point expires, keep the old destination for recovery and investigate before re-seeding; do not enable from-scratch replacement merely to clear the error.

TrueNAS's replication implementation controls receive flags, including its use of `receive -F`. Disabling allow-from-scratch prevents automatic recreation of the target; it does not remove that receive flag. Use a dedicated, read-only destination and do not place independent writable data there.

The whole Minecraft service-directory dataset, including BlueMap and configuration, can have a separate native snapshot/replication policy. The agent's game-consistent checkpoint contract covers the configured dedicated world dataset only.
