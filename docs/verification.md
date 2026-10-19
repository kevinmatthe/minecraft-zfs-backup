# Validation

Validated on 2026-10-19 with Java 21, Minecraft 1.21.1, NeoForge 21.1.251, Python 3.11 and OpenZFS 2.3.0.

- 43 Java checks pass against actual server libraries: lifecycle, transport, dimension-saving restoration, real FIFO regressions and player-activity filtering.
- 50 host-agent and Docker packaging checks pass.
- A separate modded server and disposable source dataset completed real flush/FROZEN/COMMIT checkpoints. The optional asynchronous player-save integration was installed.
- With the host agent suspended, the server reached its five-second checkpoint timeout, published CANCEL, continued processing commands and confirmed automatic saving was already enabled.
- Empty-server automatic checkpoints were skipped; manual checkpoints committed with no players.
- Native TrueNAS replication completed full and incremental transfers on the disposable dataset. Destination GUIDs matched; a read-only restored clone matched every file in the immutable source snapshot.
- With local keep=3, an externally held snapshot was preserved beyond the retention count.
- The Docker image uses Debian ZFS userspace 2.3.9 against the existing host kernel module 2.3.0. Exact read-only mount checks, pending-only FROZEN, committed GUID preservation, idempotent COMMIT and CANCEL cleanup passed in a disposable container with no network and a read-only root filesystem.

The deployed Docker companion also completed a real Mod checkpoint, followed by native TrueNAS incremental replication with matching source/destination GUIDs. Restarting the companion preserved its ledger.

The portable actual-library builder was exercised. A complete clean Gradle distribution/dependency build has not yet been exercised in this environment. These tests do not establish application-level consistency for arbitrary third-party mod databases or external services.
