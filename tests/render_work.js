/* 处理单渲染门（v0.49；node 运行，退出码 0=全过）。
 *
 * 用户 2026-10-13 需求原文：
 *   「跳转到的页面希望**只显示需处理的记录**，并有处理建议，能打包处理命令且
 *     可直接跳转到命令台。」
 *   「工作台中闭环健康项，也希望能如上述队列一样需求（当前不能跳转，也无建议）」
 *
 * 本门 extract 处理单的渲染函数**真执行**，钉住四件事：
 *  ① 只显示服务端给的那一批（条数与数字一致；对账断了要在页面上响亮报警，
 *     而不是安安静静把不完整的清单端上来）；
 *  ② 每条记录都带处理建议；
 *  ③ 命令包能把「包级命令 + 每条明细自带的命令」摊平成一份清单，并且
 *     **可复制**（有 cli 文本）；带 action_id 的能一键送进命令台（预填参数）；
 *  ④ 闭环健康的段与队列走**同一条动线**（同一批渲染函数 + target 语义）。
 *
 * 为什么不能只查源码字符串：上两轮的教训（SOP §5 第 5 条）——只看字符串抓不到
 * 执行路径上的缺陷（"工作台永远加载中"就是这么漏过去的）。故这里真跑函数。
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
function mkEl() {
  return { innerHTML: "", textContent: "", style: {}, disabled: false,
    value: "", dataset: {}, className: "", type: "", tagName: "DIV",
    querySelectorAll: () => [], addEventListener: () => {},
    classList: { toggle: () => {} } };
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl(); return els[id]; }
function errorBox(m) { return "<div class='empty'>" + esc(m) + "</div>"; }
const banners = [];
function showBanner(k, m) { banners.push([k, m]); }
const tabCalls = [];
function switchTab(n) { tabCalls.push(n); }
function loadWork() { return { catch: () => {} }; }
function anchor(sid, seq) { return "<a class='anchor' data-sid='" + sid +
  "' data-seq='" + seq + "'>" + sid + "#" + seq + "</a>"; }
function bindAnchors() {}
const copied = [];
function copyText(t, btn) { copied.push(t); if (btn) btn.textContent = "已复制"; }
/* 命令台侧：openInCmd 依赖的四个符号，门里给最小实现并记录调用 */
const cmdState = { actionId: null, cmdParams: {}, cmdCard: null, chain: [] };
const state = { act: { loaded: true, actions: [
  { action_id: "suggest_status", cmd: "suggest-status", tier: "B",
    summary: "在建议台账里把一条建议的状态改为 {status}" },
] }, loaded: {}, cmdCard: null, actionId: null, cmdParams: {}, chain: [] };
function actionSpec(aid) {
  return (state.act.actions || []).find(a => a.action_id === aid) || null;
}
function cmdCommands() { return [{ cmd: "suggest-status", card_id: "ledger" }]; }
function renderCmdCards() { cmdState.cards = true; }
function renderCmdActions() { cmdState.actions = true; }
function renderCmdParams() { cmdState.params = true; }
function loadCmdData() { return Promise.resolve(null); }

for (const [n, sig] of [
  ["cmdKey", "c"], ["renderWorkHead", "d"], ["workRows", "d"],
  ["renderWorkPkg", "d"],
  ["renderWorkItems", "d"], ["openInCmd", "actionId,params"],
  /* 2026-10-15：命令悬停说明（处理单两处命令框都要有 title）+ 仓根提示 */
  ["stripMd", "s"], ["cmdNameOf", "cli"], ["argsHelp", "c"],
  ["cmdHoverTitle", "row"], ["displayPath", "p"],
]) {
  eval(extract(n, sig));
}

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* 夹具：形状与真 /api/workitems 一致（真数字取自 2026-10-13 实测：
   queue:repair 6 条明细 / 17 次未解决；断言一律从载荷推导，不写死） */
