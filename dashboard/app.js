"use strict";

const canvas = document.querySelector("#map");
const context = canvas.getContext("2d", { alpha: false });
const MAP_CHUNK_SIZE = 32;
const MAP_LAYERS = ["explored", "obstacles", "resource_history"];
const ACCOUNT_STALE_TICKS = 3;
const ui = {
  accountStatus: document.querySelector("#account-status"),
  tick: document.querySelector("#status-tick"),
  resources: document.querySelector("#status-resources"),
  population: document.querySelector("#status-population"),
  force: document.querySelector("#status-force"),
  posture: document.querySelector("#status-posture"),
  enemies: document.querySelector("#status-enemies"),
  status: document.querySelector("#map-status"),
  slider: document.querySelector("#tick-slider"),
  play: document.querySelector("#toggle-play"),
  live: document.querySelector("#live-tick"),
  events: document.querySelector("#event-list"),
  rankings: document.querySelector("#ranking-list"),
  killHeading: document.querySelector("#kill-heading"),
  killStats: document.querySelector("#kill-stats"),
  kills: document.querySelector("#kill-list"),
  losses: document.querySelector("#loss-list"),
  revenge: document.querySelector("#revenge-list"),
  orders: document.querySelector("#order-list"),
  orderUnitList: document.querySelector("#order-unit-list"),
  orderForm: document.querySelector("#order-form"),
  orderStatus: document.querySelector("#order-status"),
  orderSelectionMode: document.querySelector("#order-selection-mode"),
  orderDistanceField: document.querySelector("#order-distance-field"),
  orderMinDistance: document.querySelector("#order-min-distance"),
  pickTarget: document.querySelector("#pick-order-target"),
  orderX: document.querySelector("#order-x"),
  orderY: document.querySelector("#order-y"),
  cursorPosition: document.querySelector("#cursor-position"),
  hoverTooltip: document.querySelector("#hover-tooltip"),
  productionForm: document.querySelector("#production-form"),
  productionStatus: document.querySelector("#production-status"),
  panelToggle: document.querySelector("#panel-toggle"),
  mapZoomIn: document.querySelector("#map-zoom-in"),
  mapZoomOut: document.querySelector("#map-zoom-out"),
  mapZoomHome: document.querySelector("#map-zoom-home"),
  unitsPanel: document.querySelector("#units-panel"),
  unitsPanelContainer: document.querySelector("#units-panel-container"),
  unitsPanelToggle: document.querySelector("#units-panel-toggle"),
  unitList: document.querySelector("#unit-list"),
  allianceForm: document.querySelector("#alliance-form"),
  allianceStatus: document.querySelector("#alliance-status"),
  expeditionForm: document.querySelector("#expedition-form"),
  expeditionStatus: document.querySelector("#expedition-status"),
  expeditionList: document.querySelector("#expedition-list"),
  pickExpeditionTarget: document.querySelector("#pick-expedition-target"),
  controlAccountChip: document.querySelector("#control-account-chip"),
  productionAccountChip: document.querySelector("#production-account-chip"),
};

const colors = {
  background: "#090c0f",
  grid: "#141a1f",
  explored: "#1d252c",
  obstacle: "#53616c",
  resource: "#40cc87",
  resourceHistory: "#b88f24",
  friendly: "#3db8e3",
  ally: "#b8a1ff",
  enemy: "#ee6268",
  oldCore: "#873f44",
  beacon: "#f0c84c",
  label: "#e6edf1",
};

const state = {
  ticks: [],
  selectedIndex: -1,
  overview: null,
  leaderboard: null,
  kills: null,
  orders: [],
  controlUnits: [],
  selectedAccount: null, // null = 大号；否则为小号用户名
  rankingKey: "damage_dealt",
  live: true,
  playing: false,
  playTimer: null,
  centered: false,
  view: { x: 0, y: 0, scale: 9 },
  dragging: false,
  pointer: null,
  pointerStart: null,
  pickingTarget: false,
  orderTarget: null,
  controlConfig: { production: null, alliance: { rally_radius: 12 }, expeditions: [] },
  layers: { explored: true, obstacles: true, resources: true, history: true, routes: false },
  pickMode: null,
  viewport: { width: 1, height: 1 },
  mapIndex: Object.fromEntries(MAP_LAYERS.map((name) => [name, new Map()])),
  // 底图增量状态：cellKeys 按坐标去重（主/副库拼接会有重复行），
  // mapVersion 对齐服务端缓存版本；viewTick 非空时绘制按 first_seen 过滤
  cellKeys: Object.fromEntries(MAP_LAYERS.map((name) => [name, new Set()])),
  mapVersion: null,
  mapLoading: false,
  exploredCount: 0,
  viewTick: null,
  unitFilter: "ALL",
  useRelativeCoords: false,
  panelVisible: true,
  unitsPanelVisible: true,
  legendVisible: true,
  flashTarget: null,
  pinch: null,
  accountCards: new Map(),
  knownAccounts: new Map(),
};
// 获取己方 Core 的绝对坐标
function getCorePosition() {
  const core = controlledCore();
  return core?.position || null;
}

// 绝对坐标 -> 相对坐标
function toRelativePos(worldPos) {
  const corePos = getCorePosition();
  if (!corePos) return worldPos;
  return [worldPos[0] - corePos[0], worldPos[1] - corePos[1]];
}

// 相对坐标 -> 绝对坐标 (发送给后台用)
function toAbsolutePos(relPos) {
  const corePos = getCorePosition();
  if (!corePos) return relPos;
  return [corePos[0] + relPos[0], corePos[1] + relPos[1]];
}

// 格式化坐标文本显示
function formatCoordDisplay(worldPos) {
  const [wx, wy] = worldPos;
  const rel = toRelativePos(worldPos);
  
  if (state.useRelativeCoords && rel) {
    const rx = rel[0] >= 0 ? `+${rel[0]}` : rel[0];
    const ry = rel[1] >= 0 ? `+${rel[1]}` : rel[1];
    return `Δ x ${rx} · y ${ry} (绝对: ${wx}, ${wy})`;
  }
  return `x ${wx} · y ${wy}`;
}

let drawFrame = 0;
let flashFrame = 0;
//悬停计时相关变量
let hoverTimer = null;
let currentHoverCell = null;
const HOVER_DELAY = 1000; // 悬停触发延迟（单位：毫秒，可根据需求调整）

function clearHover() {
  if (hoverTimer) {
    clearTimeout(hoverTimer);
    hoverTimer = null;
  }
  currentHoverCell = null;
  if (ui.hoverTooltip) {
    ui.hoverTooltip.classList.add("hidden");
  }
}

function showHoverTooltip(x, y) {
  if (!ui.hoverTooltip) return;
  // 计算方格在 Canvas 上的屏幕坐标
  const [sx, sy] = screenPosition([x, y]);
  
  // 🌟 支持悬停提示框显示相对坐标
  const rel = toRelativePos([x, y]);
  if (state.useRelativeCoords && rel) {
    const rx = rel[0] >= 0 ? `+${rel[0]}` : rel[0];
    const ry = rel[1] >= 0 ? `+${rel[1]}` : rel[1];
    ui.hoverTooltip.textContent = `相对 Core: (${rx}, ${ry})`;
  } else {
    ui.hoverTooltip.textContent = `坐标: (${x}, ${y})`;
  }
  ui.hoverTooltip.style.left = `${sx}px`;
  ui.hoverTooltip.style.top = `${sy}px`;
  ui.hoverTooltip.classList.remove("hidden");
}

function shouldDrawObject(item) {
  // 核心 CORE 永远显示
  if (item.kind === "CORE") return true;
  // 全部显示模式
  if (state.unitFilter === "ALL") return true;
  // 按指定兵种过滤
  return item.kind === "UNIT" && item.unit_type === state.unitFilter;
}
function selectUnitInForm(unit, isMultiSelect = false) {
  const typeSelect = document.querySelector("#order-unit-type");
  const unitType = unit.kind === "CORE" ? "CORE" : unit.unit_type;

  // 如果点击了不同兵种，自动切换列表
  if (typeSelect.value !== unitType) {
    typeSelect.value = unitType;
    renderUnitPicker();
  }

  const checkbox = ui.orderUnitList.querySelector(`input[value="${unit.id}"]`);

  if (isMultiSelect) {
    // 多选模式：累加当前单位的勾选状态
    if (checkbox) {
      checkbox.checked = !checkbox.checked;
    }
  } else {
    // 单选模式：取消其他勾选，只保留当前这 1 个单位
    ui.orderUnitList.querySelectorAll("input:checked").forEach((cb) => {
      if (cb !== checkbox) cb.checked = false;
    });
    if (checkbox) checkbox.checked = true;
  }

  // 统计已选中的单位总数
  const checkedBoxes = ui.orderUnitList.querySelectorAll("input:checked");
  document.querySelector("#order-count").value = checkedBoxes.length;

  setPanel("control");
  setTargetPicking(true, "order");
  
  const orderForm = document.querySelector("#order-form");
  if (orderForm) {
    orderForm.classList.remove("section-collapsed");
  }

  if (checkedBoxes.length > 0) {
    ui.orderStatus.textContent = `已选中 ${checkedBoxes.length} 个 ${unitType}（按住 Ctrl 可继续多选），请点击地图标记目的地`;
  } else {
    ui.orderStatus.textContent = "未选中任何单位，请点击选择单位";
  }
}
function drawSelectedUnitsHighlight() {
  if (!state.pickingTarget) return;
  const checkedIds = new Set(
    [...ui.orderUnitList.querySelectorAll("input:checked")].map((input) => input.value)
  );
  if (!checkedIds.size) return;

  const objects = state.overview?.state?.objects || [];
  context.save();
  context.strokeStyle = colors.beacon; // 黄金色高亮
  context.lineWidth = 2;
  context.setLineDash([4, 4]);

  for (const item of objects) {
    if (checkedIds.has(item.id) && item.position) {
      const [x, y] = screenPosition(item.position);
      const radius = Math.max(8, state.view.scale * 0.6);
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.stroke();
    }
  }
  context.restore();
}
function getPixelRatio() {
  const dpr = window.devicePixelRatio || 1;
  // 移动端高 DPI 设备限制为 2，降低 GPU/内存开销
  if (window.innerWidth <= 760) return Math.min(dpr, 2);
  return Math.max(1, dpr);
}

