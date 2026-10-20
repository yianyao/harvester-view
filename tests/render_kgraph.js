/*
 * render_kgraph.js -- 知识图谱（v0.86，大类总览+搜索子图）渲染门
 * （DOM 桩，同 render_wordcloud 模式）。
 *
 * 2026-10-20 用户裁决「开始只显示大类甚至啥也不用，给个搜索框，输入
 * 关键字再只显示相关的；黄圆圈改蓝」后重写，验两态模型：
 *   ① 组装渲染：A 档动作 kgraph 组装 → canvas + bar 账（大类/聚合/
 *     用户发起/搜索命中）；
 *   ② 两态显隐：大类态只显主题/卡片；搜索态（kgQuery 非空）全显
 *     （后端 FTS 子图）；
 *   ③ 搜索联动：kgQuery → run params {q} → bar 搜索账（正文 FTS 命中数）；
 *   ④ 确定性布局 + 缩放/平移/复位交互；
 *   ⑤ hover 邻域（簇 badge 账）；点主题锁定邻域；
 *   ⑥ 下钻分流：搜索态会话（含簇代表）点击一律直达原文；关键词 →
 *     jumpCloudWord；
 *   ⑦ 诚实空态 + 动作失败不静默 + 整页 script 语法解析。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

/* 整页 script 语法解析（行级替换曾留孤儿 `}`——extract 门测不到） */
const scriptBlocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
scriptBlocks.forEach((m, i) => { new Function(m[1]); });

function extract(name, sig) {
  const re = new RegExp("^(?:async )?function " + name + "\\(" + sig +
    "\\)\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name + "(" + sig + ")");
  return m[0];
}

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));

function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", value: "", onclick: null, _q: {}, _qa: {},
    width: 900, height: 620, clientWidth: 900,
    querySelector(sel) {
      if (!el._q[sel]) el._q[sel] = mkEl(id + ">" + sel);
      return el._q[sel];
    },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }

/* ---- 模块级桩（eval("const") 绑定不出作用域，KG_* 照抄声明）---- */
let kgData = null, kgPos = {}, kgIndex = {}, kgAdj = {};
let kgHoverId = null, kgPinId = null;
let kgView = { x: 0, y: 0, k: 1 }, kgDrag = null;
let kgQuery = "", kgDeb = null;
const KG_KIND_COLOR = { topic: "#6ea8ff", card: "#b48aff", kw: "#4fd1a5",
  session: "#4fb0ff" };
const KG_KIND_SIZE = { topic: 13, card: 9, kw: 5, session: 6 };
const KG_KIND_NAME = { topic: "主题", card: "卡片", kw: "关键词",
  session: "会话" };
const KG_EDGE_LABEL = { member: "成员", anchor: "锚点", topic_of: "归属",
  keyword: "关键词", kw_hit: "标题含词" };

const runCalls = [];
let runNext = [];
async function actJson(_url, body) {
  runCalls.push(body);
  if (!runNext.length) throw new Error("run 桩没有下一个响应");
  const d = runNext.shift();
  if (d instanceof Error) return { __err: String(d.message || d) };
  return d;
}
const jobCalls = [];
let jobNext = [];
async function actApi(url) {
  jobCalls.push(url);
  if (!jobNext.length) throw new Error("job 桩没有下一个响应：" + url);
  const d = jobNext.shift();
  if (d instanceof Error) return { __err: String(d.message || d) };
  return d;
}
const tabCalls = [];
function switchTab(n) { tabCalls.push(n); }
function showBanner(_k, msg) { banners.push(msg); }
const banners = [];
function errorBox(msg) { return "<div class='err'>" + esc(msg) + "</div>"; }
const state = { q: "", offset: 0, loaded: {} };
let sessCalls = 0;
const sessSids = [];
async function loadSessions() { sessCalls++; }
async function openSession(sid) { sessCalls += 10; sessSids.push(sid); }
global.confirm = () => true;
let kgDrawCalls = 0;
function kgDraw() { kgDrawCalls++; }
const _origSetTimeout = global.setTimeout;
global.setTimeout = (fn, _ms) => { fn(); return 0; };

eval(extract("kgRng", "seed"));
eval(extract("kgVisible", ""));
eval(extract("kgLayout", "nodes,edges,W,H"));
eval(extract("kgAdjBuild", ""));
eval(extract("kgBar", ""));
eval(extract("loadKgraph", ""));
eval(extract("kgWaitJob", "jid"));
eval(extract("renderKgraph", "d"));
eval(extract("kgRelayout", ""));
eval(extract("kgS2W", "sx,sy"));
eval(extract("kgFitView", ""));
eval(extract("kgHit", "sx,sy"));
eval(extract("kgShowTip", "html,sx,sy"));
eval(extract("kgHover", "e"));
eval(extract("kgOpenSession", "sid"));
eval(extract("kgCanvasClick", "e"));
eval(extract("kgWheel", "e"));
eval(extract("kgMouseDown", "e"));
eval(extract("kgMouseMove", "e"));
eval(extract("kgMouseUp", ""));
eval(extract("kgDblClick", ""));
eval(extract("jumpCloudWord", "w"));

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

