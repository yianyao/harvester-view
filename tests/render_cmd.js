/* P2-4 命令台渲染检查（node 运行，退出码 0=全过）。
 *
 * 为什么单独一个门：index.html 是 92k 单文件，其他门（render_smoke.js /
 * render_real.js）只 extract 它们各自关心的那几个函数——**新加的 JS 语法
 * 错了、或调了一个不存在的全局**，那些门照样绿。P1 会话为此红过三轮
 * （HANDOFF 会话 2 技术备忘：渲染函数新增全局依赖时先补桩再跑）。
 *
 * 做法与 render_smoke.js 同：从 index.html extract 真函数源码 → 注入最小
 * DOM/网络桩 → 真执行 → 断言渲染结果。
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

/* ---- 最小桩（与 index.html 同名同形） ---- */
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
function errorBox(msg) { return "<div class='empty'>" + esc(msg) + "</div>"; }
function showBanner() {}
function switchTab() {}
function clsLabel(c) { return c; }
function shortSid(s) { return String(s || "").slice(0, 12); }
function bindAnchors() {}
function anchor(sid, seq, err) {
  return "<a class='anchor' data-sid='" + esc(sid) + "' data-seq='" +
    Number(seq) + "' data-err='" + esc(err || "") + "'>" + esc(sid) +
    "#" + seq + "</a>";
}
function rerenderReport() {}
function applyTermTooltips() {}
function api() { return Promise.resolve({ topics: [] }); }
function encodeAll(o) { return JSON.stringify(o); }
function patternPlain() { return ""; }
/* v0.83：renderErrors 还要 patternNext / patternFrom（错误模式的「下一步该咋办」与依据出处）。这里给空桩：本门验的是别的东西，三层文案的断言在 render_brief.js。 */
const patternNext = () => "";
const patternFrom = () => "";
function repFilter() { return { q: "", only: false }; }
function hitQ() { return true; }
function copyText(t, btn) { if (btn) btn.textContent = "已复制"; }
const glossary = { loaded: false, data: null };
const state = { act: { loaded: false, actions: [], commands: null, error: null },
  cmdCard: null, actionId: null, cmdParams: {}, job: null, chain: [],
  /* envprobe 透出（服务端 /api/commands 注入）：命令前缀按本机环境现探测 */
  cliPrefix: "python -m harvester",
  repoRoot: "C:/Users/u/WorkBuddy/x/session-harvester" };
/* 第三跳桩：记录 gotoAnchor 的入参，用来断言"三跳确实接上了"，
   而不是只断言"函数没抛错" */
const hopCalls = [];
function gotoAnchor(sid, seq, err) {
  hopCalls.push([sid, seq, err]);
  return Promise.resolve();
}

/* 网络桩：actApi 直接吃我们塞的载荷，不发真请求 */
let ACT_RESP = {};
async function actApi(p) { return ACT_RESP[p] || {}; }
async function actJson(p, o) { return ACT_RESP[p] || {}; }

/* ---- 夹具：一份**形状与真 /api/commands 一致**的最小注册表 ---- */
const COMMANDS = {
  schema: 1, adapter_version: "0.46.0",
  cards: [
    { card_id: "errors", label: "错误验尸", tier: "A", order: 5,
      desc: "G2 错误三分类与失败翼报告。", writes: "" },
    { card_id: "service", label: "长驻服务", tier: "C", order: 13,
      desc: "要占端口常驻——只生成命令。", writes: "占用端口" },
    { card_id: "ledger", label: "台账裁决", tier: "B", order: 14,
      desc: "建议台账裁决（写动作走两段提交）。", writes: "suggestions_meta.db" },
  ],
  commands: [
    { cmd: "report-errors", card_id: "errors", tier: "A", subactions: null,
      help: "错误三分类报告", nested: false,
      args: [
        { name: "--db", dest: "db", kind: "str", default: "harvester.db",
          required: false, choices: null, src: "server", negates: null,
          help: "索引库路径", note: null },
        { name: "--since", dest: "since", kind: "float", default: null,
          required: false, choices: null, src: "chips", negates: null,
          help: "只统计最近 N 天", note: "缺省=全库" },
      ] },
    { cmd: "topic", card_id: "errors", tier: "B", nested: false,
      help: "主题注册表", subactions: [
        { name: "list", tier: "A" }, { name: "register", tier: "B" }],
      args: [
        { name: "--id", dest: "id_", kind: "str", default: null,
          required: false, choices: null, src: "topics_api", negates: null,
          help: "主题 id", note: null },
        { name: "--meta", dest: "meta", kind: "str",
          default: "topics_meta.db", required: false, choices: null,
          src: "server", negates: null, help: "meta 库路径", note: null },
      ] },
    { cmd: "mcp-serve", card_id: "service", tier: "C", subactions: null,
      help: "MCP stdio server", nested: false, args: [] },
    { cmd: "suggest-status", card_id: "ledger", tier: "B", subactions: null,
      help: "改建议台账里一条建议的状态", nested: false,
      args: [
        { name: "--meta", dest: "meta", kind: "str",
          default: "suggestions_meta.db", required: false, choices: null,
          src: "server", negates: null, help: "建议台账 meta 库路径",
          note: null },
      ] },
  ],
};

