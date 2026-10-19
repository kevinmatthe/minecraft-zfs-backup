# Autosave-driven world checkpoints

The Minecraft server already performs a periodic full save. The backup Mod must observe that complete save batch instead of initiating a second periodic save. The production idle filter is disabled because forced-loaded chunks can change without players. User-approved retention is the newest 24 local points and one week on Storage.

## Behavior

Observe the pinned 1.21.1 MinecraftServer tickServer autosave invocation without replacing the existing AsyncPlayerSave redirect. Only a successful complete saveEverything call creates an automatic checkpoint. Capture the existing saving flags immediately after the batch, prepare via bounded asynchronous HTTP, await player/world and native region IO completion, request the snapshot, restore every prior saving flag, then commit. Do not invoke saveEverything again for this automatic path. Network waits and pending IO must not block the server tick. Failed, busy, timed-out or cancelled operations must restore flags and cannot publish a point; wait for the next actual autosave to retry.

Manual /zfsbackup save retains forced flush behavior. Normal shutdown continues using positive save/close evidence. Preserve published names and HTTP periodic_save semantics for existing replication and command completion. The adapter always uses autosave-driven mode; the prior timer is not scheduled.

## Product boundary and deployment replication

The Mod and companion manage a generic administrator-configured local dataset only: local snapshot creation,24-point retention, local-name completion and local rollback. RapidPool is this deployment's pool name, not a product dependency. Storage histories never enter the game completion list or rollback API.


Native TrueNAS replication task 7 keeps snapshots independently for one actual week using CUSTOM retention. The installed engine compares UTC names against a naive Asia/Shanghai clock, so the deployment uses176configured hours (168+8) to retain168actual hours; this compensation remains outside the product. TrueNAS rejects custom TTL with name_regex, so use the equivalent mc-hourly-%Y%m%dT%H%M%SZ naming schema. Exclude pending snapshots, keep the current dedicated destination and no-from-scratch policy. Local retention remains 24.

## Verification

Tests must prove no extra forced save on autosave, one checkpoint per batch, asynchronous barriers, timeout/failure/shutdown restoration, no independent timer, and retained manual behavior. Build against installed Java21/Minecraft1.21.1/NeoForge21.1.251. Validate transformed hooks with a disposable full-modpack server/world and real companion; no production test rollback. Remove validation datasets/containers/trees after collecting small evidence. Deploy by checkpointing and normal Crafty stop/start, then verify readiness and policies.
