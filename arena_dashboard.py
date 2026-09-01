from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import math
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from arena_history import (
    cancel_unit_order,
    create_map_indexes,
    create_unit_order,
    delete_expedition,
    list_ticks,
    list_unit_orders,
    read_control_config,
    read_kill_stats,
    read_map_cells_after,
    read_overview,
    save_expedition,
    save_alliance_config,
    save_production_config,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_BASE_URL = "https://api.arenahero.io"
DEFAULT_ALLIANCE_STALE_SECONDS = 60.0
# 全量 overview 要扫描上百万地图格子，必须限制并发，
# 否则请求堆积会把进程内存撑爆（swap 抖动后整个服务挂起）。
OVERVIEW_MAX_CONCURRENCY = 2
# 超过该大小的 JSON 响应启用 gzip（探索数据压缩率约 50%+）
GZIP_MIN_BYTES = 64 * 1024
# 地图底图缓存的图层名 -> 格子表（顺序即前端 MAP_LAYERS 的语义）
MAP_LAYERS: dict[str, str] = {
    "explored": "explored_cells",
    "obstacles": "obstacle_cells",
    "resource_history": "resource_cells",
}
# 底图缓存后台线程的轮询间隔与单批读取行数
MAP_BUILD_POLL_SECONDS = 2.5
MAP_BUILD_BATCH_ROWS = 20000
LEADERBOARD_KEYS = (
    "beacon_ticks_held",
    "damage_dealt",
    "core_destruction_participations",
)


class MapLayerCache:
    """跨主/副库增量维护的地图底图字节缓存。

    百万级格子不能每次请求现查现序列化（实测构建 30~50 秒）。
    守护线程按 rowid 水位线只追新增行，把每行序列化成紧凑 JSON 后追加进
    分批字节数组；/api/map-base 直接吐出拼好的 gzip 字节，零构建延迟。
    版本号在每次有新行追加时递增；客户端版本一致时只需回一个极小响应。

    线上格式（NDJSON）：每行 {"l": 图层名, "r": [[x,y,t1,t2], ...]}；
    obstacles 层沿用 [x,y] 两元行。分批存储让"追加"不需要重写旧字节。
    """

    def __init__(self, history_dbs: tuple[Path, ...]) -> None:
        self._history_dbs = history_dbs
        self._lock = threading.Lock()
        self._version = 0
        self._ready = False
        # 每层一组已序列化的批次字节：b'[[1,2,3,4],...]'（不含外层包装），
        # 并记录各批次归属的版本号，供客户端按 ?version=N 做真增量拉取
        self._batches: dict[str, list[bytes]] = {name: [] for name in MAP_LAYERS}
        self._batch_versions: dict[str, list[int]] = {name: [] for name in MAP_LAYERS}
        self._watermarks: dict[tuple[Path, str], int] = {}

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def batch_fragments(
        self, *, since_version: int | None = None
    ) -> tuple[int, bool, dict[str, list[bytes]]]:
        """取当前版本号、就绪标记与各层批次快照。

        指定 since_version 时只返回该版本之后追加的批次（真增量）；
        版本号只增不减，进程重启后从零开始，此时调用方应整段重发。
        """
        with self._lock:
            selected: dict[str, list[bytes]] = {}
            for name, batches in self._batches.items():
                versions = self._batch_versions[name]
                if since_version is None:
                    selected[name] = list(batches)
                else:
                    selected[name] = [
                        batch
                        for batch, batch_version in zip(batches, versions)
                        if batch_version > since_version
                    ]
            return (
                self._version,
                self._ready,
                selected,
            )

    def build_once(self) -> bool:
        """追一轮所有库的新增格子；首轮调用会循环追赶直到追平。返回是否追加。"""
        appended = False
        with self._lock:
            target_version = self._version + 1
        for db_path in self._history_dbs:
            if not db_path.is_file():
                continue
            for layer, table in MAP_LAYERS.items():
                while True:
                    watermark = self._watermarks.get((db_path, layer), 0)
                    rows = read_map_cells_after(
                        db_path, table, watermark, limit=MAP_BUILD_BATCH_ROWS
                    )
                    if not rows:
                        break
                    self._append_batch(
                        layer, target_version, _serialize_cell_batch(table, rows)
                    )
                    self._watermarks[(db_path, layer)] = int(rows[-1]["rowid"])
                    appended = True
                    if len(rows) < MAP_BUILD_BATCH_ROWS:
                        break
        if not self._ready:
            with self._lock:
                self._ready = True
        if appended:
            with self._lock:
                self._version = target_version
        return appended

    def _append_batch(self, layer: str, version: int, fragment: bytes | None) -> None:
        if fragment is None:
            return
        with self._lock:
            self._batches[layer].append(fragment)
            self._batch_versions[layer].append(version)

    def start(self) -> threading.Thread:
        thread = threading.Thread(
            target=self._run, name="map-cache-builder", daemon=True
        )
        thread.start()
        return thread

    def _run(self) -> None:
        while True:
            try:
                self.build_once()
            except Exception as exc:  # 缓存线程绝不能带崩整个 dashboard
                print(f"map cache build error: {exc!r}", flush=True)
            time.sleep(MAP_BUILD_POLL_SECONDS)


def _serialize_cell_batch(table: str, rows: list) -> bytes | None:
    """把一批格子行序列化成 b'[[x,y,t1,t2],...]'；障碍表保持 [x,y] 两元。"""
    pieces: list[str] = []
    for row in rows:
        if table == "obstacle_cells":
            pieces.append(f"[{row['x']},{row['y']}]")
        else:
            pieces.append(
                f"[{row['x']},{row['y']},{row['first_seen_tick']},"
                f"{row['last_seen_tick']}]"
            )
    if not pieces:
        return None
    return f"[{','.join(pieces)}]".encode("ascii")


def _merge_history_overview(
    overview: dict[str, object],
    allied: dict[str, object],
) -> None:
    if not overview.get("available") or not allied.get("available"):
        return

    for key in ("explored", "resource_history"):
        merged: dict[tuple[int, int], list[int]] = {}
        for item in (*overview.get(key, []), *allied.get(key, [])):
            x, y, first_seen, last_seen = item
            position = int(x), int(y)
            previous = merged.get(position)
            merged[position] = [
                position[0],
                position[1],
                min(int(first_seen), previous[2]) if previous else int(first_seen),
                max(int(last_seen), previous[3]) if previous else int(last_seen),
            ]
        overview[key] = list(merged.values())

    overview["obstacles"] = [
        list(position)
        for position in sorted(
            {
                (int(item[0]), int(item[1]))
                for item in (*overview.get("obstacles", []), *allied.get("obstacles", []))
            }
        )
    ]

    for key, identifier_key in (
        ("enemy_core_history", "core_id"),
        ("enemy_unit_history", "id"),
    ):
        merged_sightings: dict[str, dict[str, object]] = {}
        for item in (*overview.get(key, []), *allied.get(key, [])):
            identifier = str(item[identifier_key])
            previous = merged_sightings.get(identifier)
            if previous is None or int(item["last_seen_tick"]) >= int(
                previous["last_seen_tick"]
            ):
                selected = dict(item)
                selected["currently_visible"] = bool(item.get("currently_visible")) or bool(
                    previous and previous.get("currently_visible")
                )
                merged_sightings[identifier] = selected
            elif item.get("currently_visible"):
                previous["currently_visible"] = True
        overview[key] = list(merged_sightings.values())

    primary_state = overview.get("state")
    allied_state = allied.get("state")
    if isinstance(primary_state, dict) and isinstance(allied_state, dict):
        primary_objects = primary_state.get("objects")
        allied_objects = allied_state.get("objects")
        if isinstance(primary_objects, list) and isinstance(allied_objects, list):
            known_ids = {
                str(item["id"])
                for item in primary_objects
                if isinstance(item, dict) and item.get("id")
            }
            for item in allied_objects:
                if not isinstance(item, dict) or item.get("controlled") is True:
                    continue
                identifier = str(item.get("id", ""))
                if identifier and identifier in known_ids:
                    continue
                if identifier:
                    known_ids.add(identifier)
                primary_objects.append(dict(item))

    primary_trails = overview.get("trails")
    allied_trails = allied.get("trails")
    if isinstance(primary_trails, dict) and isinstance(allied_trails, dict):
        for identifier, trail in allied_trails.items():
            primary_trails.setdefault(identifier, trail)


def _account_summary(
    overview: dict[str, object],
    *,
    role: str,
) -> dict[str, object] | None:
    if not overview.get("available"):
        return None
    state = overview.get("state")
    if not isinstance(state, dict):
        return None
    objects = state.get("objects")
    if not isinstance(objects, list):
        return None
    controlled = [
        item
        for item in objects
        if isinstance(item, dict) and item.get("controlled") is True
    ]
    core = next((item for item in controlled if item.get("kind") == "CORE"), None)
    units = [item for item in controlled if item.get("kind") == "UNIT"]
    core_position = core.get("position") if core is not None else None
    if not (
        isinstance(core_position, list)
        and len(core_position) == 2
        and all(isinstance(value, int) for value in core_position)
    ):
        core_position = None
    return {
        "role": role,
        "username": str(core.get("owner_username", "")) if core is not None else "",
        "tick": int(overview["tick"]),
        "resources": int(state.get("resources", 0)),
        "population": int(state.get("population", 0)),
        "workers": sum(item.get("unit_type") == "WORKER" for item in units),
        "vanguards": sum(item.get("unit_type") == "VANGUARD" for item in units),
        "rangers": sum(item.get("unit_type") == "RANGER" for item in units),
        "core_position": core_position,
    }


def _validated_leaderboard(value: object) -> dict[str, list[dict[str, object]]]:
    if not isinstance(value, dict):
        raise ValueError("leaderboard response must be an object")
    result: dict[str, list[dict[str, object]]] = {}
    for key in LEADERBOARD_KEYS:
        entries = value.get(key)
        if not isinstance(entries, list):
            raise ValueError(f"leaderboard field is invalid: {key}")
        validated = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("rank"), int)
                or isinstance(entry.get("rank"), bool)
                or not isinstance(entry.get("username"), str)
                or not entry["username"].strip()
                or not isinstance(entry.get("score"), int)
                or isinstance(entry.get("score"), bool)
                or entry["rank"] < 1
                or entry["score"] < 0
            ):
                raise ValueError(f"leaderboard entry is invalid: {key}")
            validated.append(
                {
                    "rank": entry["rank"],
                    "username": entry["username"],
                    "score": entry["score"],
                }
            )
        result[key] = validated
    return result