ACT_RESP["/api/actions"] = {
  view_actions_api: 1, harvester_version: "0.46.0",
  actions: [
    { action_id: "report_errors", tier: "A", cmd: "report-errors",
      label: "错误验尸（G2 错误三分类）",
      desc: "错误三分类 + 归一模式聚类。",
      params: { days: { type: "days", src: "chips", default: null,
        label: "时间窗" } },
      est_sec: 3, reversible: "幂等（可重跑覆盖，不写任何库）", writes: "" },
    { action_id: "suggest_status", tier: "B", cmd: "suggest-status",
      summary: "在建议台账里把一条建议的状态改为 {status}",
      params: { key: { label: "建议句", type: "str" },
                status: { label: "新状态", type: "str" } },
      target: "suggestions_meta",
      target_path: "C:/Users/u/WorkBuddy/x/session-harvester/suggestions_meta.db",
      reversible: "reversible" },
  ],
  tiers: { A: "只读直接执行", B: "写 meta 两段提交", C: "只生成命令" },
};
ACT_RESP["/api/commands"] = COMMANDS;

/* ---- extract 真源码并执行 ---- */
/* 第③步：renderErrors 依赖模块级"次数阈值"辅助块（repMinOf/repGate/
   repRemain/repMinChips/wireRepThresh）。extract 只提单个函数，这里按注释头
   到 wireRepThresh 的收尾整块提出；`const` 在 eval 里不外泄，故改成 `var`。 */
const _th0 = html.indexOf("/* 报告页「次数阈值」");
const _th1 = html.indexOf("\n}\n", html.indexOf("function wireRepThresh(")) + 3;
if (_th0 < 0 || _th1 < 2) throw new Error("阈值辅助块提取失败");
eval(html.slice(_th0, _th1).replace("const REP_MIN_DEFAULT = 3;",
                                    "var REP_MIN_DEFAULT = 3;"));

for (const [n, sig] of [
  ["tierLabel", "t"], ["cmdCommands", ""], ["renderCmdCards", ""],
  ["renderCmdActions", ""], ["selectCmdCommand", "name"],
  ["renderCmdParams", ""], ["cmdLine", "c,params"], ["updateCmdPreview", ""],
  ["cmdActionMap", ""], ["actionForCommand", "cmd"], ["A_ACTIONS", ""],
  ["jobResultHtml", "job"], ["jobAnalysisHtml", "job"],
  ["bindPatternDrill", "root"], ["renderPatternSessions", "d,out,backTo"],
  ["startThreeHop", "sid,seq,errText"], ["renderErrors", "d,out"],
  /* P3：B 档接线与版本握手 */
  ["actionSpec", "aid"], ["writeActionsForCard", "cardId"],
  ["currentWriteSpec", ""], ["renderVersionBar", ""], ["writeDisabled", ""],
  /* v0.49：会话抑制的 view 侧（「已抑制」清单 + 恢复） */
  ["loadSuppressed", "manual"], ["renderSuppressed", "d"],
  /* 2026-10-15：双形态选中解析 + 命令悬停说明 + 环境探测命令形 */
  ["resolveActionSel", ""], ["stripMd", "s"], ["cmdNameOf", "cli"],
  ["argsHelp", "c"], ["cmdHoverTitle", "row"],
  ["targetHuman", "t"], ["displayPath", "p"],
  /* v0.72：renderCmdParams 出口会调 renderCmdBrowsable（上下文感知选值面板），
     被提取函数的新依赖必须同步进提取清单（会话 10 铁教训），一并真执行。 */
  ["cmdNeededPick", ""], ["renderCmdBrowsable", ""],
  /* P9-⑤：归因台的分组/类别是**渲染行为**，必须真执行才算门
     （只断言源码里有 atr-group，变异把条件改成 if(false) 照样绿）。 */
  ["atrSlotHtml", "p,rule"], ["renderAttrib", "d,out"],
]) {
  eval(extract(n, sig));
}

/* renderAttrib 依赖的模块级常量（index.html 里是 const，eval 不外泄→补桩） */
var ATR_VERDICTS = [["accepted", "接受"], ["rejected", "驳回"],
  ["revise", "要改"]];
var ATR_VERDICT_LABEL = { accepted: "接受", rejected: "驳回", revise: "要改" };
var CLASS_LABEL = { env: "环境类", tool_interface: "用法类",
  context: "上下文类", unclassified: "未归类" };
function atrSubmit() {}

let failed = 0;
function check(label, cond) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label); }
}

/* ① 载入注册表 → 动词卡渲染（13 卡来自服务端，不写死） */
state.act = { loaded: true, actions: ACT_RESP["/api/actions"].actions,
  commands: COMMANDS, error: null };
renderCmdCards();
const cardsHtml = $("cmd-cards").innerHTML;
check("动词卡渲染（两卡）", cardsHtml.includes("错误验尸") &&
  cardsHtml.includes("长驻服务"));
check("卡上带档位徽章（A 可执行）", cardsHtml.includes("tierbadge A") &&
  cardsHtml.includes("可执行"));
check("C 档卡标注只给命令", cardsHtml.includes("只给命令"));
check("卡内命令计数来自注册表", cardsHtml.includes("2 命令"));

