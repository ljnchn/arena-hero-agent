from __future__ import annotations

import http.client
import sqlite3
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from arena_hero import Accepted, CommandPlan, PlayerState, Turn

from arena_dashboard import (
    DashboardApplication,
    DashboardServer,
    LEADERBOARD_KEYS,
    OVERVIEW_MAX_CONCURRENCY,
    _validated_leaderboard,
)
from arena_history import (
    HistoryRecorder,
    cancel_unit_order,
    create_map_indexes,
    create_unit_order,
    list_ticks,
    list_unit_orders,
    read_kill_stats,
    read_overview,
    read_control_config,
    save_expedition,
    save_alliance_config,
    save_production_config,
)


CORE_ID = "00000000-0000-4000-8000-000000000001"
WORKER_ID = "00000000-0000-4000-8000-000000000002"
ENEMY_CORE_ID = "10000000-0000-4000-8000-000000000001"
ENEMY_UNIT_ID = "10000000-0000-4000-8000-000000000002"


def make_turn(
    tick: int = 41,
    *,
    core_position: tuple[int, int] = (0, 0),
    enemy_position: tuple[int, int] | None = (4, 0),
    enemy_unit_position: tuple[int, int] | None = None,
    events: list[dict[str, object]] | None = None,
    username: str = "commander",
) -> Turn:
    core_x, core_y = core_position
    objects = [
        {
            "kind": "CORE",
            "id": CORE_ID,
            "controlled": True,
            "owner_username": username,
            "position": [core_x, core_y],
            "hp": 5,
            "shield": 5,
            "state": "NORMAL",
        },
        {
            "kind": "UNIT",
            "id": WORKER_ID,
            "controlled": True,
            "position": [core_x + 1, core_y],
            "hp": 2,
            "unit_type": "WORKER",
            "cargo": 0,
        },
        {"kind": "RESOURCE", "positions": [[core_x + 2, core_y]]},
        {"kind": "OBSTACLE", "positions": [[core_x, core_y + 2]]},
    ]
    if enemy_position is not None:
        objects.append(
            {
                "kind": "CORE",
                "id": ENEMY_CORE_ID,
                "controlled": False,
                "owner_username": "target",
                "position": list(enemy_position),
                "hp": 4,
                "shield": 1,
                "state": "NORMAL",
            }
        )
    if enemy_unit_position is not None:
        objects.append(
            {
                "kind": "UNIT",
                "id": ENEMY_UNIT_ID,
                "controlled": False,
                "position": list(enemy_unit_position),
                "hp": 2,
                "unit_type": "RANGER",
            }
        )
    state = PlayerState.model_validate(
        {
            "status": "ACTIVE",
            "respawn_at_tick": None,
            "resources": 37,
            "population": 1,
            "champion_beacon": {"position": [8, 3]},
            "objects": objects,
            "events": events or [],
        }
    )

    def submitter(plan: CommandPlan, _key: str | None) -> Accepted:
        return Accepted(
            accepted=True,
            tick=plan.tick,
            source="AGENT",
            received_at="2026-08-07T00:00:00Z",
        )

    return Turn(tick=tick, state=state, submitter=submitter)