function resizeCanvas() {
  const ratio = getPixelRatio();
  const rect = canvas.getBoundingClientRect();
  state.viewport = { width: rect.width, height: rect.height };
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}

function screenPosition(position) {
  return [
    state.viewport.width / 2 + (position[0] - state.view.x) * state.view.scale,
    state.viewport.height / 2 + (position[1] - state.view.y) * state.view.scale,
  ];
}

function worldPosition(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  return [
    Math.round(state.view.x + (clientX - rect.left - rect.width / 2) / state.view.scale),
    Math.round(state.view.y + (clientY - rect.top - rect.height / 2) / state.view.scale),
  ];
}

function visibleAt(position) {
  const [x, y] = screenPosition(position);
  const margin = state.view.scale * 2;
  return x >= -margin && y >= -margin
    && x <= state.viewport.width + margin && y <= state.viewport.height + margin;
}

function indexCells(name, cells, reset = false) {
  const index = state.mapIndex[name];
  const keys = state.cellKeys[name];
  if (reset) { index.clear(); keys.clear(); }
  cells.forEach((cell) => {
    // 坐标去重：同一格子可能同时来自主/副库（或增量批次重叠）
    const packed = ((cell[0] + 32768) << 16) | (cell[1] + 32768);
    if (keys.has(packed)) return;
    keys.add(packed);
    if (name === "explored") state.exploredCount += 1;
    const key = `${Math.floor(cell[0] / MAP_CHUNK_SIZE)},${Math.floor(cell[1] / MAP_CHUNK_SIZE)}`;
    if (!index.has(key)) index.set(key, []);
    index.get(key).push(cell);
  });
}

function drawIndexedCells(name, color, size = 1) {
  const halfWidth = state.viewport.width / state.view.scale / 2 + 2;
  const halfHeight = state.viewport.height / state.view.scale / 2 + 2;
  const left = Math.floor((state.view.x - halfWidth) / MAP_CHUNK_SIZE);
  const right = Math.floor((state.view.x + halfWidth) / MAP_CHUNK_SIZE);
  const top = Math.floor((state.view.y - halfHeight) / MAP_CHUNK_SIZE);
  const bottom = Math.floor((state.view.y + halfHeight) / MAP_CHUNK_SIZE);
  for (let chunkX = left; chunkX <= right; chunkX += 1) {
    for (let chunkY = top; chunkY <= bottom; chunkY += 1) {
      (state.mapIndex[name].get(`${chunkX},${chunkY}`) || [])
        .forEach((cell) => drawCell(cell, color, size));
    }
  }
}

function scheduleDraw() {
  if (drawFrame || flashFrame) return;
  drawFrame = requestAnimationFrame(() => {
    drawFrame = 0;
    draw();
  });
}

function drawCell(position, color, size = 1) {
  // 回放时按发现时间本地过滤（obstacles 行无时间戳，视为始终可见）
  if (state.viewTick != null && position.length > 2 && position[2] > state.viewTick) return;
  if (!visibleAt(position)) return;
  const [x, y] = screenPosition(position);
  const cell = Math.max(1, state.view.scale * size);
  context.fillStyle = color;
  context.fillRect(x - cell / 2, y - cell / 2, cell, cell);
}

function drawGrid() {
  if (state.view.scale < 7) return;
  const rect = state.viewport;
  const left = Math.floor(state.view.x - rect.width / state.view.scale / 2);
  const right = Math.ceil(state.view.x + rect.width / state.view.scale / 2);
  const top = Math.floor(state.view.y - rect.height / state.view.scale / 2);
  const bottom = Math.ceil(state.view.y + rect.height / state.view.scale / 2);
  context.strokeStyle = colors.grid;
  context.lineWidth = 1;
  context.beginPath();
  for (let x = left; x <= right; x += 1) {
    const [sx] = screenPosition([x, 0]);
    context.moveTo(Math.round(sx) + 0.5, 0);
    context.lineTo(Math.round(sx) + 0.5, rect.height);
  }
  for (let y = top; y <= bottom; y += 1) {
    const [, sy] = screenPosition([0, y]);
    context.moveTo(0, Math.round(sy) + 0.5);
    context.lineTo(rect.width, Math.round(sy) + 0.5);
  }
  context.stroke();
}

function drawAxes() {
  const [originX, originY] = screenPosition([0, 0]);
  if (
    originX < -state.viewport.width || originX > state.viewport.width * 2 ||
    originY < -state.viewport.height || originY > state.viewport.height * 2
  ) {
    return;
  }
  context.save();
  context.strokeStyle = "rgba(80, 110, 125, 0.65)";
  context.lineWidth = 1.5;
  context.beginPath();
  context.moveTo(0, originY);
  context.lineTo(state.viewport.width, originY);
  context.moveTo(originX, 0);
  context.lineTo(originX, state.viewport.height);
  context.stroke();
  context.restore();
}