class DashboardApplication:
    def __init__(
        self,
        *,
        history_db: Path,
        static_root: Path,
        base_url: str = DEFAULT_BASE_URL,
        alliance_directory: Path | None = None,
        alliance_account_id: str | None = None,
        alliance_stale_seconds: float = DEFAULT_ALLIANCE_STALE_SECONDS,
        allied_history_dbs: tuple[Path, ...] = (),
    ) -> None:
        self.history_db = history_db
        self.static_root = static_root.resolve()
        self.base_url = base_url.rstrip("/")
        self.alliance_directory = alliance_directory
        self.alliance_account_id = alliance_account_id
        self.alliance_stale_seconds = alliance_stale_seconds
        self.allied_history_dbs = allied_history_dbs
        self._leaderboard_lock = threading.Lock()
        self._leaderboard_at = 0.0
        self._leaderboard: dict[str, list[dict[str, object]]] | None = None
        # overview 是最重的接口，用信号量限制同时在处理的请求数
        self._overview_gate = threading.Semaphore(OVERVIEW_MAX_CONCURRENCY)
        # 账号注册表：把请求里的 account 参数（用户名）路由到对应历史库
        self._account_lock = threading.Lock()
        self._allied_usernames: dict[Path, str] = {}
        self._primary_username = ""
        # 地图底图缓存：后台线程增量构建，/api/map-base 直接吐字节
        self.map_cache = MapLayerCache((history_db, *allied_history_dbs))

    def alliance_objects(self) -> list[dict[str, object]]:
        if self.alliance_directory is None or self.alliance_account_id is None:
            return []
        now = time.time()
        objects: list[dict[str, object]] = []
        try:
            paths = tuple(self.alliance_directory.glob("*.json"))
        except OSError:
            return []
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                account_id = str(value["account_id"])
                updated_at = float(value["updated_at"])
                if (
                    account_id == self.alliance_account_id
                    or not math.isfinite(updated_at)
                    or abs(now - updated_at) > self.alliance_stale_seconds
                ):
                    continue
                username = str(value.get("username", ""))
                core_id = value.get("core_id")
                core_position = value.get("core_position")
                raw_defense = value.get("defense")
                if (
                    isinstance(core_id, str)
                    and isinstance(core_position, list)
                    and len(core_position) == 2
                    and all(isinstance(item, int) for item in core_position)
                ):
                    objects.append(
                        {
                            "kind": "CORE",
                            "id": core_id,
                            "position": core_position,
                            "owner_username": username,
                            "alliance_account_id": account_id,
                            "tick": value.get("tick"),
                            "population": value.get("population"),
                            "defense": raw_defense
                            if isinstance(raw_defense, dict)
                            else None,
                        }
                    )
                units = value.get("units", [])
                if isinstance(units, list):
                    for unit in units:
                        if not isinstance(unit, dict):
                            continue
                        unit_id = unit.get("id")
                        position = unit.get("position")
                        unit_type = unit.get("unit_type")
                        if (
                            not isinstance(unit_id, str)
                            or not isinstance(position, list)
                            or len(position) != 2
                            or not all(isinstance(item, int) for item in position)
                            or unit_type not in {"WORKER", "VANGUARD", "RANGER"}
                        ):
                            continue
                        objects.append(
                            {
                                "kind": "UNIT",
                                "id": unit_id,
                                "position": position,
                                "unit_type": unit_type,
                                "hp": unit.get("hp"),
                                "cargo": unit.get("cargo", 0),
                                "owner_username": username,
                                "alliance_account_id": account_id,
                            }
                        )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return sorted(
            objects,
            key=lambda item: (
                str(item["alliance_account_id"]),
                0 if item["kind"] == "CORE" else 1,
                str(item["id"]),
            ),
        )

    def alliance_identity(self) -> tuple[frozenset[str], frozenset[str]]:
        objects = self.alliance_objects()
        return (
            frozenset(str(item["id"]) for item in objects),
            frozenset(
                str(item.get("owner_username", "")).casefold()
                for item in objects
                if str(item.get("owner_username", "")).strip()
            ),
        )

    def update_alliance_config(
        self,
        *,
        rally_enabled: bool,
        rally_radius: int,
        defense_enabled: bool = True,
    ) -> dict[str, object]:
        result = save_alliance_config(
            self.history_db,
            rally_enabled=rally_enabled,
            rally_radius=rally_radius,
            defense_enabled=defense_enabled,
        )
        for allied_history_db in self.allied_history_dbs:
            save_alliance_config(
                allied_history_db,
                rally_enabled=rally_enabled,
                rally_radius=rally_radius,
                defense_enabled=defense_enabled,
            )
        return result

    def _remember_account(self, db_path: Path, username: str) -> None:
        if not username:
            return
        with self._account_lock:
            self._allied_usernames[db_path] = username

    def resolve_account_db(self, account: object) -> Path:
        """把请求里的 account 参数（用户名）解析为对应历史库；空值或 primary 表示主库。"""
        if account is None:
            return self.history_db
        name = str(account).strip()
        if not name or name.casefold() == "primary":
            return self.history_db
        wanted = {name.casefold()}
        if name.startswith("secondary:"):
            wanted.add(name.split(":", 1)[1].casefold())
        with self._account_lock:
            for db_path, username in self._allied_usernames.items():
                if username.casefold() in wanted:
                    return db_path
        # 缓存未命中（如服务刚重启还没刷过 overview）时直接扫库
        for db_path in self.allied_history_dbs:
            summary = _account_summary(
                read_overview(db_path, include_cell_history=False),
                role="secondary",
            )
            if summary is None:
                continue
            username = str(summary.get("username", ""))
            self._remember_account(db_path, username)
            if username.casefold() in wanted:
                return db_path
        raise KeyError(account)

    def kill_excluded_usernames(self, account_db: Path) -> tuple[str, ...]:
        """读取指定账号的战果时，需要从统计里排除的其他账号用户名。"""
        if account_db is self.history_db:
            _, allied_usernames = self.alliance_identity()
            return tuple(allied_usernames)
        with self._account_lock:
            allied = dict(self._allied_usernames)
        selected_username = allied.get(account_db, "")
        others = {self._primary_username, *allied.values()}
        others.discard(selected_username)
        return tuple(sorted(name for name in others if str(name).strip()))

    def overview(self, **kwargs: object) -> dict[str, object]:
        # 三大地图图层已移交给 /api/map-base 的后台缓存，overview 只带轻量状态
        kwargs["include_cell_history"] = False
        overview = read_overview(self.history_db, **kwargs)
        primary_summary = _account_summary(overview, role="primary")
        accounts = [primary_summary] if primary_summary is not None else []
        if primary_summary is not None:
            self._primary_username = str(primary_summary.get("username", ""))
        for allied_history_db in self.allied_history_dbs:
            allied_overview = read_overview(allied_history_db, **kwargs)
            summary = _account_summary(allied_overview, role="secondary")
            if summary is not None:
                accounts.append(summary)
                self._remember_account(
                    allied_history_db, str(summary.get("username", ""))
                )
            _merge_history_overview(
                overview,
                allied_overview,
            )
        for layer in MAP_LAYERS:
            overview.pop(layer, None)
        overview["map_version"] = self.map_cache.version
        alliance_objects = self.alliance_objects()
        allied_ids = {str(item["id"]) for item in alliance_objects}
        allied_usernames = {
            str(item.get("owner_username", "")).casefold()
            for item in alliance_objects
            if str(item.get("owner_username", "")).strip()
        }

        def is_ally(item: object) -> bool:
            if not isinstance(item, dict):
                return False
            return str(item.get("id", item.get("core_id", ""))) in allied_ids or (
                item.get("kind", "CORE") == "CORE"
                and str(item.get("owner_username", "")).casefold()
                in allied_usernames
            )

        state = overview.get("state")
        objects = state.get("objects", []) if isinstance(state, dict) else []
        if isinstance(objects, list):
            for item in objects:
                if is_ally(item):
                    item["relation"] = "ALLY"
        history = overview.get("enemy_core_history")
        if isinstance(history, list):
            overview["enemy_core_history"] = [
                item for item in history if not is_ally(item)
            ]
        unit_history = overview.get("enemy_unit_history")
        if isinstance(unit_history, list):
            overview["enemy_unit_history"] = [
                item for item in unit_history if not is_ally(item)
            ]
        overview["enemy_count"] = sum(
            isinstance(item, dict)
            and item.get("kind") in {"CORE", "UNIT"}
            and item.get("controlled") is False
            and not is_ally(item)
            for item in objects
        )
        overview["alliance_objects"] = alliance_objects
        overview["accounts"] = accounts
        return overview

    def leaderboard(self) -> dict[str, object]:
        now = time.monotonic()
        with self._leaderboard_lock:
            if self._leaderboard is not None and now - self._leaderboard_at < 15:
                return {"available": True, "stale": False, **self._leaderboard}
            try:
                response = httpx.get(
                    f"{self.base_url}/api/v1/leaderboard",
                    headers={"Accept": "application/json"},
                    timeout=5.0,
                    follow_redirects=False,
                )
                response.raise_for_status()
                self._leaderboard = _validated_leaderboard(response.json())
                self._leaderboard_at = now
                return {"available": True, "stale": False, **self._leaderboard}
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                if self._leaderboard is not None:
                    return {
                        "available": True,
                        "stale": True,
                        "error": type(exc).__name__,
                        **self._leaderboard,
                    }
                return {
                    "available": False,
                    "stale": False,
                    "error": type(exc).__name__,
                }


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def _account_db_or_error(self, account: object):
        """解析 account 参数；未知账号时返回 None 并已发送 400 响应。"""
        try:
            return self.server.app.resolve_account_db(account)
        except KeyError:
            self._send_json(
                {"error": "unknown_account", "account": str(account)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ticks":
            values = parse_qs(parsed.query)
            try:
                limit = int(values.get("limit", ["512"])[0])
            except ValueError:
                self._send_json(
                    {"error": "limit_must_be_an_integer"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"ticks": list_ticks(self.server.app.history_db, limit=limit)})
            return
        if parsed.path == "/api/overview":
            values = parse_qs(parsed.query)
            try:
                tick = int(values["tick"][0]) if "tick" in values else None
                since_tick = (
                    int(values["since_tick"][0]) if "since_tick" in values else None
                )
            except ValueError:
                self._send_json(
                    {"error": "tick_parameters_must_be_integers"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            include_history = values.get("history", ["1"])[0] != "0"
            # 拿不到并发配额时立即返回 503，宁可让前端下个轮询周期重试，
            # 也不能排队堆积（堆积 = 内存膨胀 = 整个服务被 swap 拖死）。
            if not self.server.app._overview_gate.acquire(blocking=False):
                self._send_json(
                    {"error": "overview_busy"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            try:
                overview = self.server.app.overview(
                    tick=tick,
                    since_tick=since_tick,
                    include_history=include_history,
                )
            finally:
                self.server.app._overview_gate.release()
            self._send_json(overview)
            return
        if parsed.path == "/api/map-base":
            values = parse_qs(parsed.query)
            client_version: int | None
            try:
                client_version = (
                    int(values["version"][0]) if "version" in values else None
                )
            except ValueError:
                self._send_json(
                    {"error": "version_must_be_an_integer"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            cache = self.server.app.map_cache
            if not cache.ready:
                # 首轮全量追赶还没完成；前端下个轮询周期再取
                self._send_json({"building": True})
                return
            current = cache.version
            # 带着有效旧版本来的客户端只收增量；不带或版本超前
            # （例如 dashboard 重启过、版本号从零重计）则整段重发，
            # 客户端按坐标去重，重复插入无副作用。
            effective_since = (
                client_version
                if client_version is not None and 0 <= client_version <= current
                else None
            )
            if effective_since == current:
                self._send_json({"unchanged": True, "version": current})
                return
            version, _, fragments = cache.batch_fragments(since_version=effective_since)
            lines = [
                b'{"l":"' + layer.encode("ascii") + b'","r":' + fragment + b"}"
                for layer, batches in fragments.items()
                for fragment in batches
            ]
            self._send_map_base(b"\n".join(lines), version)
            return
        if parsed.path == "/api/leaderboard":
            self._send_json(self.server.app.leaderboard())
            return
        values = parse_qs(parsed.query)
        if parsed.path == "/api/orders":
            db_path = self._account_db_or_error(values.get("account", [None])[0])
            if db_path is None:
                return
            self._send_json(list_unit_orders(db_path))
            return
        if parsed.path == "/api/kills":
            db_path = self._account_db_or_error(values.get("account", [None])[0])
            if db_path is None:
                return
            self._send_json(
                read_kill_stats(
                    db_path,
                    excluded_usernames=self.server.app.kill_excluded_usernames(db_path),
                )
            )
            return
        if parsed.path == "/api/control-config":
            db_path = self._account_db_or_error(values.get("account", [None])[0])
            if db_path is None:
                return
            self._send_json(read_control_config(db_path))
            return
        self._send_static(parsed.path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {
            "/api/orders",
            "/api/control-config",
            "/api/alliance-config",
            "/api/expeditions",
        }:
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError("request body is too large or empty")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            if path == "/api/alliance-config":
                # 联盟靠拢/防御支援是跨账号共享配置，始终同步写入所有库
                result = self.server.app.update_alliance_config(
                    rally_enabled=payload.get("rally_enabled"),
                    rally_radius=payload.get("rally_radius"),
                    defense_enabled=payload.get("defense_enabled", True),
                )
            else:
                db_path = self._account_db_or_error(payload.get("account"))
                if db_path is None:
                    return
                if path == "/api/orders":
                    result = create_unit_order(
                        db_path,
                        unit_type=payload.get("unit_type", ""),
                        unit_count=payload.get("unit_count", 0),
                        unit_ids=payload.get("unit_ids", []),
                        target=(payload.get("target_x"), payload.get("target_y")),
                    )
                elif path == "/api/control-config":
                    result = save_production_config(
                        db_path,
                        worker_weight=payload.get("worker_weight"),
                        vanguard_weight=payload.get("vanguard_weight"),
                        ranger_weight=payload.get("ranger_weight"),
                    )
                else:
                    result = save_expedition(
                        db_path,
                        expedition_id=payload.get("id"),
                        name=payload.get("name", ""),
                        mode=payload.get("mode", "TARGET"),
                        ranger_count=payload.get("ranger_count"),
                        vanguard_count=payload.get("vanguard_count"),
                        target=(payload.get("target_x"), payload.get("target_y")),
                        enabled=payload.get("enabled", True),
                    )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json(
                {"error": "invalid_control_request", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(result, status=HTTPStatus.CREATED)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] not in (["api", "orders"], ["api", "expeditions"]):
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        db_path = self._account_db_or_error(parse_qs(parsed.query).get("account", [None])[0])
        if db_path is None:
            return
        try:
            item_id = int(parts[2])
            if parts[1] == "orders":
                result = cancel_unit_order(db_path, item_id)
            else:
                delete_expedition(db_path, item_id)
                result = {"id": item_id, "deleted": True}
        except (ValueError, TypeError) as exc:
            self._send_json(
                {"error": "invalid_control_request", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(result)

    def _send_json(
        self,
        payload: object,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if len(body) >= GZIP_MIN_BYTES and "gzip" in self.headers.get(
            "Accept-Encoding", ""
        ):
            # 大响应（全量地图历史几十 MB）压缩后传输量减半以上
            body = gzip.compress(body, compresslevel=1)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_map_base(self, body: bytes, version: int) -> None:
        if body and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, compresslevel=1)
            encoding = "gzip"
        else:
            encoding = None
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("X-Map-Version", str(version))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (self.server.app.static_root / relative).resolve()
        if (
            not candidate.is_relative_to(self.server.app.static_root)
            or not candidate.is_file()
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{media_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"dashboard client={self.client_address[0]} {format % args}")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        app: DashboardApplication,
    ) -> None:
        self.app = app
        super().__init__(server_address, DashboardHandler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arena Hero tactical history dashboard.")
    parser.add_argument("--history-db", type=Path, default=Path("arena_history.sqlite3"))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--alliance-directory", type=Path)
    parser.add_argument("--alliance-account-id")
    parser.add_argument(
        "--allied-history-db",
        action="append",
        type=Path,
        default=[],
    )
    parser.add_argument(
        "--alliance-stale-seconds",
        type=float,
        default=DEFAULT_ALLIANCE_STALE_SECONDS,
    )
    parser.add_argument(
        "--static-root",
        type=Path,
        default=Path(__file__).with_name("dashboard"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("dashboard port must be between 1 and 65535")
    if not math.isfinite(args.alliance_stale_seconds) or args.alliance_stale_seconds <= 0:
        raise SystemExit("alliance stale seconds must be finite and positive")
    if (args.alliance_directory is None) != (args.alliance_account_id is None):
        raise SystemExit("alliance directory and account ID must be configured together")
    # farmer 重启前旧库没有 first_seen 索引；这里尽力补建（被占用则跳过）
    for db_path in (args.history_db, *args.allied_history_db):
        create_map_indexes(db_path)
    app = DashboardApplication(
        history_db=args.history_db,
        static_root=args.static_root,
        base_url=args.base_url,
        alliance_directory=args.alliance_directory,
        alliance_account_id=args.alliance_account_id,
        alliance_stale_seconds=args.alliance_stale_seconds,
        allied_history_dbs=tuple(args.allied_history_db),
    )
    if not app.static_root.is_dir():
        raise SystemExit(f"dashboard static directory is missing: {app.static_root}")
    server = DashboardServer((args.host, args.port), app)
    # 后台增量构建地图底图缓存；守护线程随进程退出
    app.map_cache.start()
    print(f"Arena Hero dashboard: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