/* ② 选卡 → 命令 chips */
state.act.commands = COMMANDS;
state.cmdCard = "errors";
renderCmdActions();
const actsHtml = $("cmd-actions").innerHTML;
check("命令 chips 渲染", actsHtml.includes("report-errors") &&
  actsHtml.includes("topic"));
/* 子动作逐项档位：选中聚合命令后才展开（phase 1 只给 chips） */
selectCmdCommand("topic");
const subHtml = $("cmd-actions").innerHTML;
check("聚合命令展开子动作逐项档位", subHtml.includes("register") &&
  subHtml.includes("tierbadge B") && subHtml.includes("tierbadge A"));

/* ③ 选命令 → 参数选择器：路径类参数**不渲染输入框**（红线） */
selectCmdCommand("report-errors");
const pHtml = $("cmd-params").innerHTML;
check("参数区渲染 --since", pHtml.includes("--since"));
check("路径类参数 --db 不渲染输入框（红线）", !pHtml.includes("data-p=\"db\""));
check("跳过路径参数要有交代", pHtml.includes("路径类参数"));

/* ④ 命令行预演与执行徽章 */
state.cmdParams = { since: 7 };
updateCmdPreview();
check("命令行预演含 --since 7", $("cmd-banner").innerHTML.includes("--since 7"));
check("命令行预演**不含**路径参数 --db",
  !$("cmd-banner").innerHTML.includes("harvester.db"));
check("执行徽章显示预估/可撤销", $("cmd-est").textContent.includes("~3s") &&
  $("cmd-est").textContent.includes("幂等"));

/* ⑤ C 档命令：不给执行入口（且**不许崩**——这是真缺陷回归门：
   2026-10-12 实测 updateCmdPreview 在"命令级 B 档且无对应动作"时读
   undefined.est_sec 整页抛错，而那条路径正是 43 条命令里的绝大多数） */
state.cmdCard = "service";
state.actionId = null;
selectCmdCommand("mcp-serve");
check("C 档命令映射不到可执行动作", actionForCommand("mcp-serve") === null);
check("C 档命令禁用执行按钮", $("btn-cmd-run").disabled === true);
check("C 档给出不可执行的理由", $("cmd-est").textContent.includes("C 档"));
/* 命令级 B 档 + 无动作：同样不许崩（回归点） */
state.cmdCard = "errors";
selectCmdCommand("topic");
check("B 档命令禁用执行按钮且不崩", $("btn-cmd-run").disabled === true);
check("B 档理由指向两段提交", $("cmd-est").textContent.includes("B 档"));
/* A 档但动作面缺该动作：也不许崩，理由要说清 */
selectCmdCommand("report-errors");
check("A 档命令映射到动作", actionForCommand("report-errors") === "report_errors");
check("A 档有动作时执行按钮可用", $("btn-cmd-run").disabled === false);

/* ⑤b 动作 id 形态（处理单/报告页「在命令台打开」装进来的是 action_id，
   不是命令名）。2026-10-15 修：以前 runCmdAction/updateCmdPreview 只认
   命令名形态——动作已装进命令台，点预演却报"没有已实现的动作"
   （用户截图批注「玩人哪」的根因）。 */
state.cmdCard = "errors";
state.actionId = "report_errors";
state.cmdParams = { since: 7 };
const selA = resolveActionSel();
check("动作 id 形态反查到命令条目",
  selA.c && selA.c.cmd === "report-errors" &&
  selA.spec && selA.spec.action_id === "report_errors");
updateCmdPreview();
check("动作 id 形态的 A 档走命令行预览（不再错走写面板）",
  $("cmd-banner").innerHTML.includes("--since 7"));
check("动作 id 形态的 A 档有执行入口", $("btn-cmd-run").disabled === false);

/* ⑤c 动作 id 形态的 B 档：走写面板（预演按钮可点），参数区用注册表参数 */
state.cmdCard = "ledger";
state.actionId = "suggest_status";
state.cmdParams = { key: "k1", status: "adopted" };
const selB = resolveActionSel();
check("B 档动作 id 反查到命令 suggest-status",
  selB.c && selB.c.cmd === "suggest-status" &&
  selB.direct === false);
updateCmdPreview();
check("B 档动作 id 走写面板（预演按钮可点，不再报没有动作）",
  $("btn-cmd-run").textContent.includes("预演") &&
  $("btn-cmd-run").disabled === false);
renderCmdParams();
check("B 档动作 id 的参数区用注册表参数（写动作面板）",
  $("cmd-params").innerHTML.includes("参数（写动作 suggest_status）"));

/* ⑤d 命令注册表缺条目时给最小伪条目——命令行预览/执行入口不丢 */
state.act.actions.push({ action_id: "lonely_act", cmd: "no-such-cmd",
  tier: "A", params: {}, est_sec: 1, reversible: "幂等" });
state.actionId = "lonely_act";
const selL = resolveActionSel();
check("注册表缺命令条目时反查给伪条目（入口不丢）",
  selL.c && selL.c.cmd === "no-such-cmd" && selL.c.tier === "A" &&
  Array.isArray(selL.c.args) && selL.c.args.length === 0);
state.act.actions.pop();

/* ⑤e 命令悬停说明（用户批注：所有命令都要有悬停提示其作用） */
const ht = cmdHoverTitle({ label: "跑一次效果评估", note: "**两指标**都要不升",
  cli: "python -m harvester harvest-eval --db x", action_id: null });
