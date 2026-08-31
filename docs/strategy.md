# Aggressive Expansion Strategy

The default profile converts resource income into a large combat force, then
uses that force to remove nearby opponents and contest the Champion Beacon.
Arena Hero does not expose a territory-ownership command, so expansion means
exploration, outward patrols, enemy removal, and durable map presence.

## Population Plan

| Stage | Worker | Vanguard | Ranger | Total | Intent |
| --- | ---: | ---: | ---: | ---: | --- |
| Establish | 6 | 2 | 2 | 10 | Keep harvesting while establishing the first attack screen. |
| Mobilize | 12 | 6 | 8 | 26 | Build enough damage to attack nearby Cores continuously. |
| Overwhelm | 18 | 14 | 16 | 48 | Maintain two Core guards and send the rest outward. |

The Core capacity at the final staged population is `max(10, 50 * 5) = 250`.
There is no upkeep in gameplay v0.14. Every spawn branch previews the current
price with the official SDK's `unit_cost()`; the settled spawn event remains
authoritative. Ordinary production keeps a ten-resource Core reserve, while an
immediate threat can spend it on emergency combat units.

Normal production first completes the `18 Workers + 15 Vanguards + 17 Rangers`
staged force. After that point, a Core below resource capacity waits; a full Core
uses otherwise wasted resources to repair any active force-stage deficit and then
continues balanced Vanguard/Ranger growth. Overflow growth still previews the
dynamic price and preserves the ten-resource repair reserve.

## Core And Economy

- A Worker occupying the Core production cell moves away before a spawn. If
  both exits are occupied, the deterministic corridor handoff clears one.
- Resource cells are observations, not permanent terrain. Workers use stable
  one-to-one assignments and return loaded cargo to the Core.
- Production continues during an offensive mission unless survival logic must
  heal, repair, evacuate, or clear an occupied production cell.
- The Core remains stationary for ordinary expansion and Beacon pressure. Only
  verified survival threats start a four-Tick Core migration.

## Combat Priorities

1. Survive a direct Core threat.
2. Attack a visible hostile Core.
3. Attack visible hostile combat units and Workers.
4. Sweep unexplored or stale map chunks with the gathered armada.
5. Contest the Champion Beacon when the force is mature.

Visible enemy Cores are not required to be isolated, stationary, repeatedly
confirmed, or escort-free before becoming a mission target. A remote escort can
change local threat posture, but it does not erase the Core target. Rangers use
legal cell fire; Vanguards sweep adjacent targets or move toward the target.

One Vanguard and one Ranger remain as Core guards. Other combat units join the
strike group. When there is no visible target, non-guard units follow
deterministic outward patrol sectors whose radius grows with elapsed Ticks.

## Armada Sweep

The sweep is the default behaviour of a gathered armada, not a separate mode an
operator has to start. Once at least four non-guard combat units have rallied at
the Core — or the twelve-Tick gather timeout expires — the fleet marches on a
sweep leg without any further prompting.

A leg is one 32x32 map chunk. The scorer ranks candidate chunks by:

1. a remembered enemy Core inside the chunk;
2. whether the chunk was recently abandoned as unreachable;
3. whether a sibling wing already holds a leg within `ARMADA_WING_SEPARATION`;
4. how long ago the chunk was last observed;
5. distance from the armada anchor, then the coordinate itself so the choice
   never depends on set iteration order.

Candidates are the anchor's own chunk neighbourhood plus the frontier around
every chunk in coverage memory, so a fleet that spawns far from the world origin
still has somewhere legal to march.

The chosen chunk is then **committed**. The armada holds that leg until it
actually stands in the chunk, rather than re-scoring every Tick: the march keeps
revealing nearer frontier neighbours, and re-scoring made the fleet swing
sideways instead of arriving. Two escapes keep the commitment from becoming a
deadlock — a newly sighted enemy Core preempts the leg immediately, and a chunk
that stays unreached for `ARMADA_SWEEP_COMMIT_TICKS` is parked in the abandoned
set so the scorer moves on. Abandoned chunks expire after
`ARMADA_SWEEP_ABANDON_TTL` and become eligible again.

