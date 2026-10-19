# Validation

Validated on 2026-10-19 with Java 21, Minecraft 1.21.1, NeoForge 21.1.251, Python 3.11 and OpenZFS 2.3.0.

- 43 Java checks pass against actual server libraries: lifecycle, transport, dimension-saving restoration, real FIFO regressions and player-activity filtering.
- 47 host-agent unit tests and 27 installer/optional-replication tests pass.
- A separate modded server and disposable source dataset completed real flush/FROZEN/COMMIT checkpoints. The optional asynchronous player-save integration was installed.
- With the host agent suspended, the server reached its five-second checkpoint timeout, published CANCEL, continued processing commands and confirmed automatic saving was already enabled.
- Empty-server automatic checkpoints were skipped; manual checkpoints committed with no players.
- The separate synchronization command completed compressed full and incremental transfers. Destination GUIDs matched; a read-only restored clone matched every file in the immutable source snapshot.
- With local keep=3, a held incremental base temporarily extended retention to four. After a successful transfer rotated the hold, the next checkpoint returned retention to three. Destination retention also passed.

The portable actual-library builder was exercised. A complete clean Gradle distribution/dependency build has not yet been exercised in this environment. These tests do not establish application-level consistency for arbitrary third-party mod databases or external services.