check("悬停说明含去星号的作用与自跑退路",
  ht.startsWith("跑一次效果评估") && ht.includes("两指标") &&
  !ht.includes("**") && ht.includes("cd 到 session-harvester"));
const ht2 = cmdHoverTitle({ label: "补 pattern 批注", note: "",
  cli: "python -m harvester suggest-status --meta m",
  action_id: "suggest_status" });
check("带动作的悬停说明指向「在命令台打开」", ht2.includes("在命令台打开"));
check("argsHelp 汇总非路径参数的帮助（路径参数不进悬停）",
  argsHelp({ args: [
      { name: "--since", help: "只统计最近 N 天", src: "chips" },
      { name: "--db", help: "索引库路径", src: "server" }] })
    .includes("--since") &&
  !argsHelp({ args: [{ name: "--db", help: "索引库路径", src: "server" }] }));

/* ⑤f 复制命令 + 粘贴位置（用户批注：复制到终端执行的命令要有复制按钮，
   且命令形按本机环境探测——本机 python 不在 PATH 而有 uv，实测） */
state.cmdCard = "errors";
state.actionId = "report_errors";
state.cmdParams = { since: 7 };
updateCmdPreview();
check("命令行预览带「复制」按钮（复制命令行）",
  $("cmd-banner").innerHTML.includes("btn-cmd-copy") &&
  $("cmd-banner").innerHTML.includes("复制命令行（粘贴到终端运行）"));
check("命令行预览写明粘贴步骤（cd 到上游仓+相对路径基准）",
  $("cmd-banner").innerHTML.includes("cd 到") &&
  $("cmd-banner").innerHTML.includes("session-harvester") &&
  $("cmd-banner").innerHTML.includes("相对路径"));
check("cmdLine 用 envprobe 前缀（不写死 python，可整体换 uv 形）",
  cmdLine({ cmd: "report-errors", args: [] }, {})
    .startsWith(state.cliPrefix));

/* ⑤g 目标库行说人话：库名 + 它装什么（不再裸甩绝对路径） */
state.actionId = "suggest_status";
state.cmdParams = { key: "k1", status: "adopted" };
updateCmdPreview();
check("写动作横幅改为「写哪个库」并带人话说明",
  $("cmd-banner").innerHTML.includes("写哪个库") &&
  $("cmd-banner").innerHTML.includes("建议台账"));
check("targetHuman 覆盖四本台账且未知库名有兜底",
  targetHuman("suggestions_meta").includes("建议台账") &&
  targetHuman("topics_meta").includes("抑制") &&
  targetHuman("artifacts_meta").includes("产物") &&
  targetHuman("keywords_meta").includes("词频") &&
  targetHuman("no_such_db").length > 0);

/* ⑥ 结果渲染：数字皆门（模式次数可点） */
const job = { status: "done", label: "report-errors", elapsed_ms: 1200,
  result: { meta: { error_count: 381, total_steps: 44969, sessions: 65 },
    by_class: { env: 100, tool_interface: 200, context: 81 },
    db_fingerprint: { sessions: 1981, steps: 44969, errors: 381,
      db_mtime: "2026-10-12 16:47:00" },
    patterns: [{ pattern: "FS_STALE_VERSION", count: 72,
      tools: ["edit"] }] } };
const jHtml = jobResultHtml(job);
check("结果渲染错误总数", jHtml.includes("381"));
check("结果渲染模式行", jHtml.includes("FS_STALE_VERSION"));
check("模式次数是可点的门", jHtml.includes("data-pat="));
check("原始 JSON 可展开（机器口径不丢）", jHtml.includes("原始 JSON"));

/* ⑥b Top-N 截断必须报数（用户口径：不到阈值不显示，但**被滤掉的要说得清**）*/
const many = { status: "done", label: "report-errors", elapsed_ms: 1,
  result: { meta: { error_count: 381 }, by_class: { env: 381 },
    patterns: Array.from({ length: 12 }, (_, i) => (
      { pattern: "PAT" + i, count: 12 - i })) } };
const mHtml = jobResultHtml(many);
check("Top 8 之外有「另有 N 个未列」", /另有 4 个未列/.test(mHtml));
check("Top 8 之外给全量出口（导出 JSON）", /完整清单用「导出 JSON」/.test(mHtml));
const few = { status: "done", label: "x", elapsed_ms: 1,
  result: { meta: {}, by_class: { env: 1 },
    patterns: [{ pattern: "ONLY", count: 1 }] } };
check("模式不足 8 个时不显示余量句", !/另有 0 个未列/.test(jobResultHtml(few)));

/* ⑦ P2-5 数字皆门：三跳接线 + 逐级数字对账 */const ERR = { meta: { error_count: 3, total_steps: 20, sessions: 2 },
  by_class: { tool_interface: 3 }, by_bucket: {}, by_source: {},
  patterns: [{ pattern: "FS_STALE_VERSION", "class": "tool_interface",
    count: 3, tools: ["edit"],
    samples: [{ sid: "s1", seq: 2, error: "E1" }] }] };
