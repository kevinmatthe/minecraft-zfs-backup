# Independent hourly full-save checkpoints

The user clarified that hourly backups must remain available when native autosave is disabled or fails. Reusing the first autosave of each hour does not provide that independent guarantee.

## Required behavior

- Native successful autosave triggers `periodic_save`, creating only one `mc-live-*` point with its existing one-hour local TTL. Its existing completed-batch/IO barriers remain intact and do not force a second save.
- A Mod-owned independent scheduler triggers `hourly_save`: prepare through HTTP, force `saveEverything(false, true, true)` on the game thread, await the supported player/chunk/entity/POI queues, capture one `mc-hourly-*` point and commit only after saving flags are restored. Its TTL remains24hours.
- Hourly capture bypasses the player-activity filter, works with vanilla saving disabled, and restores every original noSave flag afterwards. It does not depend on the autosave observer firing.
- Establish a full baseline after server startup, then schedule at UTC interval boundaries; `hourly_interval_seconds` defaults3600 and accepts integer60..31536000. Initial capture and missed intervals do not fabricate historical snapshots. Use monotonic elapsed deadlines for the pending timer; after success compute the next future wall-clock boundary.
- Live/manual completion never advances or resets the hourly timer. Only an acknowledged matching hourly COMMITTED consumes the scheduled opportunity. On failure it remains pending and retries through the existing60second backoff. Only one save/capture transaction runs at once; a pending hourly request has priority over a new live capture, without cancelling an existing valid transaction.
- The agent accepts `hourly_save` separately. It creates only hourly for that event, and only live for periodic_save. A scheduled-hourly acceptance timestamp is independent of normal live/manual rate limiting, so a recent live cannot repeatedly suppress a due hourly. Mutual exclusion and all deadline checks remain.
- Retain version2 journal compatibility, including safe replay/cancellation of old two-member pending/publication bundles. Do not rename or prune existing points because their trigger behavior changes. TTL, audit, holds and external replication remain unchanged.
- Manual save, verified normal stop and rollback recovery continue to create hourly points with24hour expiry. Native TrueNAS replication selects hourly only and independently retains seven actual days. No cron/systemd/custom replication or file IPC.
- Add `hourly_interval_seconds` to both READMEs, Mod properties/example, API/deployment docs. `interval_seconds` remains ignored. Explain the independent forced hourly save and the completed-autosave live capture accurately; preserve paired-language configuration parity.

## Safety and observability

Forced save must succeed and supported queues must finish before snapshotting. Save-off flags must be restored to their original values, including true. HTTP waits never block running server ticks. Failed or timed-out work cannot publish a successful point; status/logs distinguish independent hourly state and retry. The shared existing save coordinator protects concurrent manual/live/hourly operations. Whole-world consistency is limited to the configured dataset and observed save integrations; an unresponsive game thread or failed storage cannot be repaired by an external snapshot.

## Verification

Use fake clocks and the existing real-library Java harness to prove no-autosave hourly capture, no reset by frequent live/manual successes, save-off restoration, failure retry, collision priority, shutdown cancellation and clock behavior. Python tests must prove event-to-family mapping, independent rate limiting and preservation of legacy bundle recovery/GUID fencing. Review specification and quality independently.

Run a separate full-modpack server on a disposable ZFS clone with `hourly_interval_seconds=60`. Issue `save-off`, alter a loaded chunk, and verify an `hourly_save` snapshot is created with no periodic_save event or console manual save. Read the selected snapshot's region data to confirm the changed block was flushed. Normal-stop cleanup and delete all fixture containers/network/datasets/copied files/secrets after retaining small evidence.

Deploy Mod2.3.0 and companion2.2.0 together after checks, using a protection checkpoint and normal Crafty stop/start; preserve state/audit, restore original restart policy, verify startup hourly and subsequent natural live points, and verify native hourly-only replication/GUIDs. Keep the protected11am ZIP untouched and publish the verified source/docs.