function drawTrail(points) {
  if (!Array.isArray(points) || points.length < 2) return;
  context.strokeStyle = "rgba(61,184,227,0.24)";
  context.lineWidth = 1.2;
  context.beginPath();
  points.forEach((position, index) => {
    const [x, y] = screenPosition(position);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function drawCore(item, relation, historical = false) {
  const [x, y] = screenPosition(item.position || [item.x, item.y]);
  const size = Math.max(7, state.view.scale * 0.82);
  context.save();
  context.globalAlpha = historical ? 0.52 : 1;
  context.setLineDash(historical ? [4, 3] : []);
  const friendly = relation === "friendly";
  const allied = relation === "ally";
  context.fillStyle = historical ? colors.oldCore : allied ? colors.ally : friendly ? colors.friendly : colors.enemy;
  context.fillRect(x - size / 2, y - size / 2, size, size);
  context.strokeStyle = historical ? "#cb7178" : colors.label;
  context.lineWidth = historical ? 1 : 1.5;
  context.strokeRect(x - size / 2, y - size / 2, size, size);
  if ((!friendly || allied) && state.view.scale >= 5) {
    const name = item.owner_username ? `@${item.owner_username}` : allied ? "盟友 Core" : "敌方 Core";
    const age = historical ? ` · 最后发现 ${item.age_ticks}T 前` : "";
    context.fillStyle = historical ? "#c78388" : allied ? "#ffd7df" : "#ff9b9f";
    context.font = "11px Segoe UI, Microsoft YaHei, sans-serif";
    context.fillText(`${name}${age}`, x + size / 2 + 5, y + 4);
  }
  context.restore();
}

function drawUnit(item, relation, historical = false) {
  const [x, y] = screenPosition(item.position);
  const radius = Math.max(2.5, state.view.scale * 0.28);
  const friendly = relation === "friendly";
  const allied = relation === "ally";
  context.save();
  context.globalAlpha = historical ? 0.42 : 1;
  context.setLineDash(historical ? [3, 2] : []);
  context.fillStyle = allied ? colors.ally : friendly ? colors.friendly : colors.enemy;
  context.strokeStyle = allied ? "#ffe0e7" : friendly ? "#a8e8ff" : "#ffb0b3";
  context.lineWidth = 1;
  context.beginPath();
  if (item.unit_type === "VANGUARD") {
    context.moveTo(x, y - radius * 1.45);
    context.lineTo(x + radius * 1.45, y);
    context.lineTo(x, y + radius * 1.45);
    context.lineTo(x - radius * 1.45, y);
    context.closePath();
  } else if (item.unit_type === "RANGER") {
    // 游侠 RANGER：向上箭头三角形（远程/弓箭）
    const r = radius * 1.35;
    context.moveTo(x, y - r * 0.95);          // 顶尖箭头
    context.lineTo(x + r * 0.82, y + r * 0.7); // 右下角
    context.lineTo(x - r * 0.82, y + r * 0.7); // 左下角
    context.closePath();
  } else {
    context.arc(x, y, radius, 0, Math.PI * 2);
  }
  context.fill();
  context.stroke();

  // 携带资源的工兵添加绿色小角标
  if (item.unit_type === "WORKER" && item.cargo > 0) {
    const badgeRadius = Math.max(1.8, radius * 0.45);
    const badgeX = x + radius * 0.7;
    const badgeY = y - radius * 0.7;
    context.beginPath();
    context.arc(badgeX, badgeY, badgeRadius, 0, Math.PI * 2);
    context.fillStyle = colors.resource;
    context.fill();
    context.strokeStyle = colors.background;
    context.lineWidth = 0.8;
    context.stroke();
  }
  context.restore();
}

function drawPlan(overview, objectById) {
  const deltas = { UP: [0, -1], DOWN: [0, 1], LEFT: [-1, 0], RIGHT: [1, 0] };
  const actions = overview.plan?.unit_actions || {};
  context.strokeStyle = "rgba(240,200,76,0.7)";
  context.lineWidth = 1.5;
  for (const [id, action] of Object.entries(actions)) {
    if (action.type !== "MOVE" || !deltas[action.direction]) continue;
    const object = objectById.get(id);
    if (!object) continue;
    const destination = [object.position[0] + deltas[action.direction][0], object.position[1] + deltas[action.direction][1]];
    const [x1, y1] = screenPosition(object.position);
    const [x2, y2] = screenPosition(destination);
    context.beginPath();
    context.moveTo(x1, y1);
    context.lineTo(x2, y2);
    context.stroke();
  }
}

function drawOrderTarget() {
  if (!state.orderTarget || !visibleAt(state.orderTarget)) return;
  const [x, y] = screenPosition(state.orderTarget);
  const size = Math.max(7, state.view.scale * 0.55);
  context.save();
  context.strokeStyle = colors.beacon;
  context.fillStyle = colors.beacon;
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(x - size, y);
  context.lineTo(x + size, y);
  context.moveTo(x, y - size);
  context.lineTo(x, y + size);
  context.stroke();
  context.font = "11px Segoe UI, Microsoft YaHei, sans-serif";
  context.fillText(`${state.orderTarget[0]}, ${state.orderTarget[1]}`, x + size + 4, y - 4);
  context.restore();
}

function drawFlashTarget() {
  if (!state.flashTarget || state.flashTarget.until < Date.now()) return;
  const position = state.flashTarget.position;
  if (!visibleAt(position)) return;
  const [x, y] = screenPosition(position);
  const size = Math.max(10, state.view.scale * 0.75);
  const elapsed = state.flashTarget.until - Date.now();
  const alpha = Math.min(1, elapsed / 300);
  context.save();
  context.strokeStyle = `rgba(240, 200, 76, ${alpha})`;
  context.lineWidth = 2;
  context.setLineDash([4, 4]);
  context.beginPath();
  context.arc(x, y, size, 0, Math.PI * 2);
  context.stroke();
  context.restore();
}

function coordLink(x, y, label = null) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "coord-link";
  button.textContent = label || `${x},${y}`;
  button.dataset.coordX = x;
  button.dataset.coordY = y;
  button.title = `定位到 (${x}, ${y})`;
  return button;
}

function locatePosition(position, targetScale = null) {
  state.view.x = position[0];
  state.view.y = position[1];
  if (targetScale != null) {
    state.view.scale = Math.max(1.5, Math.min(32, targetScale));
  }
  state.centered = false;
  state.flashTarget = { position: [...position], until: Date.now() + 1200 };
  updateMetrics();
  draw();
}

function handleCoordClick(event) {
  const link = event.target.closest(".coord-link");
  if (!link) return;
  const x = Number(link.dataset.coordX);
  const y = Number(link.dataset.coordY);
  if (Number.isSafeInteger(x) && Number.isSafeInteger(y)) {
    locatePosition([x, y], Math.max(state.view.scale, 8));
  }
}

function hasVisibleEnemies() {
  const objects = state.overview?.state?.objects || [];
  const allianceIds = new Set(state.overview?.alliance_objects?.map((item) => item.id) || []);
  return objects.some((item) =>
    item.controlled === false &&
    !allianceIds.has(item.id) &&
    item.relation !== "ALLY" &&
    shouldDrawObject(item) &&
    item.position,
  );
}

function drawEnemyFlash() {
  const objects = state.overview?.state?.objects || [];
  const allianceIds = new Set(state.overview?.alliance_objects?.map((item) => item.id) || []);
  const now = Date.now();
  const pulse = (Math.sin(now / 220) + 1) / 2;

  const enemies = objects.filter((item) =>
    item.controlled === false &&
    !allianceIds.has(item.id) &&
    item.relation !== "ALLY" &&
    shouldDrawObject(item) &&
    item.position,
  );
  if (!enemies.length) return;

  context.save();
  for (const item of enemies) {
    const [x, y] = screenPosition(item.position);
    const isCore = item.kind === "CORE";
    const baseSize = isCore
      ? Math.max(12, state.view.scale * 0.95)
      : Math.max(8, state.view.scale * 0.42);
    const radius = baseSize + pulse * (isCore ? 5 : 3);
    const alpha = 0.25 + pulse * 0.55;

    context.strokeStyle = `rgba(238, 98, 104, ${alpha})`;
    context.lineWidth = isCore ? 2.5 : 1.5;
    context.beginPath();
    if (isCore) {
      const half = radius;
      context.rect(x - half, y - half, half * 2, half * 2);
    } else {
      context.arc(x, y, radius, 0, Math.PI * 2);
    }
    context.stroke();
  }
  context.restore();
}

function startEnemyFlashLoop() {
  if (flashFrame) return;
  flashFrame = requestAnimationFrame(function loop() {
    if (!hasVisibleEnemies()) {
      flashFrame = 0;
      return;
    }
    draw();
    flashFrame = requestAnimationFrame(loop);
  });
}

function drawStar(cx, cy, spikes, outerRadius, innerRadius) {
  let rot = Math.PI / 2 * 3;
  let x = cx;
  let y = cy;
  const step = Math.PI / spikes;

  context.beginPath();
  context.moveTo(cx, cy - outerRadius);
  for (let i = 0; i < spikes; i++) {
    x = cx + Math.cos(rot) * outerRadius;
    y = cy + Math.sin(rot) * outerRadius;
    context.lineTo(x, y);
    rot += step;

    x = cx + Math.cos(rot) * innerRadius;
    y = cy + Math.sin(rot) * innerRadius;
    context.lineTo(x, y);
    rot += step;
  }
  context.lineTo(cx, cy - outerRadius);
  context.closePath();
}

function drawRoutes(overview) {
  if (!state.layers.routes) return;
  const units = new Map(
    (overview.state.objects || [])
      .filter((item) => item.kind === "UNIT" && item.controlled)
      .map((item) => [item.id, item]),
  );
  const routes = [];
  (state.orders || []).filter((item) => item.status === "PENDING").forEach((order) => {
    order.unit_ids.forEach((id) => routes.push([id, [order.target_x, order.target_y]]));
  });
  (state.controlConfig.expeditions || []).filter(
    (item) => item.enabled && item.mode !== "ALLIANCE_PERIMETER"
  ).forEach((expedition) => {
    const memberIds = overview.strategy.expedition_members?.[String(expedition.id)] || [];
    memberIds.forEach((id) => routes.push([id, [expedition.target_x, expedition.target_y]]));
  });
  context.save();
  context.strokeStyle = "rgba(240,200,76,0.48)";
  context.setLineDash([5, 5]);
  routes.forEach(([id, target]) => {
    const unit = units.get(id);
    if (!unit) return;
    const [x1, y1] = screenPosition(unit.position);
    const [x2, y2] = screenPosition(target);
    context.beginPath();
    context.moveTo(x1, y1);
    context.lineTo(x2, y2);
    context.stroke();
  });
  context.restore();
}

function draw() {
  const rect = state.viewport;
  context.fillStyle = colors.background;
  context.fillRect(0, 0, rect.width, rect.height);
  drawGrid();
  drawAxes();
  const overview = state.overview;
  if (!overview?.available) {
    context.fillStyle = "#76838b";
    context.font = "14px Segoe UI, Microsoft YaHei, sans-serif";
    context.textAlign = "center";
    context.fillText("等待 Agent 历史数据", rect.width / 2, rect.height / 2);
    context.textAlign = "start";
    return;
  }
  if (state.layers.explored) drawIndexedCells("explored", colors.explored);
  if (state.layers.obstacles) drawIndexedCells("obstacles", colors.obstacle, 0.72);
  if (state.layers.history) drawIndexedCells("resource_history", colors.resourceHistory, 0.34);
  if (state.layers.routes) Object.values(overview.trails || {}).forEach(drawTrail);

  const objects = overview.state.objects || [];
  const allianceObjects = overview.alliance_objects || [];
  const allianceIds = new Set(allianceObjects.map((item) => item.id));
  (state.layers.history ? overview.enemy_core_history || [] : [])
    .filter((item) => !item.currently_visible && !allianceIds.has(item.core_id))
    .forEach((item) => drawCore(item, "enemy", true));
  (state.layers.history ? overview.enemy_unit_history || [] : [])
    .filter((item) => !item.currently_visible && !allianceIds.has(item.id) && shouldDrawObject(item))
    .forEach((item) => drawUnit(item, "enemy", true));

  const objectById = new Map();
  for (const item of objects) {
    if (state.layers.resources && item.kind === "RESOURCE") item.positions.forEach((position) => drawCell(position, colors.resource, 0.5));
    if (item.id) objectById.set(item.id, item);
  }
  for (const item of objects) {
    if (allianceIds.has(item.id) || item.relation === "ALLY") continue;
    if (!shouldDrawObject(item)) continue; //过滤掉非当前兵种的单位
    if (item.kind === "CORE") drawCore(item, item.controlled ? "friendly" : "enemy");
    if (item.kind === "UNIT") drawUnit(item, item.controlled ? "friendly" : "enemy");
  }
  for (const item of allianceObjects) {
    if (!shouldDrawObject(item)) continue; //过滤盟友非当前兵种单位
    if (item.kind === "CORE") drawCore(item, "ally");
    if (item.kind === "UNIT") drawUnit(item, "ally");
  }
  drawPlan(overview, objectById);
  drawRoutes(overview);
  drawEnemyFlash();
  startEnemyFlashLoop();

  const beacon = overview.state.champion_beacon;
  if (beacon?.position) {
    const [x, y] = screenPosition(beacon.position);
    const outer = Math.max(6, state.view.scale * 0.6);
    const inner = outer * 0.4;
    context.fillStyle = colors.beacon;
    context.strokeStyle = "#8a6d1f";
    context.lineWidth = 1;
    drawStar(x, y, 5, outer, inner);
    context.fill();
    context.stroke();
  }
  drawOrderTarget();
  drawSelectedUnitsHighlight();
  drawFlashTarget();
}

function setTargetPicking(active, mode = "order") {
  state.pickingTarget = active;
  state.pickMode = active ? mode : null;
  ui.pickTarget.classList.toggle("active", active && mode === "order");
  ui.pickExpeditionTarget.classList.toggle("active", active && mode === "expedition");
  const targetButtonText = state.orderTarget ? "隐藏地图选点" : "在地图上选择目标";
  ui.pickTarget.textContent = active && mode === "order" ? "取消选点" : targetButtonText;
  ui.pickExpeditionTarget.textContent = active && mode === "expedition" ? "取消选点" : targetButtonText;
  canvas.classList.toggle("picking-target", active);
}

function clearMapTarget() {
  state.orderTarget = null;
  setTargetPicking(false);
  draw();
}

function fitMap() {
  const cells = [...state.mapIndex.explored.values()].flat();
  if (!cells.length) return;
  let minX = cells[0][0]; let maxX = minX; let minY = cells[0][1]; let maxY = minY;
  cells.forEach(([x, y]) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  });
  state.view.x = (minX + maxX) / 2;
  state.view.y = (minY + maxY) / 2;
  state.view.scale = Math.max(1.5, Math.min(32,
    Math.min(state.viewport.width / Math.max(1, maxX - minX + 4), state.viewport.height / Math.max(1, maxY - minY + 4))));
  updateMetrics();
  draw();
}

function controlledCore() {
  return state.overview?.state?.objects?.find((item) => item.kind === "CORE" && item.controlled);
}

function centerMap(force = false) {
  const core = controlledCore();
  if (!core || (state.centered && !force)) return;
  centerMapAt(core.position);
}

function centerMapAt(position) {
  if (!Array.isArray(position) || position.length !== 2) return;
  state.view.x = position[0];
  state.view.y = position[1];
  state.centered = true;
  draw();
}

function populationCapacity(population) {
  return Math.max(10, population * 5);
}

function accountCard(key) {
  const existing = state.accountCards.get(key);
  if (existing) return existing;
  const button = document.createElement("button");
  button.type = "button";
  const badge = document.createElement("span");
  badge.className = "account-badge";
  const name = document.createElement("span");
  name.className = "account-name";
  const stats = document.createElement("span");
  stats.className = "account-stats";
  const defense = document.createElement("span");
  defense.className = "account-defense";
  defense.hidden = true;
  button.append(badge, name, stats, defense);
  const card = { button, badge, name, stats, defense, position: null };
  button.addEventListener("click", () => {
    // 点击账号卡片：切换信息与操作上下文，并把地图定位到该账号 Core
    setSelectedAccount(key === "primary" ? null : state.knownAccounts.get(key));
    if (card.position) centerMapAt(card.position);
  });
  state.accountCards.set(key, card);
  return card;
}

function setSelectedAccount(username) {
  const next = username || null;
  if (state.selectedAccount === next) return;
  state.selectedAccount = next;
  updateAccountChips();
  renderAccountStatus(state.overview?.accounts || []);
  updateMetrics();
  renderUnitList();
  refreshControl();
}

function updateAccountChips() {
  const label = state.selectedAccount
    ? `操作目标：@${state.selectedAccount}`
    : "操作目标：大号";
  if (ui.controlAccountChip) ui.controlAccountChip.textContent = label;
  if (ui.productionAccountChip) ui.productionAccountChip.textContent = label;
}

function urlWithAccount(url) {
  if (!state.selectedAccount) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}account=${encodeURIComponent(state.selectedAccount)}`;
}

function selectedAccountSummary() {
  if (!state.selectedAccount) return null;
  return (
    (state.overview?.accounts || []).find(
      (account) => account.role === "secondary" && account.username === state.selectedAccount,
    ) || null
  );
}

function renderAccountStatus(accounts) {
  const seen = new Set();
  const liveTick = accounts.reduce(
    (highest, account) => Math.max(highest, account.tick),
    state.overview?.tick ?? 0,
  );
  accounts.forEach((account) => {
    const key = account.role === "primary" ? "primary" : `secondary:${account.username}`;
    seen.add(key);
    if (account.username) state.knownAccounts.set(key, account.username);
    const card = accountCard(key);
    const lag = state.live ? Math.max(0, liveTick - account.tick) : 0;
    const stale = lag > ACCOUNT_STALE_TICKS;
    const username = account.username ? `@${account.username}` : "未识别";
    card.position = account.core_position;
    // 队友防御状态来自共享联盟目录；主号自身态势已在顶部指标卡展示
    const peerCore = (state.overview?.alliance_objects || []).find(
      (item) => item.kind === "CORE" && item.owner_username === account.username,
    );
    const defenseInfo = peerCore?.defense || null;
    const underAttack = Boolean(defenseInfo?.under_attack);
    card.defense.hidden = !defenseInfo;
    card.defense.textContent = underAttack
      ? "求援"
      : String(defenseInfo?.threat_level || "").toLowerCase();
    card.defense.className = `account-defense${underAttack ? " urgent" : ""}`;
    const active =
      key === "primary" ? state.selectedAccount === null : state.selectedAccount === account.username;
    card.button.className = `account-summary ${account.role}${stale ? " stale" : ""}${
      active ? " active" : ""
    }${underAttack ? " under-attack" : ""}`;
    card.button.title = `${username} · Tick ${account.tick}${stale ? ` · 落后 ${lag} tick` : ""}${
      defenseInfo ? ` · 态势 ${defenseInfo.posture || "?"}` : ""
    } · 点击切换到该账号${
      account.core_position ? "并定位 Core" : ""
    }`;
    card.badge.textContent = account.role === "primary" ? "主" : "小";
    card.name.textContent = username;
    card.stats.textContent = `资源 ${account.resources}/${populationCapacity(account.population)} · 人口 ${
      account.population
    } · ${account.workers}W ${account.vanguards}V ${account.rangers}R${stale ? ` · 落后 ${lag}` : ""}`;
  });
  // 小号数据来自另一实例的历史库，掉线时整条会消失；保留占位以免状态条突然抖动。
  state.knownAccounts.forEach((username, key) => {
    if (seen.has(key)) return;
    const card = accountCard(key);
    const role = key === "primary" ? "primary" : "secondary";
    card.position = null;
    card.defense.hidden = true;
    const active =
      role === "primary" ? state.selectedAccount === null : state.selectedAccount === username;
    card.button.className = `account-summary ${role} offline${active ? " active" : ""}`;
    card.button.title = `@${username} · 离线 · 点击可查看其排队中的调兵与配置`;
    card.badge.textContent = role === "primary" ? "主" : "小";
    card.name.textContent = `@${username}`;
    card.stats.textContent = "离线";
  });
  const ordered = [...state.accountCards.entries()]
    .sort(([a], [b]) => (a === "primary" ? -1 : b === "primary" ? 1 : a.localeCompare(b)))
    .map(([, card]) => card.button);
  if (ordered.some((button, index) => ui.accountStatus.children[index] !== button)) {
    ui.accountStatus.replaceChildren(...ordered);
  }
}

function updateMetrics() {
  const overview = state.overview;
  if (!overview?.available) return;
  const game = overview.state;
  const units = game.objects.filter((item) => item.kind === "UNIT" && item.controlled);
  const workers = units.filter((item) => item.unit_type === "WORKER").length;
  const vanguards = units.filter((item) => item.unit_type === "VANGUARD").length;
  const rangers = units.filter((item) => item.unit_type === "RANGER").length;
  const enemies = Number.isInteger(overview.enemy_count)
    ? overview.enemy_count
    : game.objects.filter((item) => item.controlled === false && item.relation !== "ALLY").length;
  renderAccountStatus(overview.accounts || []);
  // 选中小号时，资源/人口/兵力/Tick 展示该账号自己的摘要数据
  const selected = selectedAccountSummary();
  ui.tick.textContent = selected ? selected.tick : overview.tick;
  ui.resources.textContent = selected
    ? `${selected.resources}/${populationCapacity(selected.population)}`
    : `${game.resources}/${populationCapacity(game.population)}`;
  ui.population.textContent = selected ? selected.population : game.population;
  ui.force.textContent = selected
    ? `${selected.workers}W ${selected.vanguards}V ${selected.rangers}R`
    : `${workers}W ${vanguards}V ${rangers}R`;
  ui.posture.textContent = overview.strategy.phase || overview.strategy.posture || "--";
  ui.enemies.textContent = enemies;
  const mode = state.live ? "实时" : "历史";
  ui.status.textContent = `${mode} · 已探索 ${state.exploredCount} · 历史 Core ${(overview.enemy_core_history || []).length} · 缩放 ${state.view.scale.toFixed(1)}`;
}

function eventClass(type) {
  if (type.includes("DAMAGED") || type.includes("DESTROYED") || type.includes("SHOT") || type.includes("SWEEP")) return "combat";
  if (type.includes("FAILED") || type.includes("OVERFLOW")) return "warning";
  return "success";
}

function renderEvents() {
  const events = state.overview?.state?.events || [];
  ui.events.replaceChildren();
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "当前 Tick 无事件";
    ui.events.append(item);
    return;
  }
  [...events].reverse().forEach((event) => {
    const item = document.createElement("li");
    const tick = document.createElement("span");
    tick.className = "event-tick";
    tick.textContent = `t${event.tick}`;
    const text = document.createElement("span");
    text.className = eventClass(event.event_type);
    text.append(event.event_type);
    if (event.reason_code) {
      const reason = document.createElement("span");
      reason.textContent = ` / ${event.reason_code}`;
      text.append(reason);
    }
    if (event.position) {
      const at = document.createElement("span");
      at.textContent = " @ ";
      text.append(at, coordLink(event.position[0], event.position[1]));
    }
    item.append(tick, text);
    ui.events.append(item);
  });
}

function ownUsername() {
  return controlledCore()?.owner_username || "";
}

function renderRanking() {
  ui.rankings.replaceChildren();
  const entries = state.leaderboard?.[state.rankingKey] || [];
  if (!entries.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = state.leaderboard?.available === false ? "排行榜暂不可用" : "暂无排名";
    ui.rankings.append(item);
    return;
  }
  const me = ownUsername().toLowerCase();
  entries.forEach((entry) => {
    const item = document.createElement("li");
    if (entry.username.toLowerCase() === me) item.className = "me";
    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = `#${entry.rank}`;
    const username = document.createElement("span");
    username.className = "username";
    username.textContent = `@${entry.username}`;
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = entry.score.toLocaleString();
    item.append(rank, username, score);
    ui.rankings.append(item);
  });
}

