# Validation

Validated on2026-10-19 using Java21, Minecraft1.21.1, NeoForge21.1.251 and OpenZFS2.3.0. The companion image uses Debian ZFS userspace2.3.9 and Python3.13; Python3.11 is also tested.

## HTTP version2

- The portable builder compiles against the actual matching server libraries. All67Java checks pass: HTTP checkpoint lifecycle, player activity, real HTTP client requests, bounded headers/body handling, command rendering, dimension-saving restoration and clean-shutdown evidence.
- Real Java21 FileChannel locks and Python POSIX record locks exclude one another in both directions. A disposable ZFS fixture restores files from its immutable snapshot, preserves the lock inode and deletes extraneous files.
- Actual TLS requests using a trusted test certificate pass on both Python3.11 and3.13. Real slow-drip response headers/body cannot extend request or stop/start deadlines. HTTP inbound slow-drip regression also bounds request reading and shutdown.
- A separate full modpack server runs the2.0JAR through Crafty4.11.0 using a dedicated non-superuser account scoped to that server. TLS certificate verification remains enabled.
- Two real manual checkpoints complete PREPARED→SNAPSHOT_READY→COMMITTED with distinct snapshot GUIDs. Game commands still respond when the companion is unavailable.
- The optional shutdown Mixin applies to the actual full modpack. A normal save/close produces a stopped event requesting backup; rollback maintenance correctly records and skips that request.
- A deliberate integration failure caused by a private Docker bind hiding snapshot automounts stops safely before world writes. The selected point and distinct pre-rollback recovery point remain held, the live test marker remains unchanged, and Crafty automatic restart stays disabled. A world bind with `rslave` propagation then exposes newly created snapshot mounts without adding privileges.

- A fresh `/zfsbackup rollback` command completes stop→pre-recovery→restore→validate→start without interruption. The selected marker and extra-file presence match the chosen snapshot; the distinct pre-recovery snapshot retains the prior marker. Source and recovery holds are released only after successful readiness and policy restoration. Explicit resumption of the earlier safe failure also succeeds without replacing its original pre-recovery point.
- A subsequent normal stop publishes a `server_stopped` checkpoint. Disposable containers and their private network are removed after validation; small dataset/state evidence remains isolated.
- Python3.11 suite:81tests,79passed and2prerequisite-gated tests skipped. The optional Java-lock and real-ZFS tests were also exercised separately. Reviews found and closed shutdown, HTTP deadline, TLS compatibility and publication-recovery issues.
- Production runs Mod2.0 with a healthy private-network HTTP companion and the scoped Crafty integration. Its real manual checkpoint `mc-hourly-20261019T075316Z` has GUID `14209830665387297159` on both source and Storage. Native TrueNAS replication job21747 succeeded. Production rollback was not used as a test.

## Earlier snapshot and replication validation

Version1 completed43Java checks and50Python/packaging checks. A separate server completed real flush/FROZEN/COMMIT checkpoints with its asynchronous player-save integration installed. Suspending the companion caused a five-second timeout and cancellation; the server continued commands with automatic saving restored. Empty-server automatic checkpoints skipped, while manual checkpoints committed.

Native TrueNAS replication completed full and incremental transfers on a disposable dataset. Destination GUIDs matched, and a read-only restored clone matched every file in the immutable source snapshot. An externally held recovery point survived local keep=3 retention. The deployed version1Docker companion completed a real Mod checkpoint followed by native replication with matching GUIDs; companion restart preserved its ledger.

A complete clean Gradle distribution/dependency build has not been exercised in this environment; the portable actual-library builder is the verified build path. These tests do not establish consistency for arbitrary third-party mod databases or external services outside the dedicated world dataset.