const JOB_DONE = (result) => ({ status: "done", result });

(async () => {
  /* 夹具：SA 聚入 SB（簇2）、SC 单会话；大类态应只见 t:t1/c:c1.md */
  const FIX = {
    nodes: [
      { id: "s:SA", kind: "session", label: "部署排障网关重启失败",
        badge: "trae",
        meta: { sid: "SA", source: "trae", error_count: 0, merged: "SB" } },
      { id: "s:SB", kind: "session", label: "部署排障网关重启超时",
        badge: "trae · 簇2",
        meta: { sid: "SB", source: "trae", error_count: 0,
          cluster: ["SA", "SB"] } },
      { id: "s:SC", kind: "session", label: "买菜清单周末火锅",
        badge: "claude", meta: { sid: "SC", source: "claude",
          error_count: 0 } },
      { id: "t:t1", kind: "topic", label: "部署排障", badge: "1成员",
        meta: { topic_id: "t1", member_sids: ["SB"],
          keywords: ["部署", "排障"] } },
      { id: "c:c1.md", kind: "card", label: "部署卡", badge: "summary",
        meta: { path: "c1.md", anchor_sids: ["SB"] } },
      { id: "k:部署", kind: "kw", label: "部署", badge: "", meta: {} },
      { id: "k:描写", kind: "kw", label: "描写", badge: "自动枢纽 115",
        meta: { auto: true } },
    ],
    edges: [
      { src: "t:t1", dst: "s:SB", kind: "member" },
      { src: "c:c1.md", dst: "s:SB", kind: "anchor" },
      { src: "c:c1.md", dst: "t:t1", kind: "topic_of" },
      { src: "k:部署", dst: "t:t1", kind: "keyword" },
      { src: "k:部署", dst: "s:SB", kind: "kw_hit" },
      { src: "k:描写", dst: "s:SB", kind: "kw_hit" },
    ],
    meta: { topics: 1, cards: 1, keywords: 1, auto_hubs: 1, search: null,
      sessions: { total: 5, user_initiated: 3, filtered: 2,
        drawn: 3, truncated: false },
      clusters: 2, clusters_multi: 1, merged_into_rep: 1,
      kw_hit_counts: { "部署": 1 }, kw_hit_capped: [],
      cards_unlinked: 0, caps: { sessions: 1500, kw_hits: 200 },
      criteria: { user_initiated: "首条消息 role='user'",
        cluster: "标题 token Jaccard≥0.45 或 bigram 包含率≥0.6",
        representative: "簇内 updated_at 最新" },
      derivation: "机械推导" },
    hints: [],
  };
  const SEARCH_FIX = () => ({ ...FIX,
    meta: { ...FIX.meta,
      search: { q: "部署", fts_matched: 3 },
      sessions: { ...FIX.meta.sessions, drawn: 3 } } });

  /* ① 组装渲染（大类态） */
  runNext = [{ job_id: "j1" }];
  jobNext = [JOB_DONE(FIX)];
  await loadKgraph();
  check("空查询 run params 为空对象（默认=大类总览）",
    runCalls.length === 1 &&
    JSON.stringify(runCalls[0].params) === "{}" &&
    runCalls[0].action_id === "kgraph");
  check("canvas 显示 + bar 账（聚合/枢纽/用户发起/大类态可见边）",
    $("kg-canvas").style.display !== "none" &&
    $("kg-bar").textContent.includes("含 1 个簇代表，1 会话已聚入") &&
    $("kg-bar").textContent.includes("自动枢纽 1") &&
    $("kg-bar").textContent.includes("用户发起 3/5") &&
    $("kg-bar").textContent.includes("显示边 1"),  // 大类态只显 topic/card 邻接边
    $("kg-bar").textContent);

  /* ② 两态显隐（用户裁决核心） */
  check("大类态：只显主题/卡片；会话/关键词全隐",
    kgVisible().has("t:t1") && kgVisible().has("c:c1.md") &&
    !kgVisible().has("s:SA") && !kgVisible().has("s:SB") &&
    !kgVisible().has("s:SC") && !kgVisible().has("k:部署") &&
    !kgVisible().has("k:描写"));
  kgQuery = "部署";
  check("搜索态：后端 FTS 子图全显（会话/词/主题/卡片）",
    kgVisible().has("s:SA") && kgVisible().has("s:SB") &&
    kgVisible().has("s:SC") && kgVisible().has("k:部署") &&
    kgVisible().has("t:t1") && kgVisible().has("c:c1.md"));

  /* ③ 搜索联动：q → params + bar 搜索账 */
  runNext = [{ job_id: "j2" }];
  jobNext = [JOB_DONE(SEARCH_FIX())];
  await loadKgraph();
  check("搜索态 run params 带 q（正文 FTS）",
    runCalls[1].params.q === "部署");
  check("bar 搜索账：命中 N 会话",
    $("kg-bar").textContent.includes("搜索 '部署'：命中 3 会话"),
    $("kg-bar").textContent);
  kgQuery = "";

  /* ④ 确定性布局 + 视图交互 */
  const p1 = kgLayout(FIX.nodes, FIX.edges, 900, 620);
  const p2 = kgLayout(FIX.nodes, FIX.edges, 900, 620);
  check("布局确定性（固定种子+自适应迭代）",
    FIX.nodes.every(nd => Math.abs(p1[nd.id].x - p2[nd.id].x) < 1e-9 &&
      Math.abs(p1[nd.id].y - p2[nd.id].y) < 1e-9));
  const drawBefore = kgDrawCalls;
  kgWheel({ deltaY: -120, offsetX: 450, offsetY: 310,
    preventDefault() {} });
  check("滚轮放大：k=1.15，鼠标为锚",
    Math.abs(kgView.k - 1.15) < 1e-9 &&
    Math.abs((450 - kgView.x) / kgView.k - 450) < 1e-9);
  kgDblClick();
  check("双击复位", kgView.k === 1 && kgView.x === 0 && kgView.y === 0);
  kgMouseDown({ offsetX: 400, offsetY: 300 });
  kgMouseMove({ offsetX: 460, offsetY: 340 });
  kgMouseUp();
  check("拖拽平移", kgView.x === 60 && kgView.y === 40);
  kgMouseDown({ offsetX: 400, offsetY: 300 });
  kgMouseMove({ offsetX: 402, offsetY: 301 });
  kgMouseUp();
  check("原地点击（<3px）不改视图", kgView.x === 60 && kgView.y === 40);
  kgDblClick();
  check("交互反复触发 kgDraw", kgDrawCalls > drawBefore);

  /* ⑤⑥ 搜索态交互（kgHit 按可见集过滤——会话只在搜索态可见） */
  kgQuery = "部署";
  kgHover({ offsetX: kgPos["s:SB"].x, offsetY: kgPos["s:SB"].y });
  check("hover 簇代表：tip 带簇账（聚2 会话）",
    kgHoverId === "s:SB" &&
    $("kg-tip").innerHTML.includes("簇2") &&
    $("kg-tip").innerHTML.includes("聚2 会话"));
  kgCanvasClick({ offsetX: kgPos["s:SC"].x, offsetY: kgPos["s:SC"].y });
  check("单会话直达原文（openSession(SC)）",
    sessCalls === 10 && sessSids[0] === "SC");
  kgCanvasClick({ offsetX: kgPos["s:SB"].x, offsetY: kgPos["s:SB"].y });
  check("簇代表也是会话：直达原文（不再展开显隐）",
    sessSids[1] === "SB");
  const kwNode = kgIndex["k:部署"];
  kgCanvasClick({ offsetX: kgPos[kwNode.id].x, offsetY: kgPos[kwNode.id].y });
  check("关键词下钻：switchTab(sessions)+state.q（不发动作）",
    tabCalls.includes("sessions") && state.q === "部署" &&
    $("f-q").value === "部署" && sessCalls === 21);
  kgQuery = "";
  const tNode = kgIndex["t:t1"];
  kgCanvasClick({ offsetX: kgPos[tNode.id].x, offsetY: kgPos[tNode.id].y });
  check("大类态点主题：锁定邻域 kgPinId", kgPinId === "t:t1");
  kgCanvasClick({ offsetX: kgPos[tNode.id].x, offsetY: kgPos[tNode.id].y });
  check("再点同节点：解锁", kgPinId === null);

  /* ⑦ 截断账 + 诚实空态 + 失败不静默 */
  renderKgraph({ ...FIX, meta: { ...FIX.meta,
    sessions: { ...FIX.meta.sessions, total: 900, truncated: true } } });
  check("截断标注「超上限只画最新」",
    $("kg-bar").textContent.includes("超上限只画最新"));
  renderKgraph({ nodes: [], edges: [],
    hints: ["搜索 '梦中救人'：正文没有命中的用户发起会话"] });
  check("空态 hints 原样 + canvas 隐藏",
    $("kg-msg").innerHTML.includes("梦中救人") &&
    $("kg-canvas").style.display === "none" &&
    !$("kg-msg").innerHTML.includes("undefined"));
  runNext = [new Error("连接中断")];
  await loadKgraph();
  check("入队失败有错误空态", $("kg-msg").innerHTML.includes("连接中断"));
  runNext = [{ job_id: "j4" }];
  jobNext = [{ status: "failed", error: "真库不可读" }];
  await loadKgraph();
  check("作业失败有错误空态", $("kg-msg").innerHTML.includes("真库不可读"));

  global.setTimeout = _origSetTimeout;
  if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
  console.log("ALL KGRAPH CHECKS PASSED");
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
