# Protocol

Version2 uses active authenticated HTTP requests between the Mod and companion container. The complete endpoint, event, timing, idempotency and rollback specification is in [HTTP API v1](http-api.md).

For an automatic checkpoint, the Mod observes a complete successful vanilla autosave, freezes new background save submissions, prepares an HTTP operation and waits for all captured asynchronous writes without saving again. An explicit manual checkpoint prepares before freezing and forcing one save. Both paths then request a pending snapshot. It restores the original saving flags before committing publication. Timeouts cancel the operation and restore saving. A pending snapshot is excluded from native replication. Each successful periodic checkpoint creates a live snapshot expiring after one hour; the first committed periodic checkpoint of each UTC hour additionally creates a separate hourly snapshot expiring after24 hours inside the same save barrier. Manual, normal-shutdown and rollback-recovery points use hourly. A durable bundle intent and GUID checks cover every member; failed or cancelled bundles do not consume the hourly opportunity.

The companion persists absolute per-snapshot expiry in ZFS user properties and cleans up both families independently, including while Minecraft is stopped. Cleanup resumes after companion restart without extending deadlines. There is no shared count cap or tier promotion. External replication selects hourly only and manages destination retention separately.

Server-started and server-stopped events have separate semantics. A stopped-world checkpoint runs after world close, and the companion checks the actual session lock before creating it. Rollback is a durable maintenance operation coordinated through Crafty, with recovery points and offline world locking.

The former1.x shared-directory protocol is retired. Remove mailbox mounts and `bridge_directory` configuration when upgrading. Durable JSON in the companion's private state directory is an internal crash-recovery journal; it is not a Mod communication channel.
