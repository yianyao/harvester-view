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

/* echarts 桩：记录 setOption/on/resize（getWidth/getHeight 是 P10 加的真接口：
   kgResize 靠它们判断"画布尺寸与容器是否一致"，桩里必须像真的一样给数）。 */
function mkFakeChart() {
  const calls = [];
  const chart = {
    _calls: calls,
    _on: {},
    _w: 900,
    _h: 620,
    on(ev, fn) { chart._on[ev] = fn; },
    setOption(opt, opts) { calls.push({ opt, opts }); },
    resize() { chart.resized = (chart.resized || 0) + 1; },
    getWidth() { return chart._w; },
    getHeight() { return chart._h; },
  };
  return chart;
}
global.echarts = { init: () => { throw new Error("桩: init 不应在页面域被调"); } };

const FN_SRC = [
  extract("kgVisible", ""), extract("kgAdjBuild", ""),
  extract("kgBar", ""), extract("kgOption", ""),
  extract("kgRender", ""), extract("kgFitView", ""),
  extract("kgCanvasClick", "p"), extract("kgOpenSession", "sid"),
  extract("kgEnsureChart", ""), extract("kgResize", ""),
  extract("kgWatchResize", ""), extract("renderKgraph", "d"),
  extract("loadKgraph", ""), extract("kgWaitJob", "jid"),
  extract("kgNodePixels", ""), extract("kgBrushPick", "x0,y0,x1,y1"),
  extract("kgBrushApply", "sel"),
].join("\n");

/* eval 域：页面 let 变量在包装体内声明（赋值留在闭包里可读回） */
function mkScope(env) {
  const factory = new Function(
    "$", "esc", "errorBox", "actJson", "actApi", "switchTab",
    "openSession", "jumpCloudWord",
    "KG_KIND_COLOR", "KG_KIND_SIZE", "KG_KIND_NAME", "KG_EDGE_LABEL",
    "let kgData=null,kgIndex={},kgAdj={},kgPinId=null;\n" +
    "let kgQuery=\"\",kgDeb=null,kgTopicId=null;\n" +
    "let kgChart=null;\n" +
    "let kgBrushSel=null,kgWeakTopics=0;\n" +
    FN_SRC + "\n" +
    "return {get kgData(){return kgData;}, set kgData(v){kgData=v;},\n" +
    "  get kgPinId(){return kgPinId;},\n" +
    "  get kgChart(){return kgChart;}, set kgChart(v){kgChart=v;},\n" +
    "  get kgQuery(){return kgQuery;}, set kgQuery(v){kgQuery=v;},\n" +
    "  get kgTopicId(){return kgTopicId;},\n" +
    "  set kgTopicId(v){kgTopicId=v;},\n" +
    "  get kgBrushSel(){return kgBrushSel;},\n" +
    "  set kgBrushSel(v){kgBrushSel=v;},\n" +
    "  get kgWeakTopics(){return kgWeakTopics;},\n" +
    "  kgVisible,kgAdjBuild,kgBar,kgOption,kgRender,kgFitView,\n" +
    "  kgCanvasClick,kgOpenSession,kgEnsureChart,kgResize,kgWatchResize,\n" +
    "  renderKgraph,loadKgraph,\n" +
    "  kgWaitJob,kgNodePixels,kgBrushPick,kgBrushApply};");
  return factory(env.$, env.esc, env.errorBox, env.actJson, env.actApi,
    env.switchTab, env.openSession, env.jumpCloudWord,
    env.KG_KIND_COLOR, env.KG_KIND_SIZE, env.KG_KIND_NAME,
    env.KG_EDGE_LABEL);
}