const WI = {
  view_workitems_api: 1,
  target_id: "queue:repair", group: "queue", queue_id: "repair",
  label: "待修补", unit: "条", count: 2, owner: "人（改班规）+ 工具（补 pattern 桥）",
  source: "api_reports_agents.entries[unresolved_count>=1]",
  definition: "建议条目里 unresolved_count>=1 的条数",
  where: "报告页 → 建议条目（G2 建议池）",
  suggestion: "每条都是「班规写了但坑还在」：先点锚点回原文。",
  primary: "entries",
  metrics: [ { name: "entries", label: "待修补条目", mode: "items",
               unit: "条", value: 2 },
             { name: "unresolved", label: "未解决次数合计", mode: "sum",
               unit: "次", value: 17 } ],
  package: [ { label: "跑一次效果评估", note: "两指标都要不升才算改善",
               cli: "python -m harvester harvest-eval --db harvester.db",
               action_id: null, params: {}, tier: null } ],
  reconcile: [ { metric: "entries", mode: "items", expected: 2, actual: 2,
                 by: "len(items)", items_total: 2, shown: 2, truncated: false,
                 ok: true },
               { metric: "unresolved", mode: "sum", expected: 17, actual: 17,
                 by: "sum(item.metrics.unresolved)", items_total: 2, shown: 2,
                 truncated: false, ok: true } ],
  reconcile_ok: true,
  db_fingerprint: { sessions: 1981 },
  items: [
    { item_id: "agent:子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。",
      title: "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。",
      metrics: { count: 6, unresolved: 6 }, level: "bad", owner: "harness",
      facts: [["实测次数", "x6"], ["未解决次数", "6"]],
      anchor: { sid: "dsh:/x/session.v4.jsonl.zstd", seq: 201,
                probe: "Error: subagent depth 2 exceeds maxDepth 1" },
      detail: "subagent depth exceeds maxDepth 时不要继续嵌套。",
      suggestion: "坑还在：先点锚点回原文确认，再补 pattern 桥。",
      commands: [ { label: "在命令台补 pattern 桥（采纳并留痕）",
                    note: "note 里带一行 pattern:", tier: "B",
                    action_id: "suggest_status",
                    params: { key: "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。",
                              status: "adopted" },
                    cli: "python -m harvester suggest-status --meta suggestions_meta.db --key \"子代理…\" --status adopted" } ] },
    { item_id: "agent:权限被撤销的工具不要原地重试。",
      title: "权限被撤销的工具不要原地重试。",
      metrics: { count: 11, unresolved: 5 }, level: "bad", owner: "harness",
      facts: [["未解决次数", "5"]], anchor: null, detail: "",
      suggestion: "先看班规写清没有。", commands: [] },
  ],
};

/* ① 头部：数字 + 每条指标 + 口径 + 真源 + 对账结论 + 处理建议 */
renderWorkHead(WI);
const head = $("work-head").innerHTML;
check("头部显示 label 与数字", head.includes("待修补") &&
  head.includes(">" + WI.count + "<"));
check("头部逐条列出指标（条目数与次数和分开）",
  head.includes("待修补条目：2 条") && head.includes("未解决次数合计：17 次"),
  "同一 target 可有两条口径不同的指标，页面必须都摆出来");
check("头部显示口径与真源", head.includes(esc(WI.definition)) &&
  head.includes(esc(WI.source)));
check("头部显示处理建议（用户需求原文：并有处理建议）",
  head.includes(esc(WI.suggestion)));
check("对账通过时明说「这几条就是这 N 的全部」",
  head.includes("对账通过") && head.includes("2 条就是这 2 的全部"));
check("对账通过时交代派生方式（len(items) / sum(...)）",
  head.includes("entries=len(items)") &&
  head.includes("unresolved=sum(item.metrics.unresolved)"));

/* ①b 对账断了必须响亮报警（这条是"明细对不上"的正面防线） */
const BAD = JSON.parse(JSON.stringify(WI));
BAD.reconcile[1].ok = false;
BAD.reconcile_ok = false;
renderWorkHead(BAD);
const badHead = $("work-head").innerHTML;
check("对账断了 → 页面响亮报警并劝阻派活",
  badHead.includes("对账断了") && badHead.includes("先别照着下面的清单派活"),
  "没有这条，数字与明细不一致时页面会安静地把不完整清单端上来");

/* ② 命令包：包级 + 明细级摊平、可复制、带 action_id 的可送命令台 */
const rows = workRows(WI);
check("workRows 把包级命令与明细自带的命令都摊平",
  rows.length === WI.package.length + 1);
