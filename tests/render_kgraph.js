/*
 * render_kgraph.js -- 知识图谱（v0.82，机械关系图）渲染门（DOM 桩，同 render_wordcloud 模式）。
 *
 * 覆盖提案（PROPOSAL-knowledge-graph-v1）渲染行为：
 *   ① 组装渲染：A 档动作 kgraph 作业轮询 → canvas 显示 + bar 组装账
 *     （主题/卡片/关键词/未入主题会话计数，全来自 meta，不编数）；
 *   ② 确定性布局：同数据两次 kgLayout 坐标逐点一致（固定种子+迭代上限）；
 *   ③ 懒加载纪律：点主题走 kgExpandNode（带 sids 的第二次动作）→ 会话
 *     节点 merge；点关键词走 jumpCloudWord 真检索通路；空展开诚实提示；
 *   ④ 诚实空态：hints 原样渲染 + canvas 隐藏，不编节点；
 *   ⑤ 动作失败：__err 不静默。
 *
 * 纪律：只渲染服务端给的内容；本门不验证画布像素（Puppeteer 级视觉验收
 * 不在 view 渲染门体系内），验证的是渲染逻辑与数据纪律。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

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
    width: 900, height: 560, clientWidth: 900,
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
 * （与 index.html 的值一致；漂移时 test_v66 静态账会先红）。 */
let kgData = null, kgPos = {}, kgIndex = {}, kgExpanded = {};
const KG_KIND_COLOR = { topic: "#3b74c4", card: "#8a5fc0", kw: "#2f9e77",
  session: "#c4703b" };
const KG_KIND_SIZE = { topic: 13, card: 9, kw: 5, session: 6 };
const KG_KIND_NAME = { topic: "主题", card: "卡片", kw: "关键词",
  session: "会话" };
const KG_EDGE_LABEL = { member: "成员", anchor: "锚点", topic_of: "归属",
  keyword: "关键词" };

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
async function loadSessions() { sessCalls++; }
async function openSession(sid) { sessCalls += 10; sessSids.push(sid); }
const sessSids = [];
global.confirm = () => true;
/* 画布绘制不在 DOM 桩能力内：kgDraw 打桩（布局与合并逻辑照测） */
function kgDraw() { kgDrawCalls++; }
let kgDrawCalls = 0;

eval(extract("kgRng", "seed"));
eval(extract("kgLayout", "nodes,edges,W,H"));
eval(extract("kgBar", ""));
eval(extract("loadKgraph", ""));
eval(extract("kgWaitJob", "jid"));
eval(extract("renderKgraph", "d"));
eval(extract("kgRelayout", ""));
eval(extract("kgHit", "x,y"));
eval(extract("kgExpandNode", "id"));
eval(extract("kgMergeExpand", "parent,ekind,res"));
eval(extract("kgReset", ""));
eval(extract("kgCanvasClick", "e"));
eval(extract("kgOpenSession", "sid"));
eval(extract("jumpCloudWord", "w"));

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

const JOB_DONE = (result) => ({ status: "done", result });