function renderControl() {
  const stats = state.kills || {};
  const username = ownUsername();
  ui.killHeading.textContent = username ? `我的战果 @${username}` : "我的战果";
  ui.killStats.replaceChildren();
  [
    ["单位摧毁参与", stats.unit_participations || 0],
    ["Core 摧毁参与", stats.core_participations || 0],
    ["合计", stats.total_participations || 0],
    ["遭受攻击", stats.attacks_received || 0],
    ["单位阵亡", stats.units_lost || 0],
    ["Core 阵亡", stats.cores_lost || 0],
  ].forEach(([label, value]) => {
    const item = document.createElement("span");
    item.textContent = label;
    const number = document.createElement("strong");
    number.textContent = value;
    item.append(number);
    ui.killStats.append(item);
  });

  ui.kills.replaceChildren();
  const recentKills = stats.recent || [];
  if (!recentKills.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "暂无摧毁记录";
    ui.kills.append(item);
  } else {
    recentKills.forEach((kill) => {
      const item = document.createElement("li");
      const username = kill.username ? ` @${kill.username}` : "";
      item.append(`t${kill.tick} ${kill.kind === "CORE" ? "Core" : "单位"}${username}`);
      if (Array.isArray(kill.position)) {
        item.append(" @ ", coordLink(kill.position[0], kill.position[1]));
      }
      ui.kills.append(item);
    });
  }

  ui.losses.replaceChildren();
  const attacks = stats.attacks || [];
  (attacks.length ? attacks : [{ empty: true }]).forEach((loss) => {
    const item = document.createElement("li");
    if (loss.empty) {
      item.className = "empty-state";
      item.textContent = "暂无受击记录";
    } else {
      const result = loss.outcome === "DESTROYED" ? "摧毁" : "攻击";
      const attacker = loss.username ? `被 @${loss.username} ${result}` : `${result}者身份未公开`;
      item.append(`t${loss.tick} ${loss.kind === "CORE" ? "Core" : "单位"} ${attacker}`);
      if (Array.isArray(loss.position)) {
        item.append(" @ ", coordLink(loss.position[0], loss.position[1]));
      }
    }
    ui.losses.append(item);
  });

  ui.revenge.replaceChildren();
  const revengeTargets = stats.revenge_targets || [];
  (revengeTargets.length ? revengeTargets : [{ empty: true }]).forEach((target) => {
    const item = document.createElement("li");
    if (target.empty) {
      item.className = "empty-state";
      item.textContent = "暂无可确认仇敌";
    } else {
      item.textContent = `@${target.username} · 仇恨 ${target.score}`;
    }
    ui.revenge.append(item);
  });

  ui.orders.replaceChildren();
  if (!state.orders.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "暂无调兵记录";
    ui.orders.append(item);
  } else {
    state.orders.forEach((order) => {
      const item = document.createElement("li");
      item.className = "order-item";
      const unitIds = order.unit_ids?.length
        ? order.unit_ids.map((id) => id.slice(0, 8)).join(", ")
        : "旧订单未指定单位";
      const summary = document.createElement("span");
      summary.append(`#${order.id} ${order.unit_type} x${order.unit_count} → (`);
      summary.append(coordLink(order.target_x, order.target_y, `${order.target_x},${order.target_y}`));
      summary.append(`) / ${order.status} / ${unitIds}`);
      item.append(summary);
      if (order.status === "PENDING") {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.dataset.cancelOrder = order.id;
        cancel.textContent = "取消";
        cancel.title = "取消订单，单位将在下一 Tick 恢复自主策略";
        item.append(cancel);
      }
      ui.orders.append(item);
    });
  }
  renderUnitPicker();
  renderExpeditions();
}