check("摊平后的行保留来源项（知道这条命令是针对哪条的）",
  rows.some(r => r.src === WI.items[0].title));
renderWorkPkg(WI);
const pkg = $("work-pkg").innerHTML;
check("命令包显示条数", pkg.includes("（" + rows.length + " 条命令"));
check("命令行文本逐条渲染（可复制粘贴）",
  pkg.includes(esc(WI.package[0].cli)) &&
  pkg.includes(esc(WI.items[0].commands[0].cli)));
check("写明诚实边界：执行只在冻结白名单内 + B 档两段提交（v0.62 用户修订红线）",
  pkg.includes("冻结白名单") && pkg.includes("先预演、你确认、才写"));
check("有 action_id 的命令给「在命令台打开」按钮",
  pkg.includes("data-wact=\"suggest_status\""));
check("纯 CLI 命令不给假的执行按钮",
  (pkg.match(/data-wact=/g) || []).length === 1);
check("「在命令台打开」带上了预填参数",
  pkg.includes("data-wparams=") && pkg.includes("status"));

/* ②c 悬停说明（用户 2026-10-15 批注：命令最好有个悬停提示其作用——
   所有命令，不仅这一条，也不仅这个页面）+ 真源行白话解释 */
check("命令包每条命令框带悬停说明",
  pkg.includes("class='wi-cmd' title=") &&
  pkg.includes("cd 到 session-harvester"),
  "裸命令行没有解释=谜语；hover 必须给出「干嘛用+怎么用+参数含义」");
check("命令包每条命令带「复制」按钮（用户批注：复制到终端执行的命令要能一键复制）",
  (pkg.match(/data-clicopy=/g) || []).length === rows.filter(r => r.cli).length &&
  pkg.includes("复制命令行（粘贴到终端运行"),
  "每条有 cli 的命令一行一个复制按钮；无 cli 的不给假按钮");
check("命令包写明粘贴位置（仓根，用户在此前的上级目录踩过 No module 坑）",
  pkg.includes("仓根") && pkg.includes("session-harvester"));
renderWorkItems(WI);
const wiItems = $("work-items").innerHTML;
check("明细区命令框同样带悬停说明与复制按钮（不是只有命令包有）",
  wiItems.includes("class='wi-cmd' title=") &&
  wiItems.includes("在命令台打开」就地执行") &&
  wiItems.includes("data-clicopy="));
check("头部真源行带白话解释（机器键是给人对账用的，不是谜语）",
  head.includes("真源 ") && head.includes("跨页对账"));

/* ②b 整批通用命令必须去重（用户 2026-10-14 批注：几个命令从头到脚完全
   一样，需要反反复复执行?）——目录级命令（如 cards validate）逐条明细
   重复出现是数据形状问题，渲染层按 命令行+动作+参数 去重并标注「跑一次」。 */
const DUP = {package: [], items: [
  {item_id: "a", title: "卡片甲", metrics: {count: 1}, facts: [], anchor: null,
   detail: "", suggestion: "", level: "warn", owner: "",
   commands: [{label: "校验卡片目录", note: "目录级", tier: null,
               action_id: null, params: {}, cli: "python -m harvester cards validate --root X"}]},
  {item_id: "b", title: "卡片乙", metrics: {count: 1}, facts: [], anchor: null,
   detail: "", suggestion: "", level: "warn", owner: "",
   commands: [{label: "校验卡片目录", note: "目录级", tier: null,
               action_id: null, params: {}, cli: "python -m harvester cards validate --root X"}]},
  {item_id: "c", title: "卡片丙", metrics: {count: 1}, facts: [], anchor: null,
   detail: "", suggestion: "", level: "warn", owner: "",
   commands: [{label: "校验卡片目录", note: "目录级", tier: null,
               action_id: null, params: {}, cli: "python -m harvester cards validate --root Y"}]},
]};
const drows = workRows(DUP);
check("完全相同的命令去重后只留一条（不同参数的保留）",
  drows.length === 2 && drows[0].same === 2 && drows[1].same === 1,
  "整批通用命令重复 N 遍会让用户以为要跑 N 遍——必须合并成一条");
check("去重行标注「整批通用，跑一次即可」",
  drows.some(r => (r.shared_note||"").includes("跑一次即可")));