(async () => {
  const FIX = {
    nodes: [
      { id: "t:t1", kind: "topic", label: "部署排障", badge: "1成员",
        meta: { topic_id: "t1", member_sids: ["S1"],
          keywords: ["部署", "排障"] } },
      { id: "c:c1.md", kind: "card", label: "部署卡", badge: "summary",
        meta: { path: "c1.md", anchor_sids: ["S1"] } },
      { id: "k:部署", kind: "kw", label: "部署", badge: "", meta: {} },
      { id: "k:排障", kind: "kw", label: "排障", badge: "", meta: {} },
    ],
    edges: [
      { src: "c:c1.md", dst: "t:t1", kind: "topic_of" },
      { src: "k:部署", dst: "t:t1", kind: "keyword" },
      { src: "k:排障", dst: "t:t1", kind: "keyword" },
    ],
    meta: { topics: 1, cards: 1, keywords: 2, sessions_total: 3,
      sessions_in_topics: 1, sessions_unlinked: 2, cards_unlinked: 0,
      derivation: "机械推导" },
    hints: [],
  };

  /* ① 组装渲染：作业轮询 → canvas 显示 + bar 账 */
  runNext = [{ job_id: "j1" }];
  jobNext = [JOB_DONE(FIX)];
  await loadKgraph();
  check("作业轮询走了 /api/actions/run + /api/actions/job",
    runCalls.length === 1 && runCalls[0].action_id === "kgraph" &&
    jobCalls.length === 1 && jobCalls[0] === "/api/actions/job/j1");
  check("canvas 显示 + 消息清空",
    $("kg-canvas").style.display !== "none" &&
    $("kg-msg").innerHTML === "");
  check("bar 组装账含主题/卡片/关键词/未入主题计数",
    /主题 1/.test($("kg-bar").textContent) &&
    /卡片 1/.test($("kg-bar").textContent) &&
    /关键词 2/.test($("kg-bar").textContent) &&
    $("kg-bar").textContent.includes("未入主题的会话 2 个不画"),
    $("kg-bar").textContent);
  check("kgData 与图例同源（节点 4 边 3）",
    kgData.nodes.length === 4 && kgData.edges.length === 3);

  /* ② 确定性布局：同数据两次坐标逐点一致 */
  const p1 = kgLayout(FIX.nodes, FIX.edges, 900, 560);
  const p2 = kgLayout(FIX.nodes, FIX.edges, 900, 560);
  check("布局确定性（固定种子+迭代上限）",
    FIX.nodes.every(nd => Math.abs(p1[nd.id].x - p2[nd.id].x) < 1e-9 &&
      Math.abs(p1[nd.id].y - p2[nd.id].y) < 1e-9));

  /* ③ 懒加载展开：点主题 → 第二次动作带 sids → 会话节点 merge */
  runNext = [{ job_id: "j2" }];
  jobNext = [JOB_DONE({ sessions: [
      { sid: "S1", title: "部署会话", source: "trae",
        updated_at: "2026-10-19T00:00:00", error_count: 1 },
      { sid: "S2", title: "无关会话", source: "claude",
        updated_at: "2026-10-19T00:00:00", error_count: 0 },
    ], missing: ["SX"], hints: [] })];
  await kgExpandNode("t:t1");
  check("展开动作带 sids（懒加载，非预画）",
    runCalls.length === 2 && runCalls[1].params.sids === "S1",
    JSON.stringify(runCalls[1] || {}));
  const sNode = kgIndex["s:S1"];
  check("会话节点 merge 进图（kind=session）",
    !!sNode && sNode.kind === "session" &&
    kgData.nodes.length === 6);
  check("会话边 kind=member（主题→成员）",
    kgData.edges.some(e => e.src === "t:t1" && e.dst === "s:S1" &&
      e.kind === "member"));
  check("missing 如实提示（不编节点）",
    $("kg-msg").innerHTML.includes("1 个 sid 在库里没有"));
  check("展开后 kg-reset 可用", $("kg-reset").disabled === false);
  const sPos1 = JSON.stringify(kgPos["s:S1"]);

  /* ③b 点关键词 → 灌会话检索（真检索通路） */
  const kwNode = kgIndex["k:部署"];
  const before = runCalls.length;
  kgCanvasClick({ offsetX: kgPos[kwNode.id].x, offsetY: kgPos[kwNode.id].y });
  check("关键词下钻：switchTab(sessions)+state.q+loadSessions",
    tabCalls.includes("sessions") && state.q === "部署" &&
    $("f-q").value === "部署" && sessCalls === 1 &&
    runCalls.length === before);

  /* ③c 点会话节点 → 直达回合原文（openSession） */
  sessCalls = 0;
  kgCanvasClick({ offsetX: kgPos["s:S1"].x, offsetY: kgPos["s:S1"].y });
  check("会话节点直达原文（openSession(S1)）",
    sessCalls === 10 && sessSids[0] === "S1");

  /* ③d 已展开节点防重：提示不重复动作 */
  const beforeRuns = runCalls.length;
  await kgExpandNode("t:t1");
  check("已展开节点不重复发动作（banner 提示）",
    runCalls.length === beforeRuns && banners.some(b => b.includes("已展开")));

  /* ③e 空展开诚实：该批会话一个都没找到 */
  runNext = [{ job_id: "j3" }];
  jobNext = [JOB_DONE({ sessions: [], missing: ["S9"], hints: [] })];
  await kgExpandNode("c:c1.md");
  check("空展开诚实提示（一个都没找到）",
    $("kg-msg").innerHTML.includes("一个都没找到"));

  /* ④ 收起会话层：session 节点/边清掉，基础层不动 */
  kgReset();
  check("收起：会话节点清空、基础层保留、reset 复禁用",
    kgData.nodes.length === 4 && kgData.edges.length === 3 &&
    !kgIndex["s:S1"] && $("kg-reset").disabled === true);

  /* ⑤ 诚实空态：hints 原样 + canvas 隐藏 */
  renderKgraph({ nodes: [], edges: [],
    hints: ["topics_meta 未配置或不存在：起服时加 --topics-meta"] });
  check("空态 hints 原样渲染 + canvas 隐藏",
    $("kg-msg").innerHTML.includes("topics_meta 未配置") &&
    $("kg-canvas").style.display === "none" &&
    !$("kg-msg").innerHTML.includes("undefined"));

  /* ⑥ 动作失败不静默 */
  runNext = [new Error("连接中断")];
  await loadKgraph();
  check("入队失败有错误空态", $("kg-msg").innerHTML.includes("连接中断"));
  runNext = [{ job_id: "j4" }];
  jobNext = [{ status: "failed", error: "真库不可读" }];
  await loadKgraph();
  check("作业失败有错误空态", $("kg-msg").innerHTML.includes("真库不可读"));

  if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
  console.log("ALL KGRAPH CHECKS PASSED");
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