function renderExpeditions() {
  ui.expeditionList.replaceChildren();
  const expeditions = state.controlConfig.expeditions || [];
  (expeditions.length ? expeditions : [{ empty: true }]).forEach((expedition) => {
    const item = document.createElement("li");
    if (expedition.empty) {
      item.className = "empty-state";
      item.textContent = "暂无远征队";
    } else {
      item.className = "order-item";
      const summary = document.createElement("span");
      const perimeter = expedition.mode === "ALLIANCE_PERIMETER";
      summary.append(`${expedition.enabled ? "启用" : "暂停"} · ${expedition.name} · `);
      if (perimeter) {
        summary.append("按联合兵力动态巡逻");
      } else {
        summary.append(`${expedition.ranger_count}R ${expedition.vanguard_count}V → (`);
        summary.append(coordLink(expedition.target_x, expedition.target_y, `${expedition.target_x},${expedition.target_y}`));
        summary.append(")");
      }
      const edit = document.createElement("button");
      edit.type = "button"; edit.dataset.editExpedition = expedition.id; edit.textContent = "编辑";
      const remove = document.createElement("button");
      remove.type = "button"; remove.dataset.deleteExpedition = expedition.id; remove.textContent = "删除";
      item.append(summary, edit, remove);
    }
    ui.expeditionList.append(item);
  });
}

const UNIT_TYPE_ORDER = { CORE: 0, WORKER: 1, VANGUARD: 2, RANGER: 3 };
const UNIT_TYPE_NAMES = { WORKER: "工人", VANGUARD: "先锋", RANGER: "游侠" };

function formatUnitLabel(unit) {
  if (unit.kind === "CORE") return "Core";
  return `${UNIT_TYPE_NAMES[unit.unit_type] || unit.unit_type || "单位"} ${unit.id.slice(0, 8)}`;
}

function formatUnitAction(action) {
  if (!action) return "—";
  if (action.type === "MOVE" && action.direction) {
    const labels = { UP: "上", DOWN: "下", LEFT: "左", RIGHT: "右" };
    return `移动${labels[action.direction] || action.direction}`;
  }
  if (action.type === "ATTACK") return "攻击";
  if (action.type === "GATHER") return "采集";
  if (action.type === "RETURN") return "返程";
  if (action.type === "IDLE") return "待机";
  return action.type;
}

function setUnitFilter(filter) {
  state.unitFilter = filter;
  document.querySelectorAll("[data-unit-filter]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.unitFilter === filter);
  });
  draw();
  renderUnitList();
}

function renderUnitList() {
  if (!ui.unitList) return;
  ui.unitList.replaceChildren();
  // 选中小号时，单位列表改用联盟共享状态里该账号的单位（大号视角下它们不是 controlled）
  const objects = state.selectedAccount
    ? (state.overview?.alliance_objects || []).filter(
        (item) => item.owner_username === state.selectedAccount,
      )
    : state.overview?.state?.objects || [];
  const actions = state.overview?.plan?.unit_actions || {};
  const units = objects
    .filter((item) =>
      state.selectedAccount
        ? ["CORE", "UNIT"].includes(item.kind)
        : item.controlled === true && ["CORE", "UNIT"].includes(item.kind),
    )
    .sort((left, right) => {
      const leftKind = left.kind === "CORE" ? "CORE" : left.unit_type;
      const rightKind = right.kind === "CORE" ? "CORE" : right.unit_type;
      // 注意用 ?? 而非 ||：CORE 的序号是 0，|| 会把 0 当成缺失值回退到 99，导致 Core 沉底
      const orderDiff = (UNIT_TYPE_ORDER[leftKind] ?? 99) - (UNIT_TYPE_ORDER[rightKind] ?? 99);
      if (orderDiff !== 0) return orderDiff;
      return String(left.id).localeCompare(String(right.id));
    });

  const filtered = units.filter((item) => shouldDrawObject(item));

  if (!filtered.length) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "当前没有可显示的单位";
    empty.style.cursor = "default";
    ui.unitList.append(empty);
    return;
  }

  filtered.forEach((unit) => {
    const item = document.createElement("li");
    if (unit.kind === "CORE") item.classList.add("core");
    item.dataset.unitId = unit.id;

    const iconCell = document.createElement("div");
    iconCell.className = "unit-icon";
    const shape = document.createElement("span");
    shape.className = "shape";
    if (unit.kind === "CORE") shape.classList.add("core");
    else if (unit.unit_type === "WORKER") shape.classList.add("worker");
    else if (unit.unit_type === "VANGUARD") shape.classList.add("vanguard");
    else if (unit.unit_type === "RANGER") shape.classList.add("ranger");
    iconCell.append(shape);

    const info = document.createElement("div");
    info.className = "unit-info";
    const idSpan = document.createElement("span");
    idSpan.className = "unit-id";
    idSpan.textContent = formatUnitLabel(unit);
    item.title = unit.id;
    const meta = document.createElement("span");
    meta.className = "unit-meta";
    const cargo = unit.kind === "UNIT" && unit.unit_type === "WORKER" && unit.cargo > 0 ? ` · 载货${unit.cargo}` : "";
    meta.textContent = `(${unit.position[0]}, ${unit.position[1]}) · HP ${unit.hp}${cargo}`;
    info.append(idSpan, meta);

    const action = document.createElement("span");
    action.className = "unit-action";
    action.textContent = formatUnitAction(actions[unit.id]);

    item.append(iconCell, info, action);
    ui.unitList.append(item);
  });
}

