/*
 * render_kgraph.js -- 知识图谱（v0.84，用户发起×相近聚合×黑底）渲染门（DOM 桩，同 render_wordcloud 模式）。
 *
 * 2026-10-20 用户四点裁决后重写：验 v0.84 聚合账：
 *   ① 组装渲染：A 档动作 kgraph 一次组装 → canvas 显示 + bar 聚合账
 *     （会话节点=代表+单会话/簇代表数/聚入数/用户发起过滤/截断/封顶/显示边）；
 *   ② 聚合显隐：默认只显代表层（meta.merged 成员隐藏）；点代表 → kgOpened
 *     展开/收起成员（数据面一次下发，展开零等待）；
 *   ③ 确定性布局：同数据两次 kgLayout 坐标逐点一致（固定种子+自适应迭代）；
 *   ④ 视图交互：滚轮缩放（以鼠标为锚）、拖拽平移、双击复位；
 *   ⑤ hover 邻域：悬停设 kgHoverId + 簇提示；点击主题锁定 kgPinId；
 *   ⑥ 下钻分流：单会话/已展开成员 → openSession 直达原文；关键词 →
 *     jumpCloudWord 真检索；簇代表 → 展开（不直达）；
 *   ⑦ 诚实空态：hints 原样渲染 + canvas 隐藏；动作失败不静默。
 *
 * 纪律：只渲染服务端给的内容；本门验证渲染逻辑与数据纪律，画布像素不在
 * DOM 桩能力内（kgDraw 打桩计数）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

/* 整页 script 语法解析（行级替换曾留孤儿 `}`——extract 门测不到，
 * 全页死透；此检查兜住整类事故） */
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

/* ---- 模块级桩（extract 只取函数体，故先补齐闭包外引用）----
 * 注意：eval("const ...") 绑定不出 eval 作用域，KG_* 常量直接照抄声明
 * （v0.84 黑底亮色版；漂移时 test_v66 静态账会先红）。 */
let kgData = null, kgPos = {}, kgIndex = {}, kgAdj = {};
let kgHoverId = null, kgPinId = null;
let kgView = { x: 0, y: 0, k: 1 }, kgDrag = null, kgOpened = new Set();
const KG_KIND_COLOR = { topic: "#6ea8ff", card: "#b48aff", kw: "#4fd1a5",
  session: "#ffab70" };
const KG_KIND_SIZE = { topic: 13, card: 9, kw: 5, session: 6 };
const KG_KIND_NAME = { topic: "主题", card: "卡片", kw: "关键词",
  session: "会话" };
const KG_EDGE_LABEL = { member: "成员", anchor: "锚点", topic_of: "归属",
  keyword: "关键词", kw_hit: "标题含词" };

