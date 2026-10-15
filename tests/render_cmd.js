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
function repFilter() { return { q: "", only: false }; }
function hitQ() { return true; }
const glossary = { loaded: false, data: null };
const state = { act: { loaded: false, actions: [], commands: null, error: null },
  cmdCard: null, actionId: null, cmdParams: {}, job: null, chain: [] };
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
      target: "suggestions_meta.db", reversible: "reversible" },
  ],
  tiers: { A: "只读直接执行", B: "写 meta 两段提交", C: "只生成命令" },
};
ACT_RESP["/api/commands"] = COMMANDS;

/* ---- extract 真源码并执行 ---- */
for (const [n, sig] of [
  ["tierLabel", "t"], ["cmdCommands", ""], ["renderCmdCards", ""],
  ["renderCmdActions", ""], ["selectCmdCommand", "name"],
  ["renderCmdParams", ""], ["cmdLine", "c,params"], ["updateCmdPreview", ""],
  ["cmdActionMap", ""], ["actionForCommand", "cmd"], ["A_ACTIONS", ""],
  ["jobResultHtml", "job"],
  ["bindPatternDrill", "root"], ["renderPatternSessions", "d,out,backTo"],
  ["startThreeHop", "sid,seq,errText"], ["renderErrors", "d,out"],
  /* P3：B 档接线与版本握手 */
  ["actionSpec", "aid"], ["writeActionsForCard", "cardId"],
  ["currentWriteSpec", ""], ["renderVersionBar", ""], ["writeDisabled", ""],
  /* v0.49：会话抑制的 view 侧（「已抑制」清单 + 恢复） */
  ["loadSuppressed", "manual"], ["renderSuppressed", "d"],
  /* 2026-10-15：双形态选中解析 + 命令悬停说明 */
  ["resolveActionSel", ""], ["stripMd", "s"], ["cmdNameOf", "cli"],
  ["argsHelp", "c"], ["cmdHoverTitle", "row"],
]) {
  eval(extract(n, sig));
}

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
  ht.includes("这条命令干嘛用：跑一次效果评估") && ht.includes("两指标") &&
  !ht.includes("**") && ht.includes("复制到仓根目录"));
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

  console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
    "\nALL COMMAND-PALETTE CHECKS PASSED");
  process.exit(failed ? 1 : 0);
})();