function renderUnitPicker() {
  const selectedType = document.querySelector("#order-unit-type").value;
  if (selectedType === "CORE" && ui.orderSelectionMode.value === "DISTANT") {
    ui.orderSelectionMode.value = "MANUAL";
  }
  const selectionMode = ui.orderSelectionMode.value;
  const distantOption = ui.orderSelectionMode.querySelector('option[value="DISTANT"]');
  distantOption.disabled = selectedType === "CORE";
  const core = state.controlUnits.find((unit) => unit.kind === "CORE");
  const minDistance = Math.max(0, Number(ui.orderMinDistance.value) || 0);
  const selectedIds = new Set(
    [...ui.orderUnitList.querySelectorAll("input:checked")].map((input) => input.value),
  );
  const units = state.controlUnits
    .filter((unit) => (
      selectedType === "CORE"
        ? unit.kind === "CORE"
        : unit.kind === "UNIT" && unit.unit_type === selectedType
    ))
    .sort((left, right) => left.id.localeCompare(right.id));
  ui.orderUnitList.replaceChildren();
  ui.orderDistanceField.classList.toggle("hidden", selectionMode !== "DISTANT");
  if (!units.length) {
    const empty = document.createElement("span");
    empty.className = "empty-state";
    empty.textContent = `当前没有可派遣的 ${selectedType}`;
    ui.orderUnitList.append(empty);
  } else {
    units.forEach((unit) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = unit.id;
      const coreDistance = core
        ? Math.abs(unit.position[0] - core.position[0]) + Math.abs(unit.position[1] - core.position[1])
        : null;
      checkbox.checked = selectionMode === "ALL"
        || (selectionMode === "DISTANT" && coreDistance !== null && coreDistance >= minDistance)
        || (selectionMode === "MANUAL" && selectedIds.has(unit.id));
      const cargo = unit.unit_type === "WORKER" ? ` / 载货 ${unit.cargo}` : "";
      const distance = unit.kind === "UNIT" && coreDistance !== null ? ` / 距 Core ${coreDistance}` : "";
      const text = document.createElement("span");
      text.textContent = `${unit.id.slice(0, 8)} / (${unit.position[0]},${unit.position[1]}) / HP ${unit.hp}${cargo}${distance}`;
      label.append(checkbox, text);
      ui.orderUnitList.append(label);
    });
  }
  document.querySelector("#order-count").value = ui.orderUnitList.querySelectorAll("input:checked").length;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadOverview(tick = null) {
  // 地图图层已移交 /api/map-base 后台缓存，overview 只承载轻量状态（<1MB）。
  // 在途时直接跳过，避免轮询叠加出请求风暴（下个周期会自动补上）
  if (state.overviewLoading) return;
  state.overviewLoading = true;
  try {
    const query = tick !== null ? `?tick=${encodeURIComponent(tick)}&history=0` : "";
    const overview = await fetchJson(`/api/overview${query}`);
    state.overview = overview;
    // viewTick=null 表示实时；回放时绘制按格子的 first_seen 本地过滤
    state.viewTick = tick;
    if (overview.available) {
      centerMap(false);
      updateMetrics();
      renderEvents();
      renderRanking();
      renderUnitList();
      await refreshMapBase(overview.map_version);
      draw();
    }
  } finally {
    state.overviewLoading = false;
  }
}

async function refreshMapBase(serverVersion = null) {
  const target = serverVersion ?? state.overview?.map_version;
  if (!state.overview?.available || target == null || state.mapLoading) return;
  if (target === state.mapVersion) return;
  state.mapLoading = true;
  try {
    const query = state.mapVersion != null
      ? `?version=${encodeURIComponent(state.mapVersion)}`
      : "";
    const response = await fetch(`/api/map-base${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.startsWith("application/json")) {
      // building：缓存首轮构建中，下个周期重试
      await response.json();
      return;
    }
    const headerVersion = Number(response.headers.get("X-Map-Version"));
    const text = await response.text();
    let changed = false;
    for (const line of text.split("\n")) {
      if (!line) continue;
      const entry = JSON.parse(line);
      indexCells(entry.l, entry.r);
      changed = true;
    }
    if (Number.isFinite(headerVersion)) state.mapVersion = headerVersion;
    if (changed) {
      updateMetrics();
      draw();
    }
  } catch (error) {
    ui.status.textContent = `底图接口错误 · ${error.message}`;
  } finally {
    state.mapLoading = false;
  }
}

async function refreshTicks() {
  try {
    const payload = await fetchJson("/api/ticks?limit=1024");
    const previousLatest = state.ticks.at(-1)?.tick;
    state.ticks = payload.ticks || [];
    ui.slider.max = Math.max(0, state.ticks.length - 1);
    if (state.live) {
      state.selectedIndex = state.ticks.length - 1;
      ui.slider.value = Math.max(0, state.selectedIndex);
      const latest = state.ticks.at(-1)?.tick;
      if (latest !== previousLatest || !state.overview) await loadOverview();
      else void refreshMapBase(); // 底图缓存可能在轮询间隙追平了新格子
    }
  } catch (error) {
    ui.status.textContent = `历史接口错误 · ${error.message}`;
  }
}

async function refreshLeaderboard() {
  try {
    state.leaderboard = await fetchJson("/api/leaderboard");
    renderRanking();
  } catch (error) {
    state.leaderboard = { available: false, error: error.message };
    renderRanking();
  }
}

async function refreshControl() {
  try {
    // 选中小号时，战果/调兵/配置都按该账号的历史库读取
    const [kills, orders, overview, controlConfig] = await Promise.all([
      fetchJson(urlWithAccount("/api/kills")),
      fetchJson(urlWithAccount("/api/orders")),
      fetchJson("/api/overview?history=0"),
      fetchJson(urlWithAccount("/api/control-config")),
    ]);
    state.kills = kills;
    state.orders = orders || [];
    state.controlConfig = controlConfig;
    if (state.selectedAccount) {
      // 手动派遣的单位选择器同样切换到该账号（数据来自联盟共享状态）
      state.controlUnits = (overview.alliance_objects || []).filter(
        (item) =>
          ["CORE", "UNIT"].includes(item.kind) &&
          item.owner_username === state.selectedAccount,
      );
    } else {
      state.controlUnits = (overview.state?.objects || []).filter(
        (item) => ["CORE", "UNIT"].includes(item.kind) && item.controlled === true,
      );
    }
    renderControl();
    renderUnitList();
    const production = controlConfig.production;
    if (production && document.activeElement?.form !== ui.productionForm) {
      document.querySelector("#production-worker").value = production.worker_weight;
      document.querySelector("#production-vanguard").value = production.vanguard_weight;
      document.querySelector("#production-ranger").value = production.ranger_weight;
    }
    const alliance = controlConfig.alliance;
    if (alliance && document.activeElement?.form !== ui.allianceForm) {
      document.querySelector("#alliance-rally-enabled").checked = alliance.rally_enabled;
      document.querySelector("#alliance-rally-radius").value = alliance.rally_radius;
      document.querySelector("#alliance-defense-enabled").checked = alliance.defense_enabled !== false;
    }
  } catch (error) {
    ui.orderStatus.textContent = `调兵接口错误 · ${error.message}`;
  }
}

async function selectIndex(index) {
  if (!state.ticks.length) return;
  state.selectedIndex = Math.max(0, Math.min(index, state.ticks.length - 1));
  state.live = state.selectedIndex === state.ticks.length - 1;
  ui.live.classList.toggle("active", state.live);
  ui.slider.value = state.selectedIndex;
  await loadOverview(state.ticks[state.selectedIndex].tick);
}

function togglePlay() {
  state.playing = !state.playing;
  ui.play.textContent = state.playing ? "Ⅱ" : "▶";
  ui.play.title = state.playing ? "暂停历史" : "播放历史";
  clearInterval(state.playTimer);
  if (state.playing) {
    state.live = false;
    ui.live.classList.remove("active");
    state.playTimer = setInterval(() => {
      if (state.selectedIndex >= state.ticks.length - 1) {
        state.playing = false;
        ui.play.textContent = "▶";
        clearInterval(state.playTimer);
        return;
      }
      selectIndex(state.selectedIndex + 1);
    }, 700);
  }
}

function setPanel(name) {
  ["status", "ranking", "control"].forEach((item) => {
    const active = item === name;
    document.querySelector(`#${item}-tab`).classList.toggle("active", active);
    document.querySelector(`#${item}-tab`).setAttribute("aria-selected", active);
    document.querySelector(`#${item}-panel`).classList.toggle("hidden", !active);
  });
}