const eOut = { innerHTML: "", querySelectorAll: () => [] };
renderErrors(ERR, eOut);
check("一跳：模式次数是可点的门（data-pat2）",
  eOut.innerHTML.includes("data-pat2="));
check("一跳：样例锚点仍在（第三跳捷径）",
  eOut.innerHTML.includes("data-sid="));
check("一跳：给出通第二跳的文字入口",
  eOut.innerHTML.includes("会话列表"));

const D = { pattern: "FS_STALE_VERSION", count: 3, occurrences: 3,
  sessions_count: 2, given_up: 1,
  sessions: [
    { sid: "s1", title: "会话甲", occurrences: 2, given_up: 0,
      sample_seq: 2, sample_error: "E1", tools: ["edit"],
      updated_at: "2026-10-01 10:10:00" },
    { sid: "s2", title: "会话乙", occurrences: 1, given_up: 1,
      sample_seq: 2, sample_error: "E2", tools: ["edit"],
      updated_at: "2026-10-02 10:10:00" },
  ] };
const tOut = { innerHTML: "", querySelectorAll: () => [] };
renderPatternSessions(D, tOut, true);
check("二跳：列出全部命中会话", tOut.innerHTML.includes("会话甲") &&
  tOut.innerHTML.includes("会话乙"));
check("二跳：逐会话次数之和与总数一致（显示 ✓）",
  tOut.innerHTML.includes("与模式总数一致"));
check("二跳：会话行带 (sid,seq) 锚点（供第三跳）",
  tOut.innerHTML.includes("data-sid=") && tOut.innerHTML.includes("s1"));
/* 对账断了必须显式说断（不许两边各显示一个数糊过去） */
const BAD = Object.assign({}, D, { count: 99 });
const bOut = { innerHTML: "", querySelectorAll: () => [] };
renderPatternSessions(BAD, bOut, true);
check("二跳：数字对不上要显式报警", bOut.innerHTML.includes("不一致"));

/* 第三跳：必须带上 (sid, seq, error)，且不自己编 turn 号 */
hopCalls.length = 0;
startThreeHop("s1", 7, "E1");
check("三跳：调用 gotoAnchor 且原样带 seq/error",
  hopCalls.length === 1 && hopCalls[0][0] === "s1" && hopCalls[0][1] === 7 &&
  hopCalls[0][2] === "E1");

/* ⑧ 端点地址静态断言（**P1 的真缺陷回归门**）
 *
 * 页面在 8088，而 /view/cards 与 /view/glossary 在 8766（动作面）。
 * P1 把这两处写成相对路径 `fetch("/view/glossary")` → 打到 8088 的
 * view.py（只服务 / 与 /u/<n>/api/*）→ **实测 404**，于是 glossary 与
 * 卡片本体在浏览器里根本没数据，**而 P1 的门全绿**（门只 extract 渲染
 * 函数、只调 8766 端点，没人盯"页面用的 URL 在它自己的 origin 上存在吗"）。
 *
 * 这条门不出网、只读源码：页面里调 /view/* 的地方必须走 ACT_BASE
 * （唯一例外 /view/upstreams —— 那是 view.py 自己的端点，8088 上确实有）。
 */