`strategy_phase()` reports `ARMADA_SWEEP` while this runs. It outranks the
`MOBILIZE_*` labels because production continues throughout a sweep, and a
gathered fleet marching across the map is the headline plan rather than the
build order. `ASSAULT`, `RECOVERY`, `COMPATIBILITY_HOLD`, and `RESPAWNING` still
take precedence over it.

### Sweep Wings

Sweep coverage is bounded by travel, not by target choice: a fleet moving one
cell per Tick can cross roughly 28 chunk-widths in 900 Ticks, and the selector
already covers about 41 chunks in that time. Widening the candidate horizon or
reordering it into rings was measured and changed nothing — the fleet simply
cannot be in more places. Parallelism is the only lever that works.

The armada therefore splits into `ARMADA_SWEEP_WINGS` wings while it sweeps.
Each wing keeps its own committed chunk, its own centroid, and its own formation
slots, and the scorer penalises any chunk within `ARMADA_WING_SEPARATION` of a
sibling's leg — without that penalty the wings picked adjacent chunks and swept
the same ground. Measured effect: +33% chunks swept across four starting
positions, with no position regressing.

Wings exist only while the armada sweeps empty ground. A visible hostile, a
selected Core target, or a selected Unit target collapses every Unit back into
wing 0, so the fleet concentrates to fight and disperses only to explore.

### Advance Stall Breakout

Committing a chunk guarantees the *target* advances, not the fleet. The
formation anchor is the median of the armada, so the Units that define it are
also the ones ordered to hold station around it. A stretched fleet can freeze
solid: the middle clump holds formation, the median never moves, and the
`proj_ahead` rally drags the leaders back into it. Live telemetry showed an
anchor moving 12 cells in 400 Ticks while the sweep chunk rotated seven times
underneath it.

`_update_armada_advance_progress()` runs once per Tick against the anchor and
target the fleet actually acted on. When the anchor fails to close on the target
for `ARMADA_ADVANCE_STALL_TICKS`, the armada enters `BREAKOUT` for
`ARMADA_BREAKOUT_TICKS`: formation is abandoned and every non-guard combat Unit
drives straight at the target until the anchor closes again. Two cases are
excluded — a fleet within `ARMADA_ADVANCE_ARRIVED_RADIUS` of its target is
supposed to stop closing, and `CONTACT`/`SIEGE` postures keep their formation
because breaking those apart feeds Units into the enemy piecemeal.

## Beacon Campaign

The Beacon campaign starts only when all of these are true:

- population is at least 40;
- resources are at least 30;
- the Core has full HP and shield;
- there is no current Core attack or threatening enemy;
- more than one Vanguard is available so the guard layer remains intact.

The selected Vanguard travels to a ground Beacon, picks it up, and returns to
the Core. The Core itself never moves just to pursue or retreat from a Beacon.

## Safety And Recovery

The shared alliance roster is an *additive* ally source: it can only add names
and object ids on top of the local alliance state. A roster client that has
succeeded once keeps serving its cached snapshot, so a roster that is "not
ready" is one that never loaded at all and therefore never protected anybody.
Hostility must not be gated on it — doing so pacified the Agent completely while
the endpoint answered `403`. Peer accounts stay protected by object id, occupied
cell, and username from the local coordinator.

Lifecycle, threat, and mission layers remain independent. `RESPAWNING` queues
no invented actions, `COMPATIBILITY_HOLD` stops offensive production, and
`RECOVERY` rebuilds locally after a replacement Core. A hard survival threat
still overrides the aggressive mission plan and starts the existing multi-axis
evasion logic.

Every accepted Turn can be written to SQLite. The dashboard uses this history
to replay explored cells, resources, unit trails, events, and historical enemy
Core sightings without exposing credentials.

`CoreFarmer.strategy_summary()` is the single aggregate every reporting surface
reads: the systemd `STATUS=` line, the SQLite `strategy` record, and the
dashboard overview. Alongside the phase, posture, threat, and mission targets it
carries the sweep state — `sweeping`, `armada_mode`, `armada_gathered`,
`armada_target`, `armada_anchor`, `sweep_chunk`, `sweep_committed_tick`, and
`sweep_abandoned_chunks` — so a stalled sweep is visible without reading the
Turn diagnostics line.