canvas.addEventListener("pointerdown", (event) => {
  clearHover(); 
  state.dragging = true;
  state.pointer = [event.clientX, event.clientY];
  state.pointerStart = [event.clientX, event.clientY];
  canvas.classList.add("dragging");
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointerleave", () => {
  clearHover();
})
canvas.addEventListener("pointermove", (event) => {
  if (!state.dragging) return;
  state.view.x -= (event.clientX - state.pointer[0]) / state.view.scale;
  state.view.y -= (event.clientY - state.pointer[1]) / state.view.scale;
  state.pointer = [event.clientX, event.clientY];
  scheduleDraw();
});
canvas.addEventListener("pointerup", (event) => {
  const moved = state.pointerStart
    && Math.hypot(event.clientX - state.pointerStart[0], event.clientY - state.pointerStart[1]) > 4;
  state.dragging = false;
  state.pointerStart = null;
  canvas.classList.remove("dragging");
  canvas.releasePointerCapture(event.pointerId);
   if (!moved) {
    const worldPos = worldPosition(event.clientX, event.clientY);
    // 判断是否按下了 Ctrl (Windows/Linux) 或 Cmd (Mac) 键
    const isCtrlPressed = event.ctrlKey || event.metaKey;

    // 检查点击位置是否有己方单位/核心
    const objects = state.overview?.state?.objects || [];
    const clickedUnit = objects.find((item) =>
      item.controlled &&
      shouldDrawObject(item) &&
      ["UNIT", "CORE"].includes(item.kind) &&
      Math.hypot(item.position[0] - worldPos[0], item.position[1] - worldPos[1]) <= 1.2
    );

    // 1. 优先处理点击单位：进入单选或 Ctrl 多选模式
    if (clickedUnit) {
      selectUnitInForm(clickedUnit, isCtrlPressed);
      draw();
      return;
    }

    // 2. 如果点击了空白地图：设置目的地并提交派遣（集体发送所有选中单位）
    if (state.pickingTarget) {
      state.orderTarget = worldPos;
      //根据当前模式填入相对或绝对坐标
      const displayPos = state.useRelativeCoords ? toRelativePos(worldPos) : worldPos;

      if (state.pickMode === "expedition") {
        document.querySelector("#expedition-x").value = displayPos[0];
        document.querySelector("#expedition-y").value = displayPos[1];
        ui.expeditionStatus.textContent = `目标已选择：${displayPos[0]}, ${displayPos[1]}`;
        setTargetPicking(false);
      } else {
        [ui.orderX.value, ui.orderY.value] = displayPos;
        setTargetPicking(false);

        // 提交表单（自动打包所有勾选的单位 ID）
        ui.orderForm.requestSubmit();
      }
      draw();
      return;
    }
  }
});
canvas.addEventListener("wheel", (event) => {
  clearHover();
  event.preventDefault();
  state.view.scale = Math.max(1.5, Math.min(32, state.view.scale * (event.deltaY < 0 ? 1.14 : 0.88)));
  updateMetrics();
  scheduleDraw();
}, { passive: false });

// 移动端双指缩放
canvas.addEventListener("touchstart", (event) => {
  if (event.touches.length === 2) {
    event.preventDefault();
    const dx = event.touches[0].clientX - event.touches[1].clientX;
    const dy = event.touches[0].clientY - event.touches[1].clientY;
    state.pinch = {
      startDistance: Math.hypot(dx, dy),
      startScale: state.view.scale,
      center: [
        (event.touches[0].clientX + event.touches[1].clientX) / 2,
        (event.touches[0].clientY + event.touches[1].clientY) / 2,
      ],
    };
  }
}, { passive: false });

canvas.addEventListener("touchmove", (event) => {
  if (event.touches.length === 2 && state.pinch) {
    event.preventDefault();
    const dx = event.touches[0].clientX - event.touches[1].clientX;
    const dy = event.touches[0].clientY - event.touches[1].clientY;
    const distance = Math.hypot(dx, dy);
    const ratio = distance / state.pinch.startDistance;
    const newScale = Math.max(1.5, Math.min(32, state.pinch.startScale * ratio));
    state.view.scale = newScale;
    updateMetrics();
    scheduleDraw();
  }
}, { passive: false });

canvas.addEventListener("touchend", () => {
  state.pinch = null;
});

canvas.addEventListener("touchcancel", () => {
  state.pinch = null;
});

// 兵种筛选按钮切换监听
document.querySelectorAll("[data-unit-filter]").forEach((button) => {
  button.addEventListener("click", () => setUnitFilter(button.dataset.unitFilter));
});
document.querySelector("#previous-tick").addEventListener("click", () => selectIndex(state.selectedIndex - 1));
document.querySelector("#next-tick").addEventListener("click", () => selectIndex(state.selectedIndex + 1));
document.querySelector("#toggle-play").addEventListener("click", togglePlay);
document.querySelector("#live-tick").addEventListener("click", () => selectIndex(state.ticks.length - 1));
document.querySelector("#center-map").addEventListener("click", () => centerMap(true));
document.querySelector("#zoom-in").addEventListener("click", () => { state.view.scale = Math.min(32, state.view.scale * 1.25); updateMetrics(); draw(); });
document.querySelector("#zoom-out").addEventListener("click", () => { state.view.scale = Math.max(1.5, state.view.scale * 0.8); updateMetrics(); draw(); });
ui.mapZoomIn.addEventListener("click", () => { state.view.scale = Math.min(32, state.view.scale * 1.25); updateMetrics(); draw(); });
ui.mapZoomOut.addEventListener("click", () => { state.view.scale = Math.max(1.5, state.view.scale * 0.8); updateMetrics(); draw(); });
ui.mapZoomHome.addEventListener("click", () => centerMap(true));
ui.panelToggle.addEventListener("click", () => {
  state.panelVisible = !state.panelVisible;
  document.body.classList.toggle("panel-hidden", !state.panelVisible);
  const title = state.panelVisible ? "隐藏右侧情报面板" : "显示右侧情报面板";
  ui.panelToggle.title = title;
  ui.panelToggle.setAttribute("aria-label", title);
});
ui.unitsPanelToggle.addEventListener("click", () => {
  state.unitsPanelVisible = !state.unitsPanelVisible;
  ui.unitsPanelContainer.classList.toggle("hidden", !state.unitsPanelVisible);
  ui.unitsPanelToggle.textContent = state.unitsPanelVisible ? "‹" : "›";
  const title = state.unitsPanelVisible ? "隐藏单位列表" : "显示单位列表";
  ui.unitsPanelToggle.title = title;
  ui.unitsPanelToggle.setAttribute("aria-label", title);
});
ui.slider.addEventListener("input", () => selectIndex(Number(ui.slider.value)));
document.querySelector("#status-tab").addEventListener("click", () => setPanel("status"));
document.querySelector("#ranking-tab").addEventListener("click", () => setPanel("ranking"));
document.querySelector("#control-tab").addEventListener("click", () => setPanel("control"));
document.querySelectorAll(".ranking-mode").forEach((button) => button.addEventListener("click", () => {
  state.rankingKey = button.dataset.ranking;
  document.querySelectorAll(".ranking-mode").forEach((item) => item.classList.toggle("active", item === button));
  renderRanking();
}));
document.querySelector("#fit-map").addEventListener("click", fitMap);
document.querySelectorAll("[data-map-layer]").forEach((input) => input.addEventListener("change", () => {
  state.layers[input.dataset.mapLayer] = input.checked;
  // 图例条目即图层开关：关闭时整行置灰
  input.closest(".legend-item")?.classList.toggle("layer-off", !input.checked);
  draw();
}));
ui.pickTarget.addEventListener("click", () => {
  if (state.orderTarget || (state.pickingTarget && state.pickMode === "order")) {
    clearMapTarget();
    return;
  }
  setTargetPicking(true, "order");
});
ui.pickExpeditionTarget.addEventListener("click", () => {
  if (state.orderTarget || (state.pickingTarget && state.pickMode === "expedition")) {
    clearMapTarget();
    return;
  }
  setTargetPicking(true, "expedition");
});
[ui.orderX, ui.orderY].forEach((input) => input.addEventListener("change", () => {
  const position = [Number(ui.orderX.value), Number(ui.orderY.value)];
  state.orderTarget = position.every(Number.isSafeInteger) ? position : null;
  setTargetPicking(false);
  draw();
}));

ui.orderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  ui.orderStatus.textContent = "提交中…";
  const unitIds = [...ui.orderUnitList.querySelectorAll("input:checked")].map((input) => input.value);
  if (!unitIds.length) {
    ui.orderStatus.textContent = "请先选择具体核心或至少一个具体单位";
    return;
  }
  // 将输入框坐标换算回发送给后端的绝对坐标
  const inputPos = [Number(ui.orderX.value), Number(ui.orderY.value)];
  const absPos = state.useRelativeCoords ? toAbsolutePos(inputPos) : inputPos;
  const payload = {
    unit_type: document.querySelector("#order-unit-type").value,
    unit_count: unitIds.length,
    unit_ids: unitIds,
    target_x: absPos[0], // 发给后端的永远是真实的绝对坐标
    target_y: absPos[1],
  };
  if (state.selectedAccount) payload.account = state.selectedAccount;
  try {
    const response = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || response.statusText);
    ui.orderStatus.textContent = state.selectedAccount
      ? `已提交给 @${state.selectedAccount} #${result.id}，将在下个 Tick 调整动作`
      : `已提交 #${result.id}，将在下个 Tick 调整动作`;
    setTargetPicking(false);
    await refreshControl();
  } catch (error) {
    ui.orderStatus.textContent = `提交失败 · ${error.message}`;
  }
});

