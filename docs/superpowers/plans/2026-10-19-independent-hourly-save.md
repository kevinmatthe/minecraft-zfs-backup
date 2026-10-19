# Independent Hourly Save Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development with TDD and independent specification/quality review. Continue within the user's authorized backup/deployment scope.

**Goal:** Full hourly backups run independently of native autosave, while live snapshots continue to follow completed autosaves.

**Architecture:** Add a Mod scheduler and explicit hourly save event, reuse the forced manual save coordinator and barriers, and map each HTTP event to one local snapshot family. Preserve old durable journal bundles for crash recovery.

**Tech Stack:** Java21, Minecraft1.21.1, NeoForge21.1.251, Python3.11+/3.13, Docker and OpenZFS2.3.

### 1. Mod timer and complete-save event

Files: `mod/src/main/java/local/zfsbackup/Checkpoint.java`, new focused scheduler class/tests as needed, `ZfsBackupMod.java`, matching real HTTP/core tests and Mod version metadata.

- [ ] Add failing fake-clock checks: absent autosave still requests hourly, repeated live/manual successes do not postpone it, startup baseline, due priority, failed full save/barrier/HTTP retries with flags restored, original disabled saving restored, shutdown and monotonic clock behavior.
- [ ] Implement separate scheduler, `hourly_save` entry point and acknowledged success generation/notification without duplicating save coordination. Configure `hourly_interval_seconds=3600`, bounds60..31536000, and expose useful status/generic failure diagnostics. Preserve existing autosave hooks/gates/native IO code unless directly required.
- [ ] Build against actual supplied libraries using `python3 mod/build.py --container minecraft-crafty --server-dir /crafty/servers/48577ca8-7812-4be5-8cc6-10f9e403d205`. Review source and commit only owned Mod implementation/tests/version files as2.3.0.

### 2. Agent event routing and compatibility

Files: `agent/minecraft_zfs_agent/coordinator.py` and relevant Python tests. TTL/ZFS/audit code remains unchanged unless a demonstrated defect requires a focused correction.

- [ ] Add failing tests that periodic_save produces exactly one live point; hourly_save produces exactly one hourly; a recent live does not rate-limit hourly; repeated hourly requests retain rate limiting and mutual exclusion.
- [ ] Implement explicit event validation/routing and the separate scheduled-hourly acceptance timestamp. Remove autosave-driven hourly selection, retaining the legacy bundle reconciliation code.
- [ ] Replace superseded automatic-bundle expectations with independent-trigger tests and explicit legacy-bundle fixtures. Keep all meaningful partial-create/rename, GUID, cancellation, timeout and restart safety coverage.
- [ ] Run `PYTHONPATH=agent python3 -m unittest discover -s agent/tests -q`; review and commit owned Python changes.

### 3. Documentation and independent review

Files: `README.md`, `README.zh-CN.md`, `mod/README.md`, `mod/local-zfs-backup.properties.example`, `agent/README.md`, deployment/API/protocol/verification docs.

- [ ] Update bilingual rules, configuration tables/examples and diagram labels for independent hourly/full-save versus native live capture. Explain startup baseline, UTC boundaries, retries, player filter scope and unchanged TTL/replication/audit.
- [ ] Obtain separate specification and quality review; close blockers with regression tests.
- [ ] Validate JSON/Compose/shell/link examples and language parity.

### 4. Disposable integration and deployment

- [ ] Build companion2.2.0; run host and image Python checks.
- [ ] Prepare a private full-modpack fixture on a COW clone, separate tokens/state/network and a60second hourly interval. Disable saving, change a loaded block, verify independent hourly event and actual saved chunk contents, plus original flag restoration.
- [ ] Record small evidence and remove all fixture resources.
- [ ] Protect and stop production through Crafty, install paired versions, preserve audit/configuration and restore restart policy after startup readiness.
- [ ] Verify immediate startup hourly/full-save point, a later successful native live point, native TrueNAS hourly-only replication/GUID, protected ZIP and clean working tree. Publish source and final documentation to main.