const runCalls = [];            // actJson 入参账
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
const banners = [];
function showBanner(_k, msg) { banners.push(msg); }
function errorBox(msg) { return "<div class='err'>" + esc(msg) + "</div>"; }
const state = { q: "", offset: 0, loaded: {} };
let sessCalls = 0;
const sessSids = [];
async function loadSessions() { sessCalls++; }
async function openSession(sid) { sessCalls += 10; sessSids.push(sid); }
global.confirm = () => true;
/* 画布绘制不在 DOM 桩能力内：kgDraw 打桩（变换与高亮逻辑照测调用次数） */
let kgDrawCalls = 0;
function kgDraw() { kgDrawCalls++; }
/* setTimeout 立即化（大图分支走异步布局路径） */
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
  /* v0.84 夹具：SB 为簇代表（聚入 SA），SC 单会话；SA 默认应隐藏 */
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
    meta: { topics: 1, cards: 1, keywords: 1, auto_hubs: 1,
      sessions: { total: 5, user_initiated: 3, filtered: 2,
        drawn: 3, truncated: false },
      clusters: 2, clusters_multi: 1, merged_into_rep: 1,
      kw_hit_counts: { "部署": 1 }, kw_hit_capped: [],
      cards_unlinked: 0, caps: { sessions: 1500, kw_hits: 200 },
      criteria: { user_initiated: "首条消息 role='user'",
        cluster: "标题 token Jaccard≥0.45 或共享定稿关键词",
        representative: "簇内 updated_at 最新" },
      derivation: "机械推导" },
    hints: [],
  };

  /* ① 组装渲染：作业轮询 → canvas 显示 + bar 聚合账 */
  runNext = [{ job_id: "j1" }];
  jobNext = [JOB_DONE(FIX)];
  await loadKgraph();
  check("作业轮询走了 /api/actions/run + /api/actions/job（params 空对象）",
    runCalls.length === 1 && runCalls[0].action_id === "kgraph" &&
    JSON.stringify(runCalls[0].params) === "{}" &&
    jobCalls.length === 1 && jobCalls[0] === "/api/actions/job/j1");
  check("canvas 显示 + 消息清空 + 邻接表建立",
    $("kg-canvas").style.display !== "none" &&
    $("kg-msg").innerHTML === "" &&
    (kgAdj["k:部署"] instanceof Set) && kgAdj["k:部署"].has("s:SB") &&
    kgAdj["k:部署"].has("t:t1"));
  check("bar 聚合账：会话节点只数代表层/簇代表/聚入/用户发起/显示边",
    $("kg-bar").textContent.includes("会话节点 2") &&
    $("kg-bar").textContent.includes("含 1 个簇代表，1 会话已聚入，点代表展开") &&
    $("kg-bar").textContent.includes("用户发起 3/5") &&
    $("kg-bar").textContent.includes("滤掉 2 非用户发起") &&
    $("kg-bar").textContent.includes("显示边 6") &&
    $("kg-bar").textContent.includes("自动枢纽 1"),
    $("kg-bar").textContent);
  check("kgData 与图例同源（节点 7 边 6）",
    kgData.nodes.length === 7 && kgData.edges.length === 6);

  /* ② 聚合显隐（用户裁决③核心）：默认藏成员，点代表展开/收起 */
  check("默认可见集：代表+单会话+非会话层；聚入成员 SA 隐藏",
    kgVisible().has("s:SB") && kgVisible().has("s:SC") &&
    kgVisible().has("t:t1") && kgVisible().has("c:c1.md") &&
    kgVisible().has("k:部署") && kgVisible().has("k:描写") &&
    !kgVisible().has("s:SA"));
  const repNode = kgIndex["s:SB"];
  kgCanvasClick({ offsetX: kgPos[repNode.id].x,
    offsetY: kgPos[repNode.id].y });
  check("点簇代表：kgOpened 记账 + 成员 SA 展开（数据面已下发，零等待）",
    kgOpened.has("s:SB") && kgVisible().has("s:SA") &&
    $("kg-bar").textContent.includes("会话节点 3"),
    $("kg-bar").textContent);
  kgCanvasClick({ offsetX: kgPos[repNode.id].x,
    offsetY: kgPos[repNode.id].y });
  check("再点簇代表：收起（SA 回隐藏）",
    !kgOpened.has("s:SB") && !kgVisible().has("s:SA"));

  /* ③ 确定性布局：同数据两次坐标逐点一致 */
  const p1 = kgLayout(FIX.nodes, FIX.edges, 900, 620);
  const p2 = kgLayout(FIX.nodes, FIX.edges, 900, 620);
  check("布局确定性（固定种子+自适应迭代）",
    FIX.nodes.every(nd => Math.abs(p1[nd.id].x - p2[nd.id].x) < 1e-9 &&
      Math.abs(p1[nd.id].y - p2[nd.id].y) < 1e-9));

  /* ④ 视图交互：滚轮缩放（鼠标锚点）/拖拽平移/双击复位 */
  const drawBefore = kgDrawCalls;
  kgWheel({ deltaY: -120, offsetX: 450, offsetY: 310,
    preventDefault() {} });
  check("滚轮放大：k 变 1.15 倍，锚点屏幕坐标不动（鼠标为锚）",
    Math.abs(kgView.k - 1.15) < 1e-9 &&
    Math.abs((450 - kgView.x) / kgView.k - (450 - 0) / 1) < 1e-9,
    JSON.stringify(kgView));
  kgWheel({ deltaY: 120, offsetX: 450, offsetY: 310,
    preventDefault() {} });
  check("滚轮缩回：k 回 1，缩放可逆", Math.abs(kgView.k - 1) < 1e-9);
  for (let i = 0; i < 20; i++)
    kgWheel({ deltaY: -120, offsetX: 100, offsetY: 100,
      preventDefault() {} });
  check("缩放封顶 k<=8", kgView.k <= 8 + 1e-9, String(kgView.k));
  kgDblClick();
  check("双击复位视图", kgView.k === 1 && kgView.x === 0 && kgView.y === 0);
  kgMouseDown({ offsetX: 400, offsetY: 300 });
  kgMouseMove({ offsetX: 460, offsetY: 340 });
  kgMouseUp();
  check("拖拽平移：视图原点跟随位移",
    kgView.x === 60 && kgView.y === 40, JSON.stringify(kgView));
  kgMouseDown({ offsetX: 400, offsetY: 300 });
  kgMouseMove({ offsetX: 402, offsetY: 301 });
  kgMouseUp();
  check("原地点击（位移<3px）不改视图",
    kgView.x === 60 && kgView.y === 40, JSON.stringify(kgView));
  kgDblClick();
  check("缩放平移后 kgDraw 确实被反复调用", kgDrawCalls > drawBefore);

  /* ⑤ hover 邻域：代表提示簇展开；主题点击锁定邻域 */
  kgHover({ offsetX: kgPos["s:SB"].x, offsetY: kgPos["s:SB"].y });
  check("hover 簇代表：设 kgHoverId + tip 提示点展开/收起簇（2 会话）",
    kgHoverId === "s:SB" &&
    $("kg-tip").innerHTML.includes("点展开/收起簇（2 会话）"));
  kgHover({ offsetX: kgPos["s:SC"].x, offsetY: kgPos["s:SC"].y });
  check("hover 单会话：无簇提示（直达原文，不误导展开）",
    kgHoverId === "s:SC" &&
    !$("kg-tip").innerHTML.includes("点展开/收起簇"));
  const kwNode = kgIndex["k:部署"];
  const kwScr = { x: kgPos[kwNode.id].x, y: kgPos[kwNode.id].y };
  kgHover({ offsetX: kwScr.x, offsetY: kwScr.y });
  check("hover 关键词：tip 显示度数",
    kgHoverId === "k:部署" &&
    $("kg-tip").innerHTML.includes("部署") &&
    $("kg-tip").innerHTML.includes("2 条边"));
  const tNode = kgIndex["t:t1"];
  kgCanvasClick({ offsetX: kgPos[tNode.id].x, offsetY: kgPos[tNode.id].y });
  check("点主题：锁定邻域 kgPinId（不臆造跳转）",
    kgPinId === "t:t1");
  kgCanvasClick({ offsetX: kgPos[tNode.id].x, offsetY: kgPos[tNode.id].y });
  check("再点同节点：解锁", kgPinId === null);
  kgCanvasClick({ offsetX: 5, offsetY: 5 });
  check("点空白：不误设 pin", kgPinId === null);

  /* ⑥ 下钻分流：单会话/成员直达原文；关键词灌检索；代表只展开 */
  kgCanvasClick({ offsetX: kgPos["s:SC"].x, offsetY: kgPos["s:SC"].y });
  check("单会话节点直达原文（openSession(SC)）",
    sessCalls === 10 && sessSids[0] === "SC");
  kgCanvasClick({ offsetX: kgPos[repNode.id].x,
    offsetY: kgPos[repNode.id].y });           // 展开（这次不收起）
  const kwScr2 = { x: kgPos[kwNode.id].x,      // 展开触发重排，坐标重取
    y: kgPos[kwNode.id].y };
  kgCanvasClick({ offsetX: kgPos["s:SA"].x, offsetY: kgPos["s:SA"].y });
  check("已展开成员直达原文（openSession(SA)）",
    sessSids[1] === "SA" && kgOpened.has("s:SB"));
  kgCanvasClick({ offsetX: kwScr2.x, offsetY: kwScr2.y });
  check("关键词下钻：switchTab(sessions)+state.q+loadSessions（不发动作）",
    tabCalls.includes("sessions") && state.q === "部署" &&
    $("f-q").value === "部署" && sessCalls === 21);

  /* ⑦ 截断账：meta.sessions.truncated → bar 如实标注 */
  renderKgraph({ ...FIX, meta: { ...FIX.meta,
    sessions: { ...FIX.meta.sessions, total: 900, truncated: true } } });
  check("截断时 bar 标注「超上限只画最新」",
    $("kg-bar").textContent.includes("用户发起 3/900") &&
    $("kg-bar").textContent.includes("超上限只画最新"),
    $("kg-bar").textContent);

  /* ⑧ 诚实空态：hints 原样 + canvas 隐藏 */
  renderKgraph({ nodes: [], edges: [],
    hints: ["topics_meta 未配置或不存在：起服时加 --topics-meta"] });
  check("空态 hints 原样渲染 + canvas 隐藏",
    $("kg-msg").innerHTML.includes("topics_meta 未配置") &&
    $("kg-canvas").style.display === "none" &&
    !$("kg-msg").innerHTML.includes("undefined"));

  /* ⑨ 动作失败不静默 */
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