document.querySelector("#order-unit-type").addEventListener("change", renderUnitPicker);
ui.orderSelectionMode.addEventListener("change", renderUnitPicker);
ui.orderMinDistance.addEventListener("input", () => {
  if (ui.orderSelectionMode.value === "DISTANT") renderUnitPicker();
});
ui.orderUnitList.addEventListener("change", () => {
  ui.orderSelectionMode.value = "MANUAL";
  ui.orderDistanceField.classList.add("hidden");
  document.querySelector("#order-count").value = ui.orderUnitList.querySelectorAll("input:checked").length;
});
ui.orders.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-cancel-order]");
  if (!button) return;
  button.disabled = true;
  try {
    const response = await fetch(urlWithAccount(`/api/orders/${encodeURIComponent(button.dataset.cancelOrder)}`), {
      method: "DELETE",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || response.statusText);
    ui.orderStatus.textContent = `已取消 #${result.id}，所选单位将在下一 Tick 恢复自主策略`;
    await refreshControl();
  } catch (error) {
    button.disabled = false;
    ui.orderStatus.textContent = `取消失败 · ${error.message}`;
  }
});

canvas.addEventListener("pointermove", (event) => {
  const [x, y] = worldPosition(event.clientX, event.clientY);
  ui.cursorPosition.textContent = formatCoordDisplay([x, y]); 
  if (state.dragging) {
    clearHover();
    return;
  }

  if (!currentHoverCell || currentHoverCell[0] !== x || currentHoverCell[1] !== y) {
    clearHover(); 
    currentHoverCell = [x, y];
    
    hoverTimer = setTimeout(() => {
      showHoverTooltip(x, y);
    }, HOVER_DELAY);
  }
});

ui.productionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    worker_weight: Number(document.querySelector("#production-worker").value),
    vanguard_weight: Number(document.querySelector("#production-vanguard").value),
    ranger_weight: Number(document.querySelector("#production-ranger").value),
  };
  if (state.selectedAccount) payload.account = state.selectedAccount;
  try {
    const response = await fetch("/api/control-config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    ui.productionStatus.textContent = state.selectedAccount
      ? `@${state.selectedAccount} 的生产比例已保存，将在下个 Tick 生效`
      : "生产比例已保存，将在下个 Tick 生效";
    await refreshControl();
  } catch (error) {
    ui.productionStatus.textContent = `保存失败 · ${error.message}`;
  }
});

ui.allianceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    rally_enabled: document.querySelector("#alliance-rally-enabled").checked,
    rally_radius: Number(document.querySelector("#alliance-rally-radius").value),
    defense_enabled: document.querySelector("#alliance-defense-enabled").checked,
  };
  try {
    const response = await fetch("/api/alliance-config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    const rallyText = result.rally_enabled
      ? `联盟靠拢已开启（距离 ${result.rally_radius} 格）`
      : "联盟靠拢已关闭";
    const defenseText = result.defense_enabled === false
      ? "防御支援已关闭"
      : "防御支援已开启";
    ui.allianceStatus.textContent = `${rallyText} · ${defenseText}，将在下个 Tick 生效`;
    await refreshControl();
  } catch (error) {
    ui.allianceStatus.textContent = `保存失败 · ${error.message}`;
  }
});

ui.expeditionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const rawId = document.querySelector("#expedition-id").value;
  // 将远征队输入的坐标换算回给后端的绝对坐标
  const inputPos = [
    Number(document.querySelector("#expedition-x").value),
    Number(document.querySelector("#expedition-y").value)
  ];
  const absPos = state.useRelativeCoords ? toAbsolutePos(inputPos) : inputPos;
  const payload = {
    id: rawId ? Number(rawId) : null,
    name: document.querySelector("#expedition-name").value,
    mode: document.querySelector("#expedition-mode").value,
    ranger_count: Number(document.querySelector("#expedition-ranger").value),
    vanguard_count: Number(document.querySelector("#expedition-vanguard").value),
    target_x: absPos[0],
    target_y: absPos[1],
    enabled: document.querySelector("#expedition-enabled").checked,
  };
  if (state.selectedAccount) payload.account = state.selectedAccount;
  try {
    const response = await fetch("/api/expeditions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    ui.expeditionStatus.textContent = state.selectedAccount
      ? `@${state.selectedAccount} 的远征队已保存，将在下个 Tick 生效`
      : "远征队已保存，将在下个 Tick 生效";
    document.querySelector("#expedition-id").value = "";
    await refreshControl();
  } catch (error) {
    ui.expeditionStatus.textContent = `保存失败 · ${error.message}`;
  }
});

ui.expeditionList.addEventListener("click", async (event) => {
  const edit = event.target.closest("button[data-edit-expedition]");
  const remove = event.target.closest("button[data-delete-expedition]");
  if (edit) {
    const expedition = state.controlConfig.expeditions.find((item) => item.id === Number(edit.dataset.editExpedition));
    if (!expedition) return;
    document.querySelector("#expedition-id").value = expedition.id;
    document.querySelector("#expedition-name").value = expedition.name;
    document.querySelector("#expedition-mode").value = expedition.mode || "TARGET";
    document.querySelector("#expedition-ranger").value = expedition.ranger_count;
    document.querySelector("#expedition-vanguard").value = expedition.vanguard_count;
    document.querySelector("#expedition-x").value = expedition.target_x;
    document.querySelector("#expedition-y").value = expedition.target_y;
    document.querySelector("#expedition-enabled").checked = expedition.enabled;
    return;
  }
  if (!remove) return;
  try {
    const response = await fetch(urlWithAccount(`/api/expeditions/${encodeURIComponent(remove.dataset.deleteExpedition)}`), { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || response.statusText);
    await refreshControl();
  } catch (error) {
    ui.expeditionStatus.textContent = `删除失败 · ${error.message}`;
  }
});

[ui.events, ui.kills, ui.losses, ui.orders, ui.expeditionList].forEach((list) => {
  list.addEventListener("click", handleCoordClick);
});

ui.unitList.addEventListener("click", (event) => {
  const item = event.target.closest("li[data-unit-id]");
  if (!item) return;
  const unitId = item.dataset.unitId;
  const sources = [
    ...(state.overview?.state?.objects || []),
    ...(state.overview?.alliance_objects || []),
  ];
  const unit = sources.find((obj) => obj.id === unitId && obj.position);
  if (!unit) return;
  locatePosition(unit.position, Math.max(state.view.scale, 10));
});

new ResizeObserver(resizeCanvas).observe(canvas);
updateAccountChips();
refreshTicks();
refreshLeaderboard();
refreshControl();
setInterval(refreshTicks, 5000);
setInterval(refreshLeaderboard, 15000);
setInterval(refreshControl, 5000);

// 移动端初始化：默认收起两侧面板，给地图留出更多空间
if (window.innerWidth <= 760) {
  state.panelVisible = false;
  document.body.classList.add("panel-hidden");
  ui.panelToggle.title = "显示右侧情报面板";
  ui.panelToggle.setAttribute("aria-label", "显示右侧情报面板");

  state.unitsPanelVisible = false;
  ui.unitsPanelContainer.classList.add("hidden");
  ui.unitsPanelToggle.textContent = "›";
  ui.unitsPanelToggle.title = "显示单位列表";
  ui.unitsPanelToggle.setAttribute("aria-label", "显示单位列表");
}

let isAllCollapsed = false;

// 折叠 / 展开指定栏目
function toggleSection(element, forceState) {
  if (!element) return;
  if (typeof forceState === "boolean") {
    element.classList.toggle("section-collapsed", forceState);
  } else {
    element.classList.toggle("section-collapsed");
  }
}

// “全部折叠 / 展开” 按钮事件
document.querySelector("#toggle-all-control")?.addEventListener("click", () => {
  isAllCollapsed = !isAllCollapsed;
  const sections = document.querySelectorAll("#control-panel .config-form, #control-panel .order-form, #control-panel .control-section");
  sections.forEach((sec) => toggleSection(sec, isAllCollapsed));
  document.querySelector("#toggle-all-control").textContent = isAllCollapsed ? "全部展开" : "全部折叠";
});

// 点击单个栏目标题进行折叠/展开
document.querySelector("#control-panel")?.addEventListener("click", (event) => {
  const header = event.target.closest("h3");
  if (!header) return;
  const section = header.closest(".config-form, .order-form, .control-section");
  if (section) {
    toggleSection(section);
  }
});

function setLegendVisible(visible) {
  state.legendVisible = visible;
  document.body.classList.toggle("legend-hidden", !visible);
  const checkbox = document.querySelector("#toggle-legend");
  if (checkbox) checkbox.checked = visible;
  const btn = document.querySelector("#legend-toggle");
  if (btn) btn.classList.toggle("inactive", !visible);
}

// 相对坐标模式开关监听
document.querySelector("#toggle-relative-coord")?.addEventListener("change", (event) => {
  state.useRelativeCoords = event.target.checked;
  draw();
});

// 图例显示开关监听
document.querySelector("#toggle-legend")?.addEventListener("change", (event) => {
  setLegendVisible(event.target.checked);
});

document.querySelector("#legend-toggle")?.addEventListener("click", () => {
  setLegendVisible(!state.legendVisible);
});
