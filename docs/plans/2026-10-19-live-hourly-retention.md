# Independent live and hourly snapshots

The local backup policy has two independent snapshot families, not a shared count limit or promotion of one recovery point into another tier.

| Family | Trigger | Local expiry | External replication |
| --- | --- | --- | --- |
| `mc-live-*` | Each successful native autosave, normally every five minutes | Actual ZFS creation + 3,600 seconds | None by default |
| `mc-hourly-*` | First successful autosave checkpoint in each UTC capture hour | Actual ZFS creation + 86,400 seconds | Optional separate administrator task |

The hourly checkpoint creates a distinct native ZFS snapshot beside the live snapshot within the same completed-save barrier. It never forces another world save. Normal cadence yields about 12 live and 24 hourly points; holds, manual saves, clean shutdown and rollback recovery can add points. Manual, clean-shutdown and rollback-recovery points use the hourly family and its 24-hour expiry. A cancelled or failed operation does not consume the hourly opportunity.

OpenZFS does not perform native TTL destruction. The companion persists family and absolute local expiry as snapshot user properties, then deletes expired local points individually. Expiry continues while Minecraft is stopped and resumes after companion restart; restarting does not extend deadlines. No cron, systemd or mailbox polling is introduced. Foreign snapshots, other datasets, active publication intents, rollback maintenance and real ZFS holds remain protected. Old published hourly points receive their actual original creation + 24-hour expiry.

The Mod continues to own save consistency and actively invokes the existing HTTP prepare/snapshot/commit API. The companion publishes every member only after a valid matching commit, journals the whole bundle before mutation, checks GUIDs, and recovers interrupted renames idempotently. The existing primary `snapshot` response remains compatible; an additional `snapshots` array describes the bundle. Both families appear in local listing, rollback and snapshot-name completion.

Storage is outside the Mod/agent: this deployment's native TrueNAS replication selects only `mc-hourly-*` and retains seven actual days. It does not select live snapshots. No local TrueNAS cleanup task is required. TrueNAS can match externally created snapshots to periodic-task retention, but that coupling is not used by this portable local policy.

Implementation and verification:

1. Add failing Python tests for independent families, immutable expiry, per-family pruning, held points, failed/cancelled bundles, restart publication, legacy journals and autonomous cleanup.
2. Implement fixed administrator TTL configuration, durable snapshot metadata, bundle handling and a companion-owned expiry timer.
3. Add failing real Brigadier/HTTP tests, then extend strict local snapshot validation, list and completion to both families without changing autosave timing.
4. Review both implementations; build against the installed matching game libraries and build the Docker image.
5. Validate real ZFS metadata/expiry and game events with disposable local fixtures. Preserve only small evidence and remove all fixtures.
6. Deploy the Mod and companion together with a protection checkpoint and normal Crafty stop/start; restore existing restart policy. Verify local families and hourly-only native replication, then update operational documentation and publish the verified changes.

Additional user requirement: rollback must have a separate durable audit. Capture actual player/console actor information in the Mod request; keep legacy API callers compatible. The companion records authenticated requests/rejections, phase intents, recovery identities, terminal results, explicit retry and process-restart resumption in an fsynced append-only JSONL file outside the restored world. Audit history must outlive bounded job-history pruning; audit failure must prevent unrecorded destructive work while preserving necessary safe-stop compensation. Add fault and persistence tests before deployment.
