/*
 * render_kgraph.js -- 知识图谱渲染门（v0.87 照搬 KnowAtlas：ECharts
 * graph force）（DOM 桩 + echarts 桩）。
 *
 * 2026-10-20 用户裁决「已读了人家的实现（KnowAtlas graph.html），就照
 * 搬吧」后重写：渲染器换 ECharts，布局/边/交互参数照搬参考实现，功能
 * 底座（两态模型/正文 FTS 搜索/诚实账/黑底/会话蓝）不变。验：
 *   ① 整页 script 语法解析 + vendor 外链 + echarts.init 接线；
 *   ② 组装渲染：A 档动作 kgraph 组装 → kgRender setOption（graph force
 *     照搬账：repulsion 90 / edgeLength [30,120] / gravity 0.08 /
 *     curveness 0.1 / emphasis focus adjacency / notMerge）；
 *   ③ 两态显隐：大类态只显主题/卡片；搜索态（kgQuery 非空）全显；
 *   ④ 节点账：会话蓝 #4fb0ff（真源抽取）、簇代表 +N 与气泡 14+√n×3.2；
 *   ⑤ 点击分流：搜索态会话（含簇代表）直达原文 / 关键词灌检索 /
 *     主题卡片邻域锁（kgPinId）；
 *   ⑥ 诚实账（kg-bar：搜索命中/聚簇/枢纽/封顶）；
 *   ⑦ 诚实空态 + ECharts 未加载 fail loud + 动作失败不静默。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

/* ---- 静态账 ---- */
let ok = 0, fail = 0;
function check(name, cond) {
  if (cond) { ok++; console.log("ok - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

/* 整页 script 语法解析（行级替换曾留孤儿 `}`——extract 门测不到） */
const scriptBlocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
check("内联 script 恰 1 块", scriptBlocks.length === 1);
try {
  scriptBlocks.forEach(m => { new Function(m[1]); });
  check("整页内联 script 语法解析", true);
} catch (e) {
  check("整页内联 script 语法解析", false);
  console.log("  语法错误: " + e.message);
}
check("vendor echarts 外链", html.includes(
  '<script src="vendor/echarts.min.js"></script>'));
check("echarts.init 接线", html.includes("echarts.init("));

/* KG 常量从页面真源抽取（防桩漂移） */
function grabConst(name) {
  const re = new RegExp("const " + name + "=\\{[\\s\\S]*?\\};");
  const m = html.match(re);
  if (!m) throw new Error("抓取常量失败: " + name);
  return m[0];
}
const CONST_SRC =
  grabConst("KG_KIND_COLOR") + "\n" + grabConst("KG_KIND_SIZE") + "\n" +
  grabConst("KG_KIND_NAME") + "\n" + grabConst("KG_EDGE_LABEL") + "\n";
const CONST_SCOPE = new Function(CONST_SRC +
  ";return {KG_KIND_COLOR,KG_KIND_SIZE,KG_KIND_NAME,KG_EDGE_LABEL};");
const { KG_KIND_COLOR, KG_KIND_SIZE, KG_KIND_NAME, KG_EDGE_LABEL } =
  CONST_SCOPE();

check("会话节点蓝色 #4fb0ff（真源）",
  KG_KIND_COLOR.session === "#4fb0ff");
check("旧黄 #ffab70 不残留", !html.includes("#ffab70"));

/* ---- 函数抽取 + 桩环境 ---- */
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
    dataset: {}, className: "", value: "",
    querySelector() { return mkEl(id + ">*"); },
    querySelectorAll() { return []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }

/* echarts 桩：记录 setOption/on/resize */
function mkFakeChart() {
  const calls = [];
  const chart = {
    _calls: calls,
    _on: {},
    on(ev, fn) { chart._on[ev] = fn; },
    setOption(opt, opts) { calls.push({ opt, opts }); },
    resize() { chart.resized = true; },
  };
  return chart;
}
global.echarts = { init: () => { throw new Error("桩: init 不应在页面域被调"); } };

const FN_SRC = [
  extract("kgVisible", ""), extract("kgAdjBuild", ""),
  extract("kgBar", ""), extract("kgOption", ""),
  extract("kgRender", ""), extract("kgFitView", ""),
  extract("kgCanvasClick", "p"), extract("kgOpenSession", "sid"),
  extract("renderKgraph", "d"),
  extract("loadKgraph", ""), extract("kgWaitJob", "jid"),
].join("\n");

/* eval 域：页面 let 变量在包装体内声明（赋值留在闭包里可读回） */
function mkScope(env) {
  const factory = new Function(
    "$", "esc", "errorBox", "actJson", "actApi", "switchTab",
    "openSession", "jumpCloudWord",
    "KG_KIND_COLOR", "KG_KIND_SIZE", "KG_KIND_NAME", "KG_EDGE_LABEL",
    "let kgData=null,kgIndex={},kgAdj={},kgPinId=null;\n" +
    "let kgQuery=\"\",kgDeb=null;\n" +
    "let kgChart=null;\n" +
    FN_SRC + "\n" +
    "return {get kgData(){return kgData;}, set kgData(v){kgData=v;},\n" +
    "  get kgPinId(){return kgPinId;},\n" +
    "  get kgChart(){return kgChart;}, set kgChart(v){kgChart=v;},\n" +
    "  get kgQuery(){return kgQuery;}, set kgQuery(v){kgQuery=v;},\n" +
    "  kgVisible,kgAdjBuild,kgBar,kgOption,kgRender,kgFitView,\n" +
    "  kgCanvasClick,kgOpenSession,renderKgraph,loadKgraph,kgWaitJob};");
  return factory(env.$, env.esc, env.errorBox, env.actJson, env.actApi,
    env.switchTab, env.openSession, env.jumpCloudWord,
    env.KG_KIND_COLOR, env.KG_KIND_SIZE, env.KG_KIND_NAME,
    env.KG_EDGE_LABEL);
}

const switchCalls = [], openCalls = [], jumpCalls = [], errBoxes = [];
const scope = mkScope({
  $: $, esc: esc,
  errorBox: s => { errBoxes.push(s); return "<div class='err'>" + s + "</div>"; },
  actJson: async () => ({ __err: null, job_id: "J1" }),
  actApi: async () => ({ status: "done", result: { nodes: [], edges: [] } }),
  switchTab: t => switchCalls.push(t),
  openSession: s => { openCalls.push(s); return Promise.resolve(); },
  jumpCloudWord: w => jumpCalls.push(w),
  KG_KIND_COLOR, KG_KIND_SIZE, KG_KIND_NAME, KG_EDGE_LABEL,
});

/* ---- 动态账 ---- */

/* ① 空态诚实 + ECharts 未加载 fail loud */
{
  const el = $("kg-msg");
  scope.renderKgraph({ nodes: [], edges: [], hints: ["真库还没注册主题"] });
  check("空数据诚实空态（hints 全显）",
    el.innerHTML.includes("真库还没注册主题"));
  check("空数据 bar 清空", $("kg-bar").textContent === "");

  scope.kgChart = null;
  scope.renderKgraph({ nodes: [{ id: "t1", kind: "topic", label: "甲" }],
    edges: [] });
  check("ECharts 未加载 → fail loud 不静默",
    errBoxes.some(s => s.includes("ECharts 未加载")));
}

/* ② 组装渲染：force 照搬账 + 两态节点集 */
const chart = mkFakeChart();
scope.kgChart = chart;
const DATA = {
  nodes: [
    { id: "t1", kind: "topic", label: "部署排障",
      meta: { cluster: ["t1", "s1", "s2", "s3"] } },
    { id: "c1", kind: "card", label: "部署卡" },
    { id: "k1", kind: "kw", label: "部署" },
    { id: "s1", kind: "session", label: "网关重启", meta: { sid: "S1" } },
    { id: "s2", kind: "session", label: "买菜清单", meta: { sid: "S2" } },
  ],
  edges: [
    { src: "t1", dst: "c1", kind: "topic_of" },
    { src: "t1", dst: "s1", kind: "member" },
    { src: "k1", dst: "t1", kind: "keyword" },
    { src: "s1", dst: "s2", kind: "member" },
  ],
  meta: { search: { q: "部署", fts_matched: 2 },
    clusters_multi: 1, merged_into_rep: 2, auto_hubs: 0,
    sessions: { user_initiated: 2, total: 3, filtered: 1 } },
  hints: [],
};
els["kg-msg"].innerHTML = "";
scope.renderKgraph(DATA);

{
  const calls = chart._calls;
  check("setOption 恰一次（组装）", calls.length === 1);
  const opt = calls[0].opt;
  const ser = opt.series && opt.series[0];
  check("series type=graph layout=force（照搬）",
    ser.type === "graph" && ser.layout === "force");
  check("force 参数照搬 repulsion90/[30,120]/0.08",
    ser.force.repulsion === 90 && ser.force.edgeLength[0] === 30 &&
    ser.force.edgeLength[1] === 120 && ser.force.gravity === 0.08);
  check("边 curveness 0.1 + emphasis adjacency（照搬）",
    ser.links.every(l => l.lineStyle.curveness === 0.1) &&
    ser.emphasis.focus === "adjacency");
  check("notMerge 整体替换（防残留）",
    calls[0].opts && calls[0].opts.notMerge === true);

  /* 大类态：只发 topic/card 进 series（会话/关键词不画） */
  const ids = ser.data.map(n => n.id);
  check("大类态只显主题/卡片", ids.includes("t1") && ids.includes("c1") &&
    !ids.includes("s1") && !ids.includes("k1"));
  const linkIds = ser.links.map(l => l.source + ">" + l.target);
  check("大类态边只在可见节点间（s1-s2 不画）",
    !linkIds.includes("s1>s2") && linkIds.includes("t1>c1"));
}

/* ③ 节点账：簇代表气泡 + 名称标记（搜索态全显） */
{
  scope.kgQuery = "部署";
  scope.renderKgraph(DATA);
  chart._calls.length = 0;
  scope.renderKgraph(DATA);
  const ser = chart._calls[0].opt.series[0];
  const ids = ser.data.map(n => n.id);
  check("搜索态全显（含会话/关键词）",
    ids.includes("s1") && ids.includes("s2") && ids.includes("k1"));
  const rep = ser.data.find(n => n.id === "t1");
  check("簇代表名称带 (+3)", rep.name.includes("(+3)"));
  check("簇代表气泡 14+√4×3.2（照搬公式）",
    Math.abs(rep.symbolSize - (14 + Math.sqrt(4) * 3.2)) < 1e-9);
  const plain = ser.data.find(n => n.id === "c1");
  check("普通卡片气泡走档位表", plain.symbolSize === KG_KIND_SIZE.card);
  const sess = ser.data.find(n => n.id === "s1");
  check("会话节点蓝色", sess.itemStyle.color === KG_KIND_COLOR.session);
  scope.kgQuery = "";
}

/* ④ 点击分流（ECharts payload 形态） */
{
  let nodeById = {};
  const refresh = () => {
    const ser = chart._calls[chart._calls.length - 1].opt.series[0];
    nodeById = {};
    ser.data.forEach(n => { nodeById[n.id] = n; });
    return ser;
  };
  /* 大类态无会话可点——用搜索态验证会话直达原文 */
  scope.kgQuery = "部署";
  scope.renderKgraph(DATA);
  refresh();
  const before = chart._calls.length;
  scope.kgCanvasClick({ dataType: "node", data: nodeById["s1"] });
  check("搜索态会话点击直达原文（switchTab+openSession）",
    openCalls.includes("S1") && switchCalls.includes("sessions"));
  check("会话点击不重画（直达原文即返回）",
    chart._calls.length === before);
  scope.kgQuery = "";
  scope.renderKgraph(DATA);
  refresh();

  /* kw 点击与邻域锁需搜索态（大类态只显 topic/card，无 kw/会话节点） */
  scope.kgQuery = "部署";
  scope.renderKgraph(DATA);
  refresh();
  scope.kgCanvasClick({ dataType: "node", data: nodeById["k1"] });
  check("关键词点击灌进检索（搜索态）", jumpCalls.includes("部署"));

  scope.kgCanvasClick({ dataType: "node", data: nodeById["t1"] });
  check("主题点击锁定邻域（kgPinId）", scope.kgPinId === "t1");
  const dimmed = refresh().data.find(n => n.id === "s2");
  check("邻域外节点降透明度", dimmed && dimmed.itemStyle.opacity === 0.15);
  const hotEdge = refresh().links.find(
    l => (l.source === "t1" && l.target === "s1"));
  check("邻域边加宽提亮", hotEdge && hotEdge.lineStyle.width === 1.6);
  scope.kgCanvasClick({ dataType: "node", data: nodeById["t1"] });
  check("再点同一主题解锁", scope.kgPinId === null);
  scope.kgCanvasClick({ dataType: "edge", data: {} });
  check("非节点 payload 不误触", scope.kgPinId === null);
  scope.kgQuery = "";
}

/* ⑤ 诚实账 kg-bar */
{
  scope.renderKgraph(DATA);
  const bar = $("kg-bar").textContent;
  check("搜索命中账进 bar", bar.includes("搜索 '部署'：命中 2 会话"));
  check("聚簇账进 bar", bar.includes("1 个簇代表") &&
    bar.includes("2 会话已聚入"));
  check("用户发起过滤账进 bar", bar.includes("用户发起 2/3") &&
    bar.includes("滤掉 1 非用户发起"));
  check("显示边账进 bar（大类态可见边恰 1：t1>c1）",
    bar.includes("显示边 1"));
}

/* ⑥ 复位＝重跑布局 */
{
  chart._calls.length = 0;
  scope.kgFitView();
  check("复位重跑力导向（setOption+1）", chart._calls.length === 1);
}

/* ⑦ 动作失败不静默（loadKgraph 管线） */
(async () => {
  els["kg-msg"].innerHTML = "";
  const scope2 = mkScope({
    $: $, esc: esc,
    errorBox: s => "<div class='err'>" + s + "</div>",
    actJson: async () => ({ __err: "上游不可达" }),
    actApi: async () => ({}),
    switchTab: () => {}, openSession: () => Promise.resolve(),
    jumpCloudWord: () => {},
    KG_KIND_COLOR, KG_KIND_SIZE, KG_KIND_NAME, KG_EDGE_LABEL,
  });
  scope2.kgChart = mkFakeChart();
  await scope2.loadKgraph().catch(() => {});
  check("动作入队失败不静默（errorBox）",
    $("kg-msg").innerHTML.includes("图谱入队失败"));

  console.log(fail === 0
    ? "\nALL " + ok + " CHECKS PASSED"
    : "\n" + fail + " CHECKS FAILED / " + ok + " passed");
  process.exit(fail === 0 ? 0 : 1);
})();
