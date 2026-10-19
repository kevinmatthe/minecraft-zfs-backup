# Mod-driven ZFS backups implementation plan

**Goal:** Implement and validate the dedicated Minecraft mod and persistent host agent in this new repository, replacing cron/console orchestration after end-to-end verification.

**Architecture:** NeoForge server lifecycle and tick hooks own backup timing, preserve/restore world saving flags and complete save barriers. An expiring shared-directory protocol lets a host daemon snapshot the fixed world dataset, publish only a matching commit and retain local snapshots. Systemd runs the host daemon, with a TrueNAS POSTINIT installer for reboot persistence.

**Tech stack:** Java 21, NeoForge 21.1.251 / MC 1.21.1, Python 3.11 standard library, OpenZFS 2.3, systemd.

This is a newly created isolated repository. Work on the implementation branch; no linked worktree is needed. See `docs/protocol.md` for the frozen message contract.

- [ ] Initialize repository, ignore build/runtime artifacts and commit design/protocol.
- [ ] Mod: first write failing pure-Java lifecycle/transport tests, then implement interval scheduling, fresh heartbeat gate, save barriers, matching request/response, commit/cancel, timeout and shutdown restoration. Add metadata and portable build instructions; compile against real server libraries and standard Gradle metadata.
- [ ] Agent: first write failing protocol/backend tests, then implement coordinator/pending ledger, safe local ZFS engine, restart reconciliation, rate limits, and durable CLI config. Test malformed/stale/cancelled requests, GUID replacement, retention and foreign snapshot preservation.
- [ ] Review spec compliance and code quality independently; address material findings and rerun affected tests.
- [ ] Root integration: add unit/service installer and example configs; run real disposable-dataset protocol tests.
- [ ] Isolated Minecraft integration: use a separate validation server and test dataset; verify loaded mod, native timing, successful world flush/snapshot/commit, actual incremental replication, timeout/host loss restoring saving, server shutdown behavior and restored world content.
- [ ] Document results and commit implementation. If replacing live orchestration, first run existing safety backup, confirm zero players and save/stop, install tested JAR + daemon config, start/recheck, then disable legacy cron only after the mod has created a verified replicated checkpoint. Preserve the user-selected 11:00 legacy ZIP.

Repository visibility: public. Do not publish live host paths, account secrets, actual player identities or runtime artifacts in the reusable project.
