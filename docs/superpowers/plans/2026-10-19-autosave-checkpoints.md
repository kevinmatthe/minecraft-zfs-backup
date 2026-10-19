# Autosave-driven Checkpoints Implementation Plan

> Execute inline with TDD and review using superpowers:executing-plans.

**Goal:** Reuse natural autosaves for game-consistent ZFS checkpoints, preserving manual/shutdown behavior and local24/remote7day retention.

**Architecture:** Extend Checkpoint with an autosave-only automatic mode and a completed-save entry point that freezes and drains IO without calling flush. A MinecraftServer mixin observes the whole natural tickServer save batch. Native IO workers and optional async integrations provide nonblocking barriers.

**Tech Stack:** Java21, Minecraft1.21.1, NeoForge21.1.251, Mixin, existing Python HTTP agent and native TrueNAS replication.

- [x] Add AutosaveCheckpointTest behavioral cases and observe failure before implementation.
- [x] Add Checkpoint.autosave() and autosave-driven constructor mode; timer ticks only expire operations in that mode. Refactor shared preparation so automatic saves omit port.flush() while manual saves preserve it.
- [x] Add a testable per-batch observer and MinecraftServer hooks around the natural save invocation, preserving AsyncPlayerSave redirect compatibility.
- [x] Await installed async-save and native chunk/entity/POI IO workers without serializing mutable game state on background threads. Test failure propagation and no blocking ticks.
- [x] Build with python3 mod/build.py --container minecraft-crafty --server-dir /crafty/servers/48577ca8-7812-4be5-8cc6-10f9e403d205; all checks must pass.
- [x] Review, then validate actual mixin transformation and snapshot lifecycle on disposable full-modpack server and world. Remove all bulky fixtures.
- [x] Update API/deployment docs, deploy the versioned Mod with a normal Crafty restart, restore prior policies, verify source/target snapshot GUIDs and TrueNAS CUSTOM 1 WEEK.