class HistoryTests(unittest.TestCase):
    def test_unit_orders_are_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            order = create_unit_order(
                path,
                unit_type="worker",
                unit_count=1,
                unit_ids=[WORKER_ID],
                target=(-445, 547),
            )
            self.assertEqual(order["unit_type"], "WORKER")
            self.assertEqual(order["unit_ids"], [WORKER_ID])
            self.assertEqual(list_unit_orders(path)[0]["target_x"], -445)
            with self.assertRaises(ValueError):
                create_unit_order(
                    path,
                    unit_type="WORKER",
                    unit_count=0,
                    unit_ids=[],
                    target=(0, 0),
                )
            with self.assertRaisesRegex(ValueError, "must match"):
                create_unit_order(
                    path,
                    unit_type="WORKER",
                    unit_count=1,
                    unit_ids=[],
                    target=(0, 0),
                )

    def test_core_order_upgrades_existing_table_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_unit_order(
                path,
                unit_type="WORKER",
                unit_count=1,
                unit_ids=[WORKER_ID],
                target=(1, 0),
            )

            order = create_unit_order(
                path,
                unit_type="CORE",
                unit_count=1,
                unit_ids=[CORE_ID],
                target=(10, -5),
            )

            self.assertEqual(order["unit_type"], "CORE")
            self.assertEqual(order["unit_ids"], [CORE_ID])
            self.assertEqual(list_unit_orders(path)[0]["unit_type"], "CORE")
            with self.assertRaisesRegex(ValueError, "exactly one Core"):
                create_unit_order(
                    path,
                    unit_type="CORE",
                    unit_count=2,
                    unit_ids=[CORE_ID, WORKER_ID],
                    target=(0, 0),
                )

    def test_pending_order_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            order = create_unit_order(
                path,
                unit_type="WORKER",
                unit_count=1,
                unit_ids=[WORKER_ID],
                target=(3, 0),
            )
            cancelled = cancel_unit_order(path, int(order["id"]))
            self.assertEqual(cancelled["status"], "CANCELLED")
            with HistoryRecorder(path) as recorder:
                self.assertEqual(recorder.active_orders(), [])

    def test_kill_stats_deduplicate_participation_events(self) -> None:
        events = [
            {
                "event_id": "20000000-0000-4000-8000-000000000001",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "UNIT",
                "position": [3, 0],
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000002",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "CORE",
                "position": [4, 0],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(events=events))
                recorder.record(make_turn(42, enemy_position=None, events=events))
            stats = read_kill_stats(path)
            self.assertEqual(stats["unit_participations"], 1)
            self.assertEqual(stats["core_participations"], 1)
            self.assertEqual(len(stats["recent"]), 2)

    def test_combat_history_records_usernames_losses_and_revenge(self) -> None:
        events = [
            {
                "event_id": "20000000-0000-4000-8000-000000000010",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "CORE",
                "target_id": ENEMY_CORE_ID,
                "position": [4, 0],
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000011",
                "tick": 41,
                "event_type": "UNIT_DAMAGED",
                "reason_code": "ATTACK",
                "target_id": WORKER_ID,
                "position": [1, 0],
                "values": {"damage": 2, "hp": 0},
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000012",
                "tick": 41,
                "event_type": "CORE_DESTROYED",
                "reason_code": "ATTACK",
                "target_id": CORE_ID,
                "position": [0, 0],
                "values": {"destroyed_by": ["rival", "other_rival"]},
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000013",
                "tick": 41,
                "event_type": "UNIT_DAMAGED",
                "reason_code": "ATTACK",
                "target_id": WORKER_ID,
                "position": [1, 0],
                "values": {"damage": 1, "hp": 1},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(events=events))
                self.assertEqual(
                    recorder.revenge_usernames(),
                    frozenset({"rival", "other_rival"}),
                )
            stats = read_kill_stats(path)
            self.assertEqual(stats["recent"][0]["username"], "target")
            self.assertEqual(stats["units_lost"], 1)
            self.assertEqual(stats["cores_lost"], 1)
            self.assertEqual(stats["attacks_received"], 3)
            self.assertEqual(stats["attacks"][0]["outcome"], "DAMAGED")
            self.assertTrue(any(loss["username"] is None for loss in stats["losses"]))
            self.assertEqual(
                stats["revenge_targets"],
                [
                    {"username": "other_rival", "score": 1},
                    {"username": "rival", "score": 1},
                ],
            )

    def test_allies_are_excluded_from_enemy_and_combat_history(self) -> None:
        events = [
            {
                "event_id": "20000000-0000-4000-8000-000000000020",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "CORE",
                "target_id": ENEMY_CORE_ID,
                "position": [4, 0],
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000021",
                "tick": 41,
                "event_type": "CORE_DESTROYED",
                "reason_code": "ATTACK",
                "target_id": CORE_ID,
                "position": [0, 0],
                "values": {"destroyed_by": ["ally"]},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(
                    make_turn(events=events),
                    allied_object_ids=[ENEMY_CORE_ID],
                    allied_usernames=["ally", "target"],
                )

            overview = read_overview(path)
            stats = read_kill_stats(path, excluded_usernames=["ally", "target"])
            self.assertEqual(overview["enemy_core_history"], [])
            self.assertEqual(stats["total_participations"], 0)
            self.assertEqual(stats["attacks_received"], 0)
            self.assertEqual(stats["revenge_targets"], [])

    def test_records_and_reads_tactical_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            turn = make_turn()
            with HistoryRecorder(path) as recorder:
                recorder.record(turn, strategy={"phase": "EXPANSION"})

            ticks = list_ticks(path)
            overview = read_overview(path, tick=41)

            self.assertEqual([item["tick"] for item in ticks], [41])
            self.assertTrue(overview["available"])
            self.assertEqual(overview["strategy"]["phase"], "EXPANSION")
            self.assertIn([2, 0, 41, 41], overview["resource_history"])
            self.assertEqual(
                overview["enemy_core_history"][0]["core_id"],
                ENEMY_CORE_ID,
            )
            self.assertTrue(
                overview["enemy_core_history"][0]["currently_visible"]
            )
            self.assertEqual(overview["enemy_core_history"][0]["age_ticks"], 0)
            self.assertIn(WORKER_ID, overview["trails"])

    def test_enemy_core_history_distinguishes_live_and_last_seen_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41, enemy_position=(4, 0)))
                recorder.record(make_turn(42, enemy_position=None))
                recorder.record(make_turn(43, enemy_position=(5, 0)))

            hidden = read_overview(path, tick=42)["enemy_core_history"][0]
            visible = read_overview(path, tick=43)["enemy_core_history"][0]

            self.assertFalse(hidden["currently_visible"])
            self.assertEqual(hidden["last_seen_tick"], 41)
            self.assertEqual(hidden["age_ticks"], 1)
            self.assertEqual((hidden["x"], hidden["y"]), (4, 0))
            self.assertTrue(visible["currently_visible"])
            self.assertEqual(visible["age_ticks"], 0)
            self.assertEqual((visible["x"], visible["y"]), (5, 0))

    def test_enemy_unit_history_keeps_last_seen_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(
                    make_turn(41, enemy_unit_position=(-390, 578))
                )
                recorder.record(make_turn(42))

            hidden = read_overview(path, tick=42)["enemy_unit_history"][0]

            self.assertFalse(hidden["currently_visible"])
            self.assertEqual(hidden["last_seen_tick"], 41)
            self.assertEqual(hidden["age_ticks"], 1)
            self.assertEqual(hidden["position"], [-390, 578])

    def test_destroyed_enemy_core_is_removed_from_later_history(self) -> None:
        destruction = {
            "event_id": "20000000-0000-4000-8000-000000000030",
            "tick": 42,
            "event_type": "DESTRUCTION_PARTICIPATION",
            "reason_code": "CORE",
            "target_id": ENEMY_CORE_ID,
            "position": [4, 0],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41, enemy_position=(4, 0)))
                recorder.record(
                    make_turn(42, enemy_position=None, events=[destruction])
                )

            self.assertEqual(
                read_overview(path, tick=41)["enemy_core_history"][0]["core_id"],
                ENEMY_CORE_ID,
            )
            self.assertEqual(read_overview(path, tick=42)["enemy_core_history"], [])

    def test_enemy_core_reappearing_after_destruction_is_shown_again(self) -> None:
        destruction = {
            "event_id": "20000000-0000-4000-8000-000000000031",
            "tick": 42,
            "event_type": "DESTRUCTION_PARTICIPATION",
            "reason_code": "CORE",
            "target_id": ENEMY_CORE_ID,
            "position": [4, 0],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41, enemy_position=(4, 0)))
                recorder.record(
                    make_turn(42, enemy_position=None, events=[destruction])
                )
                recorder.record(make_turn(43, enemy_position=(8, 0)))

            history = read_overview(path, tick=43)["enemy_core_history"]
            self.assertEqual(len(history), 1)
            self.assertEqual((history[0]["x"], history[0]["y"]), (8, 0))

    def test_create_map_indexes_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history.sqlite3"
            with HistoryRecorder(history):
                pass
            connection = sqlite3.connect(history)
            try:
                # 模拟 farmer 尚未重启的旧库：先删掉索引
                for table in ("explored_cells", "obstacle_cells", "resource_cells"):
                    connection.execute(f"DROP INDEX IF EXISTS {table}_first_seen_idx")
                connection.commit()
                self.assertTrue(create_map_indexes(history))
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }
            finally:
                connection.close()
            for table in ("explored_cells", "obstacle_cells", "resource_cells"):
                self.assertIn(f"{table}_first_seen_idx", names)
            # 重复执行不报错
            self.assertTrue(create_map_indexes(history))

    def test_overview_can_return_only_new_map_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41))
                recorder.record(make_turn(42))

            delta = read_overview(path, since_tick=41)
            state_only = read_overview(path, include_history=False)

            self.assertTrue(delta["history_delta"])
            self.assertEqual(delta["explored"], [])
            self.assertEqual(delta["obstacles"], [])
            self.assertEqual(delta["resource_history"], [])
            self.assertEqual(state_only["state"]["resources"], 37)
            self.assertEqual(state_only["explored"], [])

    def test_control_config_persists_production_and_expedition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            save_production_config(
                path,
                worker_weight=4,
                vanguard_weight=1,
                ranger_weight=2,
            )
            save_expedition(
                path,
                expedition_id=None,
                name="strike-1",
                ranger_count=2,
                vanguard_count=2,
                target=(12, -8),
                enabled=True,
            )
            save_alliance_config(path, rally_enabled=True, rally_radius=24)

            config = read_control_config(path)

            self.assertEqual(config["production"]["ranger_weight"], 2)
            self.assertTrue(config["alliance"]["rally_enabled"])
            self.assertEqual(config["alliance"]["rally_radius"], 24)
            self.assertEqual(config["expeditions"][0]["name"], "strike-1")
            self.assertEqual(config["expeditions"][0]["mode"], "TARGET")
            self.assertTrue(config["expeditions"][0]["enabled"])

    def test_alliance_perimeter_expedition_mode_persists_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            saved = save_expedition(
                path,
                expedition_id=None,
                name="shared perimeter",
                ranger_count=1,
                vanguard_count=1,
                target=(0, 0),
                enabled=True,
                mode="alliance_perimeter",
            )

            self.assertEqual(saved["mode"], "ALLIANCE_PERIMETER")
            self.assertEqual(
                read_control_config(path)["expeditions"][0]["mode"],
                "ALLIANCE_PERIMETER",
            )
            with self.assertRaisesRegex(ValueError, "mode"):
                save_expedition(
                    path,
                    expedition_id=None,
                    name="bad mode",
                    ranger_count=1,
                    vanguard_count=1,
                    target=(0, 0),
                    enabled=True,
                    mode="wander",
                )

    def test_existing_expeditions_migrate_to_target_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE expeditions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        ranger_count INTEGER NOT NULL,
                        vanguard_count INTEGER NOT NULL,
                        target_x INTEGER NOT NULL,
                        target_y INTEGER NOT NULL,
                        enabled INTEGER NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO expeditions VALUES (1, 'legacy', 1, 2, 3, 4, 1, 0)"
                )

            config = read_control_config(path)

            self.assertEqual(config["expeditions"][0]["mode"], "TARGET")
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(expeditions)")
                }
            self.assertIn("mode", columns)

    def test_alliance_config_defaults_to_twelve_and_validates_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            alliance = read_control_config(path)["alliance"]
            self.assertFalse(alliance["rally_enabled"])
            self.assertEqual(alliance["rally_radius"], 12)
            with self.assertRaisesRegex(ValueError, "between 1 and 256"):
                save_alliance_config(path, rally_enabled=False, rally_radius=0)
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                save_alliance_config(path, rally_enabled=1, rally_radius=12)

    def test_history_limit_removes_old_snapshots_and_core_sightings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path, limit=2) as recorder:
                for tick in (40, 41, 42):
                    recorder.record(make_turn(tick))

            self.assertEqual([item["tick"] for item in list_ticks(path)], [41, 42])
            overview = read_overview(path, tick=40)
            self.assertFalse(overview["available"])


