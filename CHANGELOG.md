# Changelog

All notable changes to this project will be documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased]

### Added

- Hash-locked runtime and build dependency sets shared by local bootstrap, CI, Docker, and systemd installation.
- Versioned systemd releases with atomic `current` activation, interruption journaling, service-state restoration, and a standalone rollback command.
- Public documentation navigation, executable clone-first quick starts, compatibility fields in bug reports, and clearer community reporting guidance.
- Release tags now pass the complete reusable CI workflow before publishing, with version validation, SBOM, provenance, and image-digest reporting.
- Tolerant stationary-Core confirmation across short visibility gaps, while still requiring three real same-position observations before a raid.
- Structured v0.11 upkeep due/paid/deficit and excess-Unit damage diagnostics with deterministic supervisor and optional model-review triggers.
- Bounded long-range raids against confirmed stationary, unprotected Cores, with strike-distance hysteresis and immediate combat-pressure recall.
- Gameplay v0.13 and official SDK 0.2.8 compatibility, including conservative Ranger cell fire against a confirmed stationary Core during short visibility gaps.
- Hierarchical lifecycle and threat assessment with explicit posture/reason diagnostics for alerts, pre-evasion, engagement, multi-axis breakout, recovery, and compatibility hold.
- Gameplay v0.14 and official SDK 0.2.9 compatibility, including dynamic Unit-price boundary tests and authoritative spawn-cost reconciliation.
- Bounded SQLite Turn history and a local tactical dashboard with historical
  vision, unit trails, enemy Core memory, events, and public leaderboards.
- The armada sweep is now part of the aggregated strategy summary: `strategy_phase()`
  reports `ARMADA_SWEEP`, and a new `strategy_summary()` feeds the sweep chunk,
  commitment Tick, formation mode, and gather state to systemd status, SQLite
  history, and the dashboard.

### Changed

- The Docker base image is pinned to an immutable multi-architecture digest.
- GitHub Actions are pinned to full commit SHAs while retaining their reviewed major-version annotations.
- systemd upgrades now preflight host requirements, restart the Agent after compatibility validation, and support explicit supervisor, AI, and optimizer disable paths.
- Docker Compose now uses the same graceful `SIGINT` shutdown contract as systemd.
- Resource targets now use deterministic minimum-cost Worker matching with limited intent stickiness instead of preserving a worse assignment indefinitely.
- Scout routes prefer less recently covered chunks and rotate after three consecutive non-improving Ticks.
- The default mature fleet is now `18 Workers + 14 Vanguards + 16 Rangers = 48`,
  built through `6/2/2`, `12/6/8`, and `18/14/16` stages for 240 Core capacity.
- Visible hostile Cores are immediate priority targets, non-guard combat Units
  patrol an expanding perimeter, and a strong healthy force contests the Beacon.
- All production paths use the SDK's current `unit_cost()` preview while preserving operational reserves. Obsolete upkeep diagnostics and alerts are replaced by settled spawn cost, required-price, and repeated affordability-failure telemetry.
- The sweeping armada now splits into `ARMADA_SWEEP_WINGS` wings, each holding
  its own frontier leg at least `ARMADA_WING_SEPARATION` chunks from its
  siblings. Coverage is bounded by how far one fleet can walk, so parallelism is
  the only lever that widens it: measured +33% chunks swept across four starting
  positions. Wings exist only while sweeping empty ground — any hostile collapses
  them back into a single anchor so the fleet never trickles into a fight.

### Fixed

- An unreachable alliance roster no longer pacifies the Agent. `_hostile_enemies()`
  returned an empty tuple whenever the shared roster had never loaded, so with the
  endpoint answering `403` the fleet treated the entire map as friendly: no assault
  target was ever selected, the strike group stayed empty, and the `sweep()`/`shoot()`
  call sites became unreachable. Live history showed `SWEEP=0` and `SHOOT=0` across
  4096 recorded Turns while three enemy Cores sat one cell from our Units. The roster
  only ever adds allies and a client that has succeeded once keeps serving its cached
  snapshot, so an unready roster never protected anyone; local alliance state still
  shields peer accounts by object id, occupied cell, and username.
- The armada no longer freezes in place while its sweep target rotates. The
  median formation anchor was self-locking — the Units defining it were the ones
  ordered to hold station around it — so a stretched fleet stopped advancing
  entirely; live telemetry showed 12 cells of anchor movement across 400 Ticks.
  A stalled advance now enters a bounded `BREAKOUT` that drops formation and
  drives at the target, excluding arrived fleets and `CONTACT`/`SIEGE` postures.
- The armada no longer crashes the Agent mid-Turn. Rallying a Unit that outran
  the fleet centroid read formation offsets that the `SIEGE` and `COLUMN`
  branches never assigned, raising `UnboundLocalError` out of `choose_actions()`
  and terminating the process the moment a sweep entered an obstacle corridor or
  besieged a Core.
- The armada sweep no longer stalls. A sweep leg is committed until the fleet
  reaches the chunk instead of being re-scored every Tick, unreachable chunks are
  abandoned after a bounded window, and the candidate set is seeded from the
  armada anchor so a fleet far from the world origin still has a legal target.

## [0.1.0] - 2026-08-03

### Added

- Cross-platform local bootstrap and launch scripts.
- Docker and Docker Compose deployment with runtime secret mounting.
- Hardened systemd installer with optional supervisor, AI review, and optimizer tiers.
- GitHub CI, community health files, and release documentation.
- Accepted-Turn heartbeat and deterministic unattended health checks for systemd and Compose.
- Deterministic resource-first tactic, structured diagnostics, compatibility monitor, read-only supervisor, and bounded runtime optimizer.
- Tag-driven GHCR release images for build-free Compose deployment.

### Changed

- AI supervisor review now requires explicit `ARENA_SUPERVISOR_AI_ENABLED=true` opt-in.
- Model IDs and model credentials are no longer embedded in systemd units.
- The main systemd service no longer depends on a supervisor refresh timer.
- systemd installation now requires an immediate compatibility check before starting the Agent.
