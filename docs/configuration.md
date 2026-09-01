# Configuration

## Main Agent

The Agent reads credentials in this order:

1. `ARENA_HERO_API_KEY` in the process environment.
2. The file passed to `--env-file`.
3. `.env` in the current directory.
4. A secure terminal prompt when interactive.

| CLI option | Default | Purpose |
| --- | --- | --- |
| `--base-url` | `https://api.arenahero.io` | Arena Hero HTTP API base. |
| `--env-file` | `.env` | Explicit credential file. |
| `--worker-target` | `18` | Worker goal; accepted range is 1-18. |
| `--beacon-policy` | `pursue` | `hold`, `pursue`, or `retreat`; `pursue` is the aggressive default. |
| `--compatibility-marker` | systemd marker path | Enter compatibility hold while the file exists. |
| `--no-compatibility-marker` | off | Disable marker checks for local/container runs. |
| `--heartbeat-file` | none | Write liveness metadata after each accepted Turn. |
| `--history-db` | `arena_history.sqlite3` | Bounded SQLite history used by the dashboard. |
| `--stale-turn-timeout-seconds` | `0` | Exit transiently after no accepted Turn; `0` disables. |
| `--alliance-directory` | none | Shared local directory for multi-account state; enables alliance coordination. |
| `--alliance-id` | none | Shared alliance name. Required with `--alliance-directory`. |
| `--alliance-account-id` | none | Stable, non-secret identifier unique to this account. |
| `--alliance-expected-members` | `1` | Pause autonomous actions until this many same-Turn members are present. |
| `--alliance-stale-seconds` | `60` | Reject member state older than this many seconds. |
| `--alliance-barrier-timeout-seconds` | `1` | Maximum same-Turn identity synchronization wait before choosing WAIT. |
| `--alliance-roster-url` | none | Authenticated external roster endpoint checked before selecting attack targets. |
| `--alliance-roster-token-file` | none | Protected file containing the external roster bearer token. Required with the roster URL. |
| `--alliance-roster-refresh-seconds` | `15` | Minimum interval between roster requests; the last successful roster remains cached. |
| `--alliance-roster-timeout-seconds` | `5` | Timeout for one roster request. |

`start_agent.sh` enables a 90-second stale-Turn deadline by default. On expiry,
the watchdog first closes the event stream; if the main planning loop does not
unwind within five seconds, it forces transient exit code 75 so the launcher or
process supervisor can restart a CPU-stuck Agent. Direct CLI invocations retain
the opt-in default of `0` for interactive development.

The default unattended force is `18 Workers + 15 Vanguards + 17 Rangers = 50`.
Core capacity therefore reaches 250 resources. Production is staged at
`8/1/1`, `12/3/4`, `18/6/8`, and `18/15/17`; the controller previews each dynamic unit
price with the official SDK and retains a ten-resource Core reserve except for
emergency defense.

Reaching the staged population is not a permanent production stop. When Core
resources reach capacity, the controller first repairs any active force-stage deficit
and then continues balanced Vanguard/Ranger growth, subject to the live SDK
price and a separate 50-resource strategic reserve.

The Core does not migrate for Beacon geometry or routine expansion. Workers
clear the production cell. Core migration remains available to the survival
controller when a threat is close enough to justify the slower four-Tick move.

## Multi-account alliance

Trusted local accounts can coordinate through a shared directory. Each Agent
atomically publishes only its current Tick, population, Core identity and
position, Unit identities, and update time. API keys are never written to the
coordination directory.

Fresh members with the same alliance ID are treated as allies: their Core and
Units are removed from targeting, danger, and pursuit logic. The account with
the largest population is the deterministic rally leader; ties are resolved by
the stable account ID. Healthy follower Cores move toward that leader until
they are within 12 cells. Survival, healing, cargo delivery, and compatibility
hold take priority over alliance movement.

Expeditions support `TARGET` (the default fixed destination) and
`ALLIANCE_PERIMETER` modes. The perimeter mode ignores the stored destination
and fixed composition while active: ordinary target expeditions retain priority,
then the Agents jointly size a rotating outer defense at 25% of combined combat
population. Each account contributes proportionally, fixed Core guards remain
inside, and either account's attack broadcast contracts the shared perimeter for
mutual defense.

For fail-safe operation, set `--alliance-expected-members` to the configured
account count. An Agent chooses WAIT until every member has published identity
for the same Turn. Stale or malformed state is ignored, preventing an unknown
object from being trusted after an account disconnects.

An optional authenticated external roster can extend ally protection beyond
the local coordination group. Core ownership is matched by game username,
Units are matched by object UUID, and known allied positions block attacks on
the same cell. The bearer token must be supplied through a protected file (a
Docker secret in Compose), never a command-line value or image layer. The
roster is refreshed every 15 seconds by default. A failed refresh keeps the
last successful snapshot; if the initial request fails, offensive target
selection stays disabled until a roster has been verified.

## Dashboard

Start the read-only local dashboard with:

```powershell
.\.venv\Scripts\python.exe -m arena_dashboard --history-db .\arena_history.sqlite3
```

It listens on `127.0.0.1:8765` by default. `GET /api/ticks` lists captured Turns,
`GET /api/overview?tick=N` returns the selected historical map, and
`GET /api/leaderboard` proxies the public leaderboard without an API key.

## Runtime Files

| Deployment | History path | Runtime overrides |
| --- | --- | --- |
| Windows | `arena_history.sqlite3` | `-HistoryDb`, `ARENA_HISTORY_DB` |
| POSIX script | `$PROJECT_ROOT/arena_history.sqlite3` | `ARENA_HISTORY_DB` |
| Docker Compose | `/data/history.sqlite3` | `ARENA_DASHBOARD_PORT` for the UI |
| systemd | `/var/lib/arena-hero-agent/history.sqlite3` | `/etc/arena-hero-agent/runtime.env` |

Systemd runtime configuration contains only non-secret values:

```dotenv
ARENA_WORKER_TARGET=18
ARENA_BEACON_POLICY=pursue
ARENA_TUNING_GENERATION=0
```

The generation value is emitted in diagnostics so optimizer changes can be
correlated with logs. Credentials remain in protected environment files or
Docker secrets.

## Optimizer

`arena-hero-optimizer` is disabled by default. When explicitly enabled on a
systemd host, it only evaluates the allow-listed Worker targets `12`, `15`, and
`18`, with `pursue` locked as the Beacon policy. It can restart or roll back the
service, so do not enable it on a host where automatic restarts are not desired.

The supervisor and optional AI review remain deterministic observers; neither
has a plan-submission path.