const bareViewFetches = (html.match(/fetch\("\/view\/[a-z]+"/g) || []);
check("页面不再用相对路径打 /view/*（P1 真缺陷回归）",
  bareViewFetches.every(s => s.includes("/view/upstreams")) &&
  (html.match(/fetch\(ACT_BASE\+"\/view\/[a-z]+"/g) || []).length >= 2);
check("glossary 与卡片本体都走动作面地址",
  html.includes('fetch(ACT_BASE+"/view/glossary")') &&
  html.includes('fetch(ACT_BASE+"/view/cards")'));

/* ⑨ P3：B 档接线 + 版本握手（P3-5 验收：不匹配 → 黄条 + B 档禁用，A 档不受影响）
 *
 * 这一节的门是**真缺陷驱动**的：B 档动作 id（如 topic_register）不是命令名，
 * 若页面仍按"命令名 → 动作"去找，点下去会静默什么都不做（首版就差点这样）。
 */
ACT_RESP["/api/actions"].actions.push(
  { action_id: "topic_register", tier: "B", risk: "reversible",
    target: "topics_meta", target_path: "X:/topics_meta.db", cmd: "topic",
    subaction: "register", reversible: "幂等/可回到原状（仍有台账留痕）",
    label: "注册一个新主题「{name}」", desc: "同名主题已存在时幂等返回旧 id。",
    params: { name: { type: "str", src: "none", required: true,
                      label: "主题名" } } },
  { action_id: "topic_delete", tier: "B", risk: "destructive",
    target: "topics_meta", target_path: "X:/topics_meta.db", cmd: "topic",
    subaction: "delete", reversible: "会丢信息",
    label: "删除主题行 {id_}",
    params: { topic_id: { type: "str", src: "topics_api", required: true,
                          label: "要删除的主题" } } });
state.act.health = { version_ok: true, harvester_version: "0.46.0",
  commands_adapter_version: "0.46.0" };
state.act.versionOk = true;
state.cmdCard = "errors";
renderCmdActions();
check("写动作按 action_id 出现在命令卡里（不是命令名）",
  $("cmd-actions").innerHTML.includes("data-cmd=\"topic_register\""));
check("破坏性动作在 chips 上有区分标记",
  $("cmd-actions").innerHTML.includes("破坏性"));
check("actionSpec 能按 id 取到注册表条目（B 档确认框要用）",
  actionSpec("topic_delete").risk === "destructive");
/* 选中写动作 → 参数来自注册表、路径类不渲染输入框、按钮改成"预演" */
selectCmdCommand("topic_register");
const wHtml = $("cmd-params").innerHTML;
check("写动作参数渲染（主题名）", wHtml.includes("主题名"));
check("写动作不渲染路径类参数", !wHtml.includes("data-p=\"target\""));
check("写动作按钮是「预演」而不是「执行」",
  $("btn-cmd-run").textContent.includes("预演"));
check("写动作面板写明风险与两段提交",
  $("cmd-est").textContent.includes("两段提交") ||
  $("cmd-est").textContent.includes("可逆"));

/* 版本不匹配 → B 档禁用（A 档仍可用）*/
state.act.versionOk = false;
state.act.health = { version_ok: false, harvester_version: "0.99.0",
  commands_adapter_version: "0.46.0" };
const verEl = mkEl();
els["verbar"] = verEl;
renderVersionBar();
check("版本不匹配 → 黄条出现且说明 B 档禁用",
  verEl.style.display === "block" && verEl.innerHTML.includes("B 档写动作已禁用") &&
  verEl.innerHTML.includes("0.99.0"));
check("版本不匹配 → writeDisabled() 为真", writeDisabled() === true);
selectCmdCommand("topic_register");
check("版本不匹配时写动作按钮禁用",
  $("btn-cmd-run").disabled === true &&
  $("btn-cmd-run").textContent.includes("已禁用"));
/* A 档不受影响：仍可执行 */
selectCmdCommand("report-errors");
check("A 档不受版本不匹配影响（仍可执行）",
  $("btn-cmd-run").disabled === false);
state.act.versionOk = true;
state.act.health = { version_ok: true };
renderVersionBar();
check("恢复匹配后黄条消失", verEl.style.display === "none");

/* 旧代码检测（P14 2026-10-26）：磁盘改过、进程没换 → 红条 + 给具体修法。
   判据由服务端给（code_stale），页面不许自己比指纹。 */
state.act.health = { version_ok: true, code_stale: true,
  started_at: "2026-10-26T09:00:00+08:00" };
renderVersionBar();
check("在跑旧代码 → 红条（err 而不是 warn）",
  verEl.style.display === "block" && verEl.className === "err");
check("在跑旧代码 → 说清症状与修法（含 stop.cmd 与启动时间）",
  verEl.innerHTML.includes("这套服务在跑旧代码") &&
  verEl.innerHTML.includes("stop.cmd") &&
  verEl.innerHTML.includes("2026-10-26T09:00:00"));
check("在跑旧代码 → 不许把 markdown 星号漏到页面上",
  !verEl.innerHTML.includes("**"));
state.act.health = { version_ok: true };
renderVersionBar();
check("换回干净 health → 红条消失", verEl.style.display === "none");

/* ---------- v0.49 会话抑制：已抑制清单与「恢复」 ----------
 * 用户 2026-10-13 批准清单 ①–⑦、⑧ 不做。本段钉住 view 侧三条：
 *  ① 「已抑制」是**服务端全量清单**（`/api/suppressed`），不是对当前页做筛选
 *     ——在只有一页的表格上筛选，看起来像「全部就这些」（§七 20 那类静默骗人）；
 *  ② 口径必须写在页面上（仓内副本 / 不删采集源 / 只取决于语料是否还在 / 不设时限）；
 *  ③ 「恢复」把 `session_unsuppress` 装到命令台（**走现成的两段提交，不新造写路径**）。
 */
const unsuppressCalls = [];
openInCmd = function (aid, params) {
  unsuppressCalls.push([aid, params]);
  return Promise.resolve(true);
};
cssShortId = s => String(s || "").slice(0, 12) + "…";

const SUP = {
  view_suppress_api: 1, configured: true, meta: "topics_meta.db",
  meta_exists: true, count: 2,
  rows: [
    { sid: "dsh:aaa-bbb", reason: "太水了", created: "2026-10-13 10:00:00",
      created_by: "web-ui" },
    { sid: "dsh:ccc-ddd", reason: "", created: "2026-10-13 11:00:00",
      created_by: "" },
  ],
};

renderSuppressed(SUP);
const sup = $("suppressed-panel").innerHTML;
check("已抑制清单渲染条数", sup.includes("已抑制会话（2 条）"));
check("每条都渲染出 sid / 何时 / 谁 / 为什么",
  sup.includes("2026-10-13 10:00:00") && sup.includes("web-ui") &&
  sup.includes("太水了"));
check("缺理由与缺执行人都如实标注（不静默留空）", sup.includes("（未写）"));
check("每条都有「恢复」按钮",
  (sup.match(/data-unsuppress=/g) || []).length === 2);
check("口径逐条写在页面上（用户批准的 ③ 与 ⑧）",
  sup.includes("仓内副本") && sup.includes("不删采集源") &&
  sup.includes("只取决于语料是否还在") && sup.includes("不设恢复时限"));
check("不许出现「可恢复」式承诺",
  !sup.replace(/不恢复/g, "").includes("可恢复"));

/* 「恢复」= 把 session_unsuppress 装到命令台（不新造写路径）。
   门里的 `$()` 桩 `querySelectorAll` 返回空数组，故这里**给它一批假按钮**，
   让 renderSuppressed 真的绑上 onclick，再手动点一次——不这样验，就等于
   只查了 HTML 字符串（抓不到"按钮绑错动作"这种执行路径上的缺陷）。 */
const fakeBtns = [{ dataset: { unsuppress: "dsh:aaa-bbb" }, onclick: null }];
const supPanel = $("suppressed-panel");
const origQSA = supPanel.querySelectorAll;
supPanel.querySelectorAll = sel =>
  (sel === "[data-unsuppress]" ? fakeBtns : []);
renderSuppressed(SUP);
check("恢复按钮被真的绑上 onclick", typeof fakeBtns[0].onclick === "function");
unsuppressCalls.length = 0;
if (fakeBtns[0].onclick) fakeBtns[0].onclick();
check("恢复按钮把 session_unsuppress 交给命令台（含该 sid）",
  unsuppressCalls.length === 1 &&
  unsuppressCalls[0][0] === "session_unsuppress" &&
  unsuppressCalls[0][1].sids === "dsh:aaa-bbb");
supPanel.querySelectorAll = origQSA;

/* 空清单明说「没有」，不装成有内容 */
renderSuppressed({ view_suppress_api: 1, configured: true, rows: [] });
check("空清单写「没有已抑制的会话」",
  $("suppressed-panel").innerHTML.includes("没有已抑制的会话"));
/* 未配置 topics_meta 时如实说，不假装 0 条 */
renderSuppressed({ view_suppress_api: 1, configured: false, rows: [] });
check("未配置时标注「无处可查」（不是假 0）",
  $("suppressed-panel").innerHTML.includes("无处可查"));

/* 勾选后表格与分页必须让位（清单是另一份数据，不能并排看着像同一批） */
$("f-suppressed").checked = true;
const supApiCalls = [];
actApi = u => { supApiCalls.push(u); return Promise.resolve(SUP); };
(async () => {
  await loadSuppressed(false);
  check("勾「已抑制」走的是一次性全量端点（不是对当前页筛选）",
    supApiCalls.length === 1 && supApiCalls[0].includes("/api/suppressed"));
  check("会话表格让位隐藏", $("sess-table").style.display === "none");
  check("分页也让位隐藏", $("pager").style.display === "none");
  check("清单面板显示出来", $("suppressed-panel").style.display === "");
  $("f-suppressed").checked = false;
  await loadSuppressed(false);
  check("取消勾选后表格与分页恢复",
    $("sess-table").style.display === "" && $("pager").style.display === "");

  /* ---------- v0.80 命令台三项（用户 2026-10-23 批准）----------
   这里做**接线面**断言（源码文本）：DOM 行为由手测 + boot_sequence 覆盖。 */
check("G1 只有 A 档自动执行（B/C 绝不）",
  /if\(spec\.tier==="A"&&typeof runCmdAction==="function"\)/.test(html));
check("G1 自动执行说明可见（banner 写清跑了什么）",
  html.includes("已自动执行（A 档只读）"));
check("G1 B 档文案仍是「预演→确认→才写」",
  html.includes("点「预演」看清将写什么，确认后才写"));
check("G3 会话选择器有「按标题搜全库」",
  html.includes("btn-cmd-sidsearch") && html.includes("/api/sessions?q="));
check("G3 搜索走 encodeURIComponent（不拼裸串）",
  html.includes('/api/sessions?q="+encodeURIComponent(q)'));
check("L3 结果区有「打包给 LLM」按钮", html.includes("data-llmpack"));
check("L3 打包走 llm_pack 只读动作",
  html.includes('action_id:"llm_pack"'));
check("L3 明说页面不调模型", html.includes("不调模型"));

/* ---------- P9（用户 2026-10-23 五条批注）----------
   断言全部打在**行为/结构**上；同源数据取自 commands.json（不是抄文案）。 */
const _cmds = JSON.parse(
  require("fs").readFileSync(require("path").join(__dirname, "..",
    "actions", "commands.json"), "utf8")).commands;
const _purposes = _cmds.map(c => (c.purpose || "").trim());
check("P9-② 每条命令都有用途（commandgen PURPOSE 落进 commands.json）",
  _purposes.every(p => p.length > 0) && _purposes.length === _cmds.length);
check("P9-② 用途两两不同（悬停不再一模一样）",
  new Set(_purposes).size === _purposes.length);
check("P9-② 悬停取 purpose，不回落成卡说明",
  /const tip=stripMd\(c\.purpose\|\|c\.help\|\|""\)/.test(html) &&
  !/card_id===c\.card_id\)\|\|\{\}\)\.desc/.test(html));
check("P9-④ 「打包给 LLM」必须轮询 job（旧版直读 r2.result → 空框）",
  html.includes("atrWaitJob(r2.job_id)") &&
  !/const pack=r2\.result&&r2\.result\.pack\|\|r2\.result\|\|\{\}/.test(html));
check("P9-④ 服务端结构直给：读 result.pack，缺了要响亮报错",
  html.includes("const pack=res.pack") && html.includes("服务端没给出结构化包"));
check("P9-④ 结果区有机械分析（退出码/条数/未归因）",
  html.includes("function jobAnalysisHtml") &&
  html.includes("条**没有**归因候选"));
check("P9-④ 结果区不再限高 380px（不再挤小窗）",
  !/max-height:\s*380px/.test(html) &&
  /#job-out\{max-height:none/.test(html));
check("P9-④ 结果在网格主位（左结果/右参数）",
  /#cmd-layout\{display:grid;grid-template-columns:minmax\(440px,2fr\)/.test(html) &&
  html.indexOf('id="cmd-result"') < html.indexOf('id="cmd-side"'));
check("P9-③ 动词卡全表默认收起 + 记住展开状态",
  /#cmd-rail\{display:none\}/.test(html) &&
  html.includes('localStorage.getItem("hv_cmd_rail")'));
check("P9-③ 动作台账折到底部（details）",
  /<details id="cmd-ledger-box">/.test(html));
check("P9-①/P10 长解释只在「怎么读」一处（主体只留事实一行，底部那份已删）",
  html.includes('panelIntro("cmd")') &&
  html.includes("怎么读") &&
  !html.includes("怎么读这个命令台") &&
  !html.includes('id="cmd-howto"') &&
  !html.includes("这张卡上有 ") &&
  !html.includes("复制后这样跑：开一个终端"));
check("P9-⑤ 归因台类别显示规则类，并标出与模式类不一致",
  html.includes("CLASS_LABEL[rcls]||rcls") &&
  html.includes("模式判 "));
check("P9-⑤ 同根因多模式出组头（不再散落成孤立行）",
  html.includes("atr-group") && html.includes("个模式 / 合计"));

/* P9-⑤ **行为门**：真渲染两行同规则、不同模式类的候选。
   期望：组头出现（同根因合并）+ 类别不一致时并列标出。
   （M4/M5 变异就是把这两处条件改成 false——静态断言抓不到，这里才红。） */
const _atrOut = mkEl();
renderAttrib({
  reviewable: { level: "R3", label: "可裁决", reason: "夹具" },
  coverage: { line_text: "覆盖 2/2" },
  reviewed: [],
  patterns: [
    { pattern: "EPERM: mkdir x", class: "env", count: 5,
      rule: { rule_id: "AR-009", class: "env", why: "同一根因-权限",
              fix: "修权限", confidence: "high" } },
    { pattern: "tool not found: y", class: "tool_interface", count: 3,
      rule: { rule_id: "AR-009", class: "env", why: "同一根因-权限",
              fix: "修权限", confidence: "high" } },
  ],
}, _atrOut);
check("P9-⑤ 行为：同根因两模式渲染出组头",
  _atrOut.innerHTML.includes("atr-group") &&
  _atrOut.innerHTML.includes("2 个模式 / 合计 8 次"));
check("P9-⑤ 行为：类别不一致时并列标出（规则类 env / 模式判 用法类）",
  _atrOut.innerHTML.includes("环境类") &&
  _atrOut.innerHTML.includes("模式判 用法类"));

/* P9-③ **行为门**：服务端同时给文本与结构（G2 八条命令）时，分析块要认出
   `result.json`（schema / 顶层块数 / 各数组条数 / 库快照），并在结构坏掉时
   响亮报错；只有文本时**不许**冒充有结构（这是旧 bug 的翻版：空结构装成功）。 */
const _jsOk = mkEl();
_jsOk.innerHTML = jobAnalysisHtml({ result: {
  exit_code: 0, stdout: "cards validate 文本",
  json: { schema: "cards-validate/1",
          generated_at: "2026-10-23T00:00:00+0800",
          db_fingerprint: { sessions: 1776, steps: 9001, errors: 532 },
          data: { root: "cards", summary: { cards: 2 }, cards: [{}, {}] } },
} });
check("P9-③ 行为：认 result.json（schema + 顶层块数）",
  _jsOk.innerHTML.includes("cards-validate/1") &&
  _jsOk.innerHTML.includes("顶层 3 块"));
check("P9-③ 行为：按块报条数（cards=2，其余不是数组不列）",
  _jsOk.innerHTML.includes("cards=2"));
check("P9-③ 行为：库快照三数原是现算的（1776/9001/532）",
  _jsOk.innerHTML.includes("1776") && _jsOk.innerHTML.includes("9001") &&
  _jsOk.innerHTML.includes("532"));

const _jsBad = mkEl();
_jsBad.innerHTML = jobAnalysisHtml({ result: {
  exit_code: 0, stdout: "文本", json_error: "落盘不是合法 JSON：Expecting value" } });
check("P9-③ 行为：结构坏了要响亮（不装成功）",
  _jsBad.innerHTML.includes("落盘不是合法 JSON"));

const _jsTextOnly = mkEl();
_jsTextOnly.innerHTML = jobAnalysisHtml({ result: {
  exit_code: 0, stdout: "只有文本的报告" } });
check("P9-③ 行为：只有文本时不冒充有结构",
  !_jsTextOnly.innerHTML.includes("机读口径") &&
  _jsTextOnly.innerHTML.includes("输出"));

console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
    "\nALL COMMAND-PALETTE CHECKS PASSED");
  process.exit(failed ? 1 : 0);
})();