class DashboardTests(unittest.TestCase):
    def test_dual_account_overview_merges_vision_without_changing_primary_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary_db = root / "primary.sqlite3"
            secondary_db = root / "secondary.sqlite3"
            with HistoryRecorder(primary_db) as recorder:
                recorder.record(make_turn(enemy_position=None))
            with HistoryRecorder(secondary_db) as recorder:
                recorder.record(
                    make_turn(
                        core_position=(100, 100),
                        enemy_position=(104, 100),
                    )
                )
            app = DashboardApplication(
                history_db=primary_db,
                static_root=Path(__file__).with_name("dashboard"),
                allied_history_dbs=(secondary_db,),
            )

            overview = app.overview()
            current_ids = {
                item.get("id")
                for item in overview["state"]["objects"]
                if isinstance(item, dict)
            }

            # 三大地图图层已移交 /api/map-base 的后台缓存，overview 只带轻量状态
            for layer in ("explored", "obstacles", "resource_history"):
                self.assertNotIn(layer, overview)
            self.assertIsInstance(overview["map_version"], int)
            self.assertIn(ENEMY_CORE_ID, current_ids)
            self.assertEqual(overview["state"]["population"], 1)
            self.assertEqual(
                overview["accounts"],
                [
                    {
                        "role": "primary",
                        "username": "commander",
                        "tick": 41,
                        "resources": 37,
                        "population": 1,
                        "workers": 1,
                        "vanguards": 0,
                        "rangers": 0,
                        "core_position": [0, 0],
                    },
                    {
                        "role": "secondary",
                        "username": "commander",
                        "tick": 41,
                        "resources": 37,
                        "population": 1,
                        "workers": 1,
                        "vanguards": 0,
                        "rangers": 0,
                        "core_position": [100, 100],
                    },
                ],
            )

    def test_map_cache_serves_allied_cells_via_map_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary_db = root / "primary.sqlite3"
            secondary_db = root / "secondary.sqlite3"
            with HistoryRecorder(primary_db) as recorder:
                recorder.record(make_turn(enemy_position=None))
            with HistoryRecorder(secondary_db) as recorder:
                recorder.record(
                    make_turn(
                        core_position=(100, 100),
                        enemy_position=(104, 100),
                    )
                )
            app = DashboardApplication(
                history_db=primary_db,
                static_root=Path(__file__).with_name("dashboard"),
                allied_history_dbs=(secondary_db,),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                # 首轮构建未跑：报告 building
                connection.request("GET", "/api/map-base")
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read()), {"building": True})
                app.map_cache.build_once()
                # 全量拉取：NDJSON 分批，主/副库探索格子都在
                connection.request("GET", "/api/map-base")
                response = connection.getresponse()
                version = int(response.headers["X-Map-Version"])
                self.assertGreater(version, 0)
                self.assertTrue(
                    response.headers["Content-Type"].startswith("application/x-ndjson")
                )
                layers: dict[str, list[list[int]]] = {}
                for line in response.read().decode("ascii").splitlines():
                    entry = json.loads(line)
                    layers.setdefault(entry["l"], []).extend(entry["r"])
                positions = {(row[0], row[1]) for row in layers["explored"]}
                self.assertIn((0, 0), positions)
                self.assertIn((100, 100), positions)
                self.assertEqual({(row[0], row[1]) for row in layers["obstacles"]},
                                 {(0, 2), (100, 102)})
                self.assertEqual(
                    {(row[0], row[1]) for row in layers["resource_history"]},
                    {(2, 0), (102, 100)},
                )
                explored_rows = [row for row in layers["explored"] if row[0] == 0]
                self.assertTrue(all(len(row) == 4 for row in explored_rows))
                obstacle_rows = [row for row in layers["obstacles"]]
                self.assertTrue(all(len(row) == 2 for row in obstacle_rows))
                # 版本一致时短路
                connection.request("GET", f"/api/map-base?version={version}")
                response = connection.getresponse()
                self.assertTrue(
                    response.headers["Content-Type"].startswith("application/json")
                )
                self.assertEqual(
                    json.loads(response.read()),
                    {"unchanged": True, "version": version},
                )
                # 新格子产生后，带旧版本号只收增量批次
                with HistoryRecorder(primary_db) as recorder:
                    recorder.record(
                        make_turn(tick=42, core_position=(50, 50), enemy_position=None)
                    )
                app.map_cache.build_once()
                connection.request("GET", f"/api/map-base?version={version}")
                response = connection.getresponse()
                self.assertGreater(int(response.headers["X-Map-Version"]), version)
                delta_positions: set[tuple[int, int]] = set()
                for line in response.read().decode("ascii").splitlines():
                    entry = json.loads(line)
                    if entry["l"] == "explored":
                        delta_positions.update((row[0], row[1]) for row in entry["r"])
                self.assertIn((50, 50), delta_positions)
                self.assertNotIn((0, 0), delta_positions)
                self.assertNotIn((100, 100), delta_positions)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_map_cache_appends_only_new_rows_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.sqlite3"
            with HistoryRecorder(history) as recorder:
                recorder.record(make_turn(enemy_position=None))
            app = DashboardApplication(
                history_db=history,
                static_root=Path(__file__).with_name("dashboard"),
            )
            cache = app.map_cache
            cache.build_once()
            version_one = cache.version
            fragments_one = cache.batch_fragments()[2]
            explored_one = fragments_one["explored"]
            # 无新增：版本与字节都不动
            self.assertFalse(cache.build_once())
            self.assertEqual(cache.version, version_one)
            # 新回合探索到新区域：版本递增，且只做纯追加（旧批次字节不变）
            with HistoryRecorder(history) as recorder:
                recorder.record(
                    make_turn(tick=42, core_position=(50, 50), enemy_position=None)
                )
            self.assertTrue(cache.build_once())
            self.assertGreater(cache.version, version_one)
            fragments_two = cache.batch_fragments()[2]
            self.assertEqual(fragments_two["explored"][: len(explored_one)], explored_one)
            positions_two = {
                (row[0], row[1])
                for batch in fragments_two["explored"]
                for row in json.loads(batch)
            }
            self.assertIn((50, 50), positions_two)
            self.assertIn((0, 0), positions_two)

    def test_single_account_overview_has_one_account_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.sqlite3"
            with HistoryRecorder(history) as recorder:
                recorder.record(make_turn())
            app = DashboardApplication(
                history_db=history,
                static_root=Path(__file__).with_name("dashboard"),
            )

            accounts = app.overview()["accounts"]

            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["role"], "primary")
            self.assertEqual(accounts[0]["resources"], 37)
            self.assertEqual(accounts[0]["workers"], 1)

    def test_dashboard_renders_account_status_and_core_location_control(self) -> None:
        dashboard_root = Path(__file__).with_name("dashboard")
        html = (dashboard_root / "index.html").read_text(encoding="utf-8")
        script = (dashboard_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="account-status"', html)
        self.assertIn("function renderAccountStatus(accounts)", script)
        self.assertIn("centerMapAt(card.position)", script)

    def test_dashboard_exposes_alliance_rally_toggle(self) -> None:
        dashboard_root = Path(__file__).with_name("dashboard")
        html = (dashboard_root / "index.html").read_text(encoding="utf-8")
        script = (dashboard_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="alliance-rally-enabled"', html)
        self.assertIn('rally_enabled: document.querySelector', script)

    def test_windows_launcher_uses_lightweight_dashboard_healthcheck(self) -> None:
        launcher = Path(__file__).with_name("start_agent.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('api/overview?history=0', launcher)

    def test_map_target_can_be_hidden_without_reloading(self) -> None:
        script = (
            Path(__file__).with_name("dashboard") / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function clearMapTarget()", script)
        self.assertIn("state.orderTarget = null;", script)
        self.assertIn('"隐藏地图选点"', script)

    def test_dispatch_ui_supports_all_and_core_distance_selection(self) -> None:
        dashboard_root = Path(__file__).with_name("dashboard")
        html = (dashboard_root / "index.html").read_text(encoding="utf-8")
        script = (dashboard_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('value="DISTANT">远离 Core X 格', html)
        self.assertIn('value="ALL">全部', html)
        self.assertIn('id="order-min-distance"', html)
        self.assertIn('coreDistance >= minDistance', script)
        self.assertIn('selectionMode === "ALL"', script)

    def test_alliance_objects_exclude_local_account_and_reject_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alliance = root / "alliance"
            alliance.mkdir()
            peer = {
                "account_id": "account-2",
                "username": "ally",
                "updated_at": time.time(),
                "core_id": ENEMY_CORE_ID,
                "core_position": [7, 8],
                "units": [
                    {
                        "id": WORKER_ID,
                        "position": [6, 8],
                        "unit_type": "WORKER",
                        "hp": 2,
                        "cargo": 1,
                    }
                ],
            }
            peer["defense"] = {
                "under_attack": True,
                "posture": "ENGAGED",
                "threat_level": "ENGAGED",
                "threat_cells": [[6, 9], [8, 9]],
            }
            (alliance / "account-2.json").write_text(json.dumps(peer), encoding="utf-8")
            (alliance / "account-1.json").write_text(
                json.dumps({**peer, "account_id": "account-1"}),
                encoding="utf-8",
            )
            (alliance / "account-3.json").write_text(
                json.dumps({**peer, "account_id": "account-3", "updated_at": 1}),
                encoding="utf-8",
            )
            static = root / "dashboard"
            static.mkdir()
            app = DashboardApplication(
                history_db=root / "history.sqlite3",
                static_root=static,
                alliance_directory=alliance,
                alliance_account_id="account-1",
                alliance_stale_seconds=60,
            )

            objects = app.alliance_objects()

            self.assertEqual([item["kind"] for item in objects], ["CORE", "UNIT"])
            self.assertTrue(all(item["alliance_account_id"] == "account-2" for item in objects))
            self.assertEqual(objects[0]["position"], [7, 8])
            self.assertEqual(objects[0]["defense"]["posture"], "ENGAGED")
            self.assertTrue(objects[0]["defense"]["under_attack"])
            self.assertEqual(objects[0]["population"], peer.get("population"))

    def test_overview_excludes_allies_from_enemy_count_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.sqlite3"
            with HistoryRecorder(history) as recorder:
                recorder.record(make_turn())
            alliance = root / "alliance"
            alliance.mkdir()
            (alliance / "account-2.json").write_text(
                json.dumps(
                    {
                        "account_id": "account-2",
                        "username": "target",
                        "updated_at": time.time(),
                        "core_id": ENEMY_CORE_ID,
                        "core_position": [4, 0],
                        "units": [],
                    }
                ),
                encoding="utf-8",
            )
            static = root / "dashboard"
            static.mkdir()
            app = DashboardApplication(
                history_db=history,
                static_root=static,
                alliance_directory=alliance,
                alliance_account_id="account-1",
            )

            overview = app.overview()

            allied_core = next(
                item
                for item in overview["state"]["objects"]
                if item.get("id") == ENEMY_CORE_ID
            )
            self.assertEqual(allied_core["relation"], "ALLY")
            self.assertEqual(overview["enemy_count"], 0)
            self.assertEqual(overview["enemy_core_history"], [])

    def test_order_endpoint_accepts_coordinate_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            (static / "index.html").write_text("ok", encoding="utf-8")
            app = DashboardApplication(history_db=root / "history.sqlite3", static_root=static)
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                body = json.dumps(
                    {
                        "unit_type": "WORKER",
                        "unit_count": 1,
                        "unit_ids": [WORKER_ID],
                        "target_x": -445,
                        "target_y": 547,
                    }
                )
                connection.request(
                    "POST",
                    "/api/orders",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 201)
                self.assertEqual(payload["target_y"], 547)

                connection.request("DELETE", f"/api/orders/{payload['id']}")
                response = connection.getresponse()
                cancelled = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(cancelled["status"], "CANCELLED")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_orders_route_to_selected_allied_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            primary_db = root / "history.sqlite3"
            secondary_db = root / "secondary.sqlite3"
            with HistoryRecorder(secondary_db) as recorder:
                recorder.record(make_turn(core_position=(100, 100), username="alt"))
            app = DashboardApplication(
                history_db=primary_db,
                static_root=static,
                allied_history_dbs=(secondary_db,),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                body = json.dumps(
                    {
                        "unit_type": "WORKER",
                        "unit_count": 1,
                        "unit_ids": [WORKER_ID],
                        "target_x": 5,
                        "target_y": 6,
                        "account": "alt",
                    }
                )
                connection.request(
                    "POST",
                    "/api/orders",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 201)

                # 订单写入小号库，主号库不受影响
                self.assertEqual(len(list_unit_orders(secondary_db)), 1)
                self.assertEqual(list_unit_orders(primary_db), [])

                # GET 按 account 返回对应账号的订单
                connection.request("GET", "/api/orders?account=alt")
                response = connection.getresponse()
                listed = json.loads(response.read())
                self.assertEqual([item["id"] for item in listed], [payload["id"]])
                connection.request("GET", "/api/orders")
                response = connection.getresponse()
                self.assertEqual(json.loads(response.read()), [])

                # DELETE 按 account 取消小号的订单
                connection.request("DELETE", f"/api/orders/{payload['id']}?account=alt")
                response = connection.getresponse()
                cancelled = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(cancelled["status"], "CANCELLED")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_production_config_routes_to_selected_allied_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            primary_db = root / "history.sqlite3"
            secondary_db = root / "secondary.sqlite3"
            with HistoryRecorder(secondary_db) as recorder:
                recorder.record(make_turn(core_position=(100, 100), username="alt"))
            app = DashboardApplication(
                history_db=primary_db,
                static_root=static,
                allied_history_dbs=(secondary_db,),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/control-config",
                    body=json.dumps(
                        {
                            "worker_weight": 9,
                            "vanguard_weight": 1,
                            "ranger_weight": 1,
                            "account": "alt",
                        }
                    ),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 201)

                self.assertEqual(
                    read_control_config(secondary_db)["production"]["worker_weight"], 9
                )
                self.assertIsNone(read_control_config(primary_db)["production"])

                # GET 同样按账号路由
                connection.request("GET", "/api/control-config?account=alt")
                response = connection.getresponse()
                config = json.loads(response.read())
                self.assertEqual(config["production"]["worker_weight"], 9)

                # 战果端点也接受账号参数（小号视角）
                connection.request("GET", "/api/kills?account=alt")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                kills = json.loads(response.read())
                self.assertIn("available", kills)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_unknown_account_is_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            primary_db = root / "history.sqlite3"
            secondary_db = root / "secondary.sqlite3"
            with HistoryRecorder(secondary_db) as recorder:
                recorder.record(make_turn(core_position=(100, 100), username="alt"))
            app = DashboardApplication(
                history_db=primary_db,
                static_root=static,
                allied_history_dbs=(secondary_db,),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/orders",
                    body=json.dumps(
                        {
                            "unit_type": "WORKER",
                            "unit_count": 1,
                            "unit_ids": [WORKER_ID],
                            "target_x": 5,
                            "target_y": 6,
                            "account": "ghost",
                        }
                    ),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())

                self.assertEqual(response.status, 400)
                self.assertEqual(payload["error"], "unknown_account")
                self.assertEqual(list_unit_orders(primary_db), [])
                self.assertEqual(list_unit_orders(secondary_db), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_alliance_config_endpoint_updates_all_account_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            app = DashboardApplication(
                history_db=root / "history.sqlite3",
                static_root=static,
                allied_history_dbs=(root / "secondary.sqlite3",),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/alliance-config",
                    body=json.dumps({"rally_enabled": True, "rally_radius": 24}),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())

                self.assertEqual(response.status, 201)
                self.assertTrue(payload["rally_enabled"])
                self.assertEqual(payload["rally_radius"], 24)
                for name in ("history.sqlite3", "secondary.sqlite3"):
                    alliance = read_control_config(root / name)["alliance"]
                    self.assertTrue(alliance["rally_enabled"])
                    self.assertEqual(alliance["rally_radius"], 24)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_validates_all_leaderboard_categories(self) -> None:
        payload = {
            key: [{"rank": 1, "username": "commander", "score": 0}]
            for key in LEADERBOARD_KEYS
        }

        self.assertEqual(_validated_leaderboard(payload), payload)
        payload["damage_dealt"][0]["score"] = True
        with self.assertRaisesRegex(ValueError, "damage_dealt"):
            _validated_leaderboard(payload)

    def test_overview_returns_503_when_concurrency_is_saturated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.sqlite3"
            with HistoryRecorder(history) as recorder:
                recorder.record(make_turn())
            app = DashboardApplication(
                history_db=history,
                static_root=Path(__file__).with_name("dashboard"),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            acquired = 0
            try:
                # 占满 overview 并发配额，模拟已有全量请求在处理中
                for _ in range(OVERVIEW_MAX_CONCURRENCY):
                    if app._overview_gate.acquire(blocking=False):
                        acquired += 1
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/api/overview")
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 503)
                self.assertEqual(payload, {"error": "overview_busy"})
            finally:
                for _ in range(acquired):
                    app._overview_gate.release()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_static_handler_rejects_parent_directory_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            (static / "index.html").write_text("ok", encoding="utf-8")
            (root / "secret.txt").write_text("secret", encoding="utf-8")
            app = DashboardApplication(
                history_db=root / "history.sqlite3",
                static_root=static,
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/../secret.txt")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