renderWorkPkg(DUP);
check("命令包摘要点名合并了几条相同命令",
  $("work-pkg").innerHTML.includes("完全相同的合并"));
renderWorkItems(DUP);
const dupItems = $("work-items").innerHTML;
check("明细里整批通用命令只给提示不重复贴命令行",
  (dupItems.match(/cards validate --root X/g) || []).length === 0 &&
  dupItems.includes("整批通用"),
  "明细区再贴一遍同一条命令=换着地方继续误导（重复的 X 应只在命令包出现一次）");
check("参数不同的同类命令不去重（--root X 与 --root Y 都在）",
  dupItems.includes("--root Y"));

/* ③ 明细区：只渲染服务端那一批 + 每条都带建议 + 锚点可点 */
renderWorkItems(WI);
const items = $("work-items").innerHTML;
check("明细条数与数字一致（只显示需处理的记录）",
  items.includes(WI.items.length + " 条，与上面的数字同源"));
check("每条明细都渲染出处理建议",
  WI.items.every(i => items.includes(esc(i.suggestion))));
check("每条明细都渲染出事实字段（facts）",
  items.includes("未解决次数") && items.includes("x6"));
check("有锚点的明细渲染可点锚点（回得到原文）",
  items.includes("data-sid=") && items.includes("data-seq=") &&
  items.includes("证据锚点"));
check("bad 级明细带 red 左边框（一眼看出哪条更急）",
  items.includes("wi-item bad"));

/* ③b 空明细不许装成"有东西" */
const EMPTY = JSON.parse(JSON.stringify(WI));
EMPTY.count = 0; EMPTY.items = []; EMPTY.package = [];
EMPTY.reconcile = []; EMPTY.reconcile_ok = true;
renderWorkItems(EMPTY);
check("空明细写明「这一队是空的」（不是空白一片）",
  $("work-items").innerHTML.includes("这一队是空的"));
renderWorkPkg(EMPTY);
check("没有命令时命令包明说没有（不是假按钮）",
  $("work-pkg").innerHTML.includes("没有可打包的命令"));

/* ④ 闭环健康的段与队列同动线：同一批渲染函数吃得下 ring 形状 */
const RING = JSON.parse(JSON.stringify(WI));
RING.target_id = "ring:errors.givenup";
RING.group = "ring"; RING.ring_id = "errors";
RING.label = "错误 → 班规 → 效果 · 未解决（模式维度）";
RING.unit = "次"; RING.count = 31;
RING.suggestion = "优先给它们补班规或改工具。";
renderWorkHead(RING);
check("闭环健康段用同一个渲染器（group=ring 也认）",
  $("work-head").innerHTML.includes("闭环健康") &&
  $("work-head").innerHTML.includes("错误 → 班规 → 效果"));
renderWorkItems(RING);
check("闭环健康段的明细同样列出记录",
  $("work-items").innerHTML.includes("与上面的数字同源"));

/* ⑤ openInCmd：真跑「跳到命令台并按 action_id 预填参数」 */
(async () => {
  await openInCmd("suggest_status",
    { key: WI.items[0].title, status: "adopted" });
  check("openInCmd 切到命令台", tabCalls.includes("cmd"));
  check("openInCmd 选中了写动作", state.actionId === "suggest_status");
  check("openInCmd 预填了参数（用户不用手抄 key）",
    state.cmdParams.key === WI.items[0].title &&
    state.cmdParams.status === "adopted");
  check("openInCmd 选中了动作所在的命令卡",
    state.cmdCard === "ledger");
  check("openInCmd 重渲染了参数区（否则预填看不见）",
    cmdState.params === true);
  check("openInCmd 明确告知「预演 → 确认 → 才写」",
    banners.length > 0 && banners[banners.length - 1][1].includes("预演"));
  /* 动作面没有这条动作时不许静默失败 */
  banners.length = 0;
  await openInCmd("no_such_action", {});
  check("动作不存在时给出可执行的退路（复制命令行）",
    banners.length > 0 && banners[banners.length - 1][1].includes("复制"));

  console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
    "\nALL WORK-ITEM CHECKS PASSED");
  process.exit(failed ? 1 : 0);
})();