const switchCalls = [], openCalls = [], jumpCalls = [], errBoxes = [];
const actCalls = [];
const scope = mkScope({
  $: $, esc: esc,
  errorBox: s => { errBoxes.push(s); return "<div class='err'>" + s + "</div>"; },
  actJson: async (...a) => { actCalls.push(a);
    return { __err: null, job_id: "J1" }; },
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
  delete global.echarts;   // typeof echarts → undefined（惰性路径 fail loud）
  scope.renderKgraph({ nodes: [{ id: "t1", kind: "topic", label: "甲" }],
    edges: [] });
  check("ECharts 未加载 → fail loud 不静默（惰性路径）",
    errBoxes.some(s => s.includes("ECharts 未加载")));
  check("fail loud 时画布收起（不塌角落）",
    $("kg-chart").style.display === "none");
}

/* ② 组装渲染：force 照搬账 + 两态节点集 */
const chart = mkFakeChart();
scope.kgChart = chart;
const DATA = {
  nodes: [
    { id: "t:t1", kind: "topic", label: "部署排障",
      meta: { topic_id: "t1",
        cluster: ["t:t1", "s:S1", "s:S2", "s:S3"] } },
    { id: "t:t2", kind: "topic", label: "部署规范",
      meta: { cluster: ["t:t2", "s:S9"] } },
    { id: "t:t3", kind: "topic", label: "吾好梦中救人",
      meta: { cluster: ["t:t3", "s:S8"] } },
    { id: "c:c1", kind: "card", label: "部署卡" },
    { id: "k:部署", kind: "kw", label: "部署" },
    { id: "k:孤立", kind: "kw", label: "孤立词" },
    { id: "s:S1", kind: "session", label: "网关重启", meta: { sid: "S1" } },
    { id: "s:S2", kind: "session", label: "买菜清单", meta: { sid: "S2" } },
  ],
  edges: [
    { src: "t:t1", dst: "c:c1", kind: "topic_of" },
    { src: "t:t1", dst: "t:t2", kind: "shared_kw", weight: 2,
      via: ["部署", "网关"] },
    { src: "t:t1", dst: "s:S1", kind: "member" },
    { src: "t:t1", dst: "s:S2", kind: "member" },
    { src: "t:t3", dst: "s:S1", kind: "member" },
    { src: "k:部署", dst: "t:t1", kind: "keyword" },
    { src: "k:部署", dst: "s:S1", kind: "kw_hit" },
    { src: "k:孤立", dst: "s:S2", kind: "kw_hit" },
    { src: "s:S1", dst: "s:S2", kind: "member" },
  ],
  meta: { search: { q: "部署", fts_matched: 2 },
    clusters_multi: 1, merged_into_rep: 2, auto_hubs: 0,
    topic_links: 1, topic_link_excluded_hubs: [],
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
  check("大类态只显主题/卡片",
    ids.includes("t:t1") && ids.includes("t:t2") && ids.includes("t:t3") &&
    ids.includes("c:c1") &&
    !ids.includes("s:S1") && !ids.includes("k:部署"));
  const linkIds = ser.links.map(l => l.source + ">" + l.target);
  check("大类态边只在可见节点间（s1-s2 不画）",
    !linkIds.includes("s:S1>s:S2") && linkIds.includes("t:t1>c:c1"));
  const skLink = ser.links.find(l => l.kind === "shared_kw");
  check("P7-② 主题连线大类态画出+宽度按共享数 1+2*0.3",
    skLink && skLink.source === "t:t1" && skLink.target === "t:t2" &&
    Math.abs(skLink.lineStyle.width - 1.6) < 1e-9);
  const plainC = ser.data.find(n => n.id === "c:c1");
  check("普通卡片气泡走档位表",
    plainC && plainC.symbolSize === KG_KIND_SIZE.card);
}

/* ②' 惰性 init：容器显示后才 echarts.init（display:none 时 init 拿
 * 0×0，力导向塌左上角且画布外无鼠标事件——用户截图实测教训） */
{
  let initSize = null;
  global.echarts = { init: (el) => {
    initSize = { display: el.style.display };
    const c = mkFakeChart();
    c.on = () => {};
    return c;
  } };
  scope.kgChart = null;
  scope.renderKgraph(DATA);
  check("容器显示后才 init（display 拉开再 init）",
    initSize && initSize.display === "");
  check("惰性 init 的实例接管渲染（setOption 已调）",
    scope.kgChart && scope.kgChart._calls.length === 1);
  scope.kgChart = chart;   // 恢复主桩，供后续用例
}

/* ③ 节点账：簇代表气泡 + 名称标记（搜索态全显） */
{
  scope.kgQuery = "部署";
  scope.renderKgraph(DATA);
  chart._calls.length = 0;
  scope.renderKgraph(DATA);
  const ser = chart._calls[0].opt.series[0];
  const ids = ser.data.map(n => n.id);
  /* v0.87.1 搜索态收紧：命中会话+直接边邻居（不二次扩散） */
  check("搜索态：命中会话全显",
    ids.includes("s:S1") && ids.includes("s:S2"));
  check("搜索态：会话的直接邻居进（主题 t1 / 枢纽 k1 经 kw_hit）",
    ids.includes("t:t1") && ids.includes("k:部署"));
  check("搜索态：隔主题一跳的卡片不进（不二次扩散）",
    !ids.includes("c:c1"));
  const s2links = ser.links.map(l => l.source + ">" + l.target);
  check("搜索态：s1-s2 簇成员边可见", s2links.includes("s:S1>s:S2"));
  check("搜索态：k1-t1 keyword 边两端可见才画",
    s2links.includes("k:部署>t:t1"));
  check("搜索态：c1-t1 边不画（c1 不可见）",
    !s2links.includes("c:c1>t:t1") && !s2links.includes("t:t1>c:c1"));
  /* v0.88 弱命中门槛（用户裁决）：主题需≥2 成员命中才显 */
  check("搜索态：命中≥2 的主题进（t1 有 S1/S2 两成员命中）",
    ids.includes("t:t1"));
  check("搜索态：命中 1 次的弱主题不进（防人名带出大类）",
    !ids.includes("t:t3"));
  check("搜索态：无关关键词经 kw_hit 照进（k:孤立）",
    ids.includes("k:孤立"));
  check("搜索态：弱命中主题账进 bar（不静默）",
    $("kg-bar").textContent.includes("已隐藏 1 个弱命中主题（成员命中<2）"));
  const rep = ser.data.find(n => n.id === "t:t1");
  check("簇代表名称带 (+3)", rep.name.includes("(+3)"));
  check("簇代表气泡 14+√4×3.2（照搬公式）",
    Math.abs(rep.symbolSize - (14 + Math.sqrt(4) * 3.2)) < 1e-9);
  const plain = ser.data.find(n => n.id === "c:c1");
  if (plain) check("普通卡片气泡走档位表",
    plain.symbolSize === KG_KIND_SIZE.card);
  const sess = ser.data.find(n => n.id === "s:S1");
  check("会话节点蓝色", sess.itemStyle.color === KG_KIND_COLOR.session);
  scope.kgQuery = "";

  /* 搜索态有效性守卫：数据不是本关键词的搜索产物时降级大类态——
   * 动作失败/未刷新时大类态旧数据的会话层不得冒充命中集全显 */
  scope.kgQuery = "另一个词";
  scope.renderKgraph(DATA);   // DATA.meta.search.q="部署" ≠ "另一个词"
  const guardIds = chart._calls[chart._calls.length - 1].opt
    .series[0].data.map(n => n.id);
  check("守卫：数据与关键词不符 → 降级大类态（会话不显）",
    !guardIds.includes("s:S1") && guardIds.includes("t:t1"));
  scope.kgQuery = "部署";
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
  scope.kgCanvasClick({ dataType: "node", data: nodeById["s:S1"] });
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
  scope.kgCanvasClick({ dataType: "node", data: nodeById["k:部署"] });
  check("关键词点击灌进检索（搜索态）", jumpCalls.includes("部署"));

  /* ④' 框选（v0.88）：指针手势在浏览器实测，这里测过滤内核
   * （搜索态语境：kgQuery 仍为「部署」） */
  scope.kgBrushApply(new Set(["t:t1", "s:S1", "k:部署"]));
  let brSer = refresh();
  check("框选：只留框内节点",
    brSer.data.length === 3 && brSer.data.every(n =>
      ["t:t1", "s:S1", "k:部署"].includes(n.id)));
  check("框选：只留两端都在框内的边（悬空线不画）",
    brSer.links.length === 3 && brSer.links.every(l =>
      ["k:部署>t:t1", "t:t1>s:S1", "k:部署>s:S1"].includes(
        l.source + ">" + l.target)));
  check("框选：还原按钮亮出",
    els["kg-brush-clear"].style.display === "");
  check("框选：账进 bar", $("kg-bar").textContent.includes("框选子图"));
  scope.kgBrushApply(null);
  brSer = refresh();
  check("框选还原：全量恢复+按钮收起",
    brSer.data.length > 3 &&
    els["kg-brush-clear"].style.display === "none");

  /* v0.89 主题点击=节点名置换进搜索框+带 topic id 重新查询（用户批注
   * ①②）：大类态锁邻域会孤点（邻域全是不可见会话），label 整串 FTS
   * 几乎必 0 命中——成员子图 ∪ FTS 才是用户要的「点大节点看它的图」；
   * 放块尾防 kgQuery 污染上文语境 */
  scope.kgCanvasClick({ dataType: "node", data: nodeById["t:t1"] });
  check("主题点击置换搜索框（input value+kgQuery）",
    $("kg-q").value === "部署排障" && scope.kgQuery === "部署排障");
  check("主题点击带 topic id（成员子图 ∪ FTS，防孤点）",
    actCalls.length === 1 &&
    JSON.stringify(actCalls[0]).includes("kgraph") &&
    JSON.stringify(actCalls[0]).includes('"topic":"t1"'));
  check("主题点击不进邻域锁（kgPinId 保持空）", scope.kgPinId === null);
  scope.kgQuery = ""; $("kg-q").value = ""; scope.kgTopicId = null;

  /* v0.89 守卫扩展：meta.search.topic 与 kgTopicId 一致也算真搜索态
   * （label 整串 FTS 0 命中时 sq!==qn，纯 q 守卫会降级大类态孤点）。
   * DATA2=1 成员主题场景：目标主题白名单强制进，成员会话照进 */
  const DATA2 = {
    ...DATA,
    nodes: [...DATA.nodes,
      { id: "s:S9", kind: "session", label: "规范讨论", meta: { sid: "S9" } }],
    edges: [...DATA.edges,
      { src: "t:t2", dst: "s:S9", kind: "member" }],
    meta: { ...DATA.meta,
      search: { q: "", fts_matched: 0, topic: "t2",
        topic_members: 1, member_ids: ["S9"] } },
  };
  scope.kgQuery = "吾好梦中救人";
  scope.kgTopicId = "t2";
  scope.renderKgraph(DATA2);
  const tser = refresh();
  check("主题态守卫：topic 匹配 → 真搜索态（目标主题白名单强制进）",
    tser.data.some(n => n.id === "t:t2"));
  check("主题态：成员会话 s:S9 进（不再孤点）",
    tser.data.some(n => n.id === "s:S9"));
  check("主题态：成员边可见",
    tser.links.some(l => l.source === "t:t2" && l.target === "s:S9"));
  scope.kgQuery = ""; scope.kgTopicId = null;

  /* 卡片点击保留邻域锁（大类态）：邻域外节点直接不画 */
  scope.renderKgraph(DATA);
  refresh();
  scope.kgCanvasClick({ dataType: "node", data: nodeById["c:c1"] });
  check("卡片点击锁定邻域（kgPinId）", scope.kgPinId === "c:c1");
  const pinned = refresh();
  check("邻域锁：邻域外节点直接隐藏（t2 不在 series）",
    !pinned.data.some(n => n.id === "t:t2"));
  check("邻域锁：邻域内节点保留（t1 在且无降透明度）",
    pinned.data.some(n => n.id === "t:t1") &&
    pinned.data.every(n => n.itemStyle.opacity === undefined));
  const hotEdge = pinned.links.find(
    l => (l.source === "t:t1" && l.target === "c:c1"));
  check("邻域边加宽提亮", hotEdge && hotEdge.lineStyle.width === 1.6);
  scope.kgCanvasClick({ dataType: "node", data: nodeById["c:c1"] });
  check("再点同一卡片解锁", scope.kgPinId === null);
  scope.kgCanvasClick({ dataType: "edge", data: {} });
  check("非节点 payload 不误触", scope.kgPinId === null);
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
  check("显示边账进 bar（大类态可见边恰 2：t1>c1、t1~t2）",
    bar.includes("显示边 2"));
}

/* ⑥ 复位＝重跑布局 */
{
  chart._calls.length = 0;
  scope.kgFitView();
  check("复位重跑力导向（setOption+1）", chart._calls.length === 1);
}

/* ⑥b P10（用户 2026-10-24 第 7 条：图谱又全被挤到左边）——
   根因是"画布尺寸与容器不一致时照样铺点"：隐藏/0 宽时 init，之后变宽也不重排。
   两条门：① 容器还没有宽度时**不许建实例**（返回 null，走既有的 fail loud 路）；
   ② 渲染/切页前先把画布对齐容器（resize 真被调用）。 */
{
  const box = els["kg-chart"];
  const savedW = box.clientWidth, savedH = box.clientHeight;
  const scope3 = mkScope({
    $: $, esc: esc, errorBox: s => "<div class='err'>" + s + "</div>",
    actJson: async () => ({}), actApi: async () => ({}),
    switchTab: () => {}, openSession: () => Promise.resolve(),
    jumpCloudWord: () => {},
    KG_KIND_COLOR, KG_KIND_SIZE, KG_KIND_NAME, KG_EDGE_LABEL,
  });
  box.clientWidth = 0; box.clientHeight = 0;
  check("P10 图谱：容器 0 宽时不建实例（不许按 0×0 布局）",
    scope3.kgEnsureChart() === null);
  box.clientWidth = 1200; box.clientHeight = 620;
  const c2 = mkFakeChart();
  c2._w = 300; c2._h = 200;          /* 旧画布尺寸 ≠ 容器 */
  scope3.kgChart = c2;
  check("P10 图谱：尺寸不一致时对齐容器（挤左边就是没对齐）",
    scope3.kgResize() === true && c2.resized === 1);
  c2._w = 1200; c2._h = 620;         /* 真 echarts resize 后自报新尺寸 */
  check("P10 图谱：尺寸已一致时不重复 resize（省一次重排）",
    scope3.kgResize() === false && c2.resized === 1);
  box.clientWidth = savedW; box.clientHeight = savedH;
}

/* ⑦ 动作失败不静默（loadKgraph 管线） */
(async () => {
  /* 排干 ④ 块主题点击触发的 loadKgraph 残留微任务链（其空结果
   * renderKgraph 晚于本块 errorBox 落地会覆盖 kg-msg） */
  await new Promise(r => setImmediate(r));
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
