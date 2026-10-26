/* 渲染门（第③步）：次数阈值 + **被滤掉的部分必须报数**。
 *
 * 用户 2026-10-23 批准："默认阈值认可"（待处理 ≥3）；并要求"不到的就不显示"——
 * 本仓同时要求**不静默丢弃**，所以每张表都要说清"另有 N 条低于阈值、合计几次"。
 *
 * 钉住：
 *   1. 默认阈值 3：1–2 次的模式不进行，但余量行给出条数与次数合计；
 *   2. 切到"全部"（阈值 1）后行数真的变多、余量行消失（H22：过滤必须真能滤）；
 *   3. 两种空态分得清："一条都没命中" vs "都没达到阈值"；
 *   4. 表长上限 80 条时要写"表内只显示前 80 条"，不许静默截断；
 *   5. 阈值 chip 真的接线（点击改 state.repMin 并重渲染）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "static", "index.html"), "utf8");

function extract(name) {
  const m = html.match(new RegExp("^function " + name + "\\([^)]*\\)\\{[\\s\\S]*?^\\}", "m"));
  if (!m) throw new Error("extract fail: " + name);
  return m[0];
}
function extractBlock(firstMarker, lastFn) {
  const i = html.indexOf(firstMarker);
  const fnAt = html.indexOf("function " + lastFn + "(");
  const j = html.indexOf("\n}\n", fnAt);
  if (i < 0 || fnAt < 0 || j < 0) throw new Error("extractBlock fail: " + firstMarker);
  return html.slice(i, j + 3).replace("const REP_MIN_DEFAULT = 3;",
                                      "var REP_MIN_DEFAULT = 3;");
}

let store = {};
const $ = id => (store[id] || (store[id] = { textContent: "", value: "",
                                             checked: false }));
const esc = s => String(s == null ? "" : s).replace(/&/g, "&amp;")
  .replace(/</g, "&lt;").replace(/>/g, "&gt;");
const mkOut = () => ({ innerHTML: "", querySelectorAll: () => [] });
let FILTER = { q: "", only: false };
const repFilter = () => FILTER;
const hitQ = () => true;
const anchor = (sid, seq) => "<a>" + sid + "#" + seq + "</a>";
const clsLabel = c => c;
const patternPlain = () => "";
/* v0.83：renderErrors 还要 patternNext / patternFrom（错误模式的「下一步该咋办」与依据出处）。这里给空桩：本门验的是别的东西，三层文案的断言在 render_brief.js。 */
const patternNext = () => "";
const patternFrom = () => "";
let state = { repMin: undefined, repLast: null, errChip: "" };
const bindPatternDrill = () => {};

eval(extractBlock("/* 报告页「次数阈值」", "wireRepThresh"));
eval(extract("renderTools"));
eval(extract("renderErrors"));

let fail = 0;
const ok = (cond, msg) => {
  if (cond) { console.log("ok - " + msg); } else { fail++; console.log("FAIL - " + msg); }
};
const remOf = h => {
  const m = h.match(/<div class='sub2 rep-rem'>[\s\S]*?<\/div>/);
  return m ? m[0] : "";
};
const rowCount = h => (h.match(/<tr><td class='num'>/g) || []).length;

/* ---------- 报告页"异常根因明细"：1×1、2×2、3×3、5×4 ---------- */
const TOOLS = { tools: [
  { tool: "t1", calls: 5, success: 1, error: 4, fail_rate: 0.8,
    given_up: 0, retried: 0, raw_tools: ["t1"], ranked: true,
    roots: [
      { pattern: "P-ONE", class: "env", count: 1, sample: "s" },
      { pattern: "P-TWO", class: "env", count: 2, sample: "s" },
      { pattern: "P-THREE", class: "tool_interface", count: 3, sample: "s" },
      { pattern: "P-FIVE", class: "tool_interface", count: 5, sample: "s" },
    ] },
] };
function toolsView() {
  state.repMin = arguments[0];
  const out = mkOut();
  renderTools(TOOLS, out);
  return out.innerHTML;
}

const h3 = toolsView(undefined);          /* 默认 = 3 */
ok(rowCount(h3) === 2, "默认阈值 ≥3：只剩 2 条（3 次、5 次）");
ok(h3.indexOf("P-ONE") < 0 && h3.indexOf("P-TWO") < 0, "1–2 次的模式不进行");
ok(remOf(h3).indexOf("另有 2 条") >= 0, "余量行报出「另有 2 条」");
ok(remOf(h3).indexOf("合计 3 次错误") >= 0, "余量行报出它们的次数合计（1+2=3）");
ok(remOf(h3).indexOf("共 2 条 / 11 次错误") >= 0, "余量行给出全量口径（2 条 / 11 次）");

const h1 = toolsView(1);
ok(rowCount(h1) === 4, "切到「全部」后 4 条都出来（过滤真的能滤）");
ok(remOf(h1).indexOf("另有") < 0, "全部模式下不再有「另有 N 条」");

/* ---------- 两种空态 ---------- */
const hNone = (() => {
  state.repMin = 1;
  FILTER = { q: "", only: true };   /* 走过滤态分支，表才会渲染空态 */
  const out = mkOut();
  renderTools({ tools: [{ tool: "t", calls: 1, success: 0, error: 0,
    fail_rate: 0, raw_tools: ["t"], ranked: false, roots: [] }] }, out);
  return out.innerHTML;
})();
ok(hNone.indexOf("无命中") >= 0, "一条都没命中 → 「无命中」");

const ALL_SMALL = { tools: [
  { tool: "t1", calls: 2, success: 0, error: 2, fail_rate: 1,
    given_up: 0, retried: 0, raw_tools: ["t1"], ranked: false,
    roots: [{ pattern: "P-ONE", class: "env", count: 1, sample: "s" }] },
] };
state.repMin = 3;
const outSmall = mkOut();
renderTools(ALL_SMALL, outSmall);
ok(outSmall.innerHTML.indexOf("没有达到阈值的条目") >= 0,
   "命中但都低于阈值 → 单独一种空态（不许说成「无命中」）");
ok(outSmall.innerHTML.indexOf("共 1 条低于阈值") >= 0, "这种空态也报条数");

/* ---------- 表长上限 80 ---------- */
const many = { tools: [{ tool: "t", calls: 200, success: 0, error: 200,
  fail_rate: 1, given_up: 0, retried: 0, raw_tools: ["t"], ranked: true,
  roots: Array.from({ length: 100 }, (_, i) => ({
    pattern: "P-" + i, class: "env", count: 3 + i, sample: "s" })) }] };
state.repMin = 3;
FILTER = { q: "", only: false };
const outMany = mkOut();
renderTools(many, outMany);
ok(rowCount(outMany.innerHTML) === 80, "表里最多 80 行");
ok(remOf(outMany.innerHTML).indexOf("表内只显示前 80 条") >= 0,
   "被截掉的部分要注明（原先静默截断）");

/* ---------- 错误模式表（renderErrors）同样有阈值与余量 ---------- */
const DEP = {
  meta: { error_count: 6, total_steps: 9, sessions: 2 },
  by_class: {}, attrib_status: "n/a",
  patterns: [
    { pattern: "PAT-1", class: "env", count: 1, tools: ["t1"], samples: [] },
    { pattern: "PAT-2", class: "env", count: 2, tools: ["t2"], samples: [] },
    { pattern: "PAT-3", class: "tool_interface", count: 3, tools: ["t3"],
      samples: [] },
  ],
};
state.repMin = undefined;
state.repLast = DEP;
const deOut = mkOut();
renderErrors(DEP, deOut);
ok(rowCount(deOut.innerHTML) === 1, "模式表默认也按 ≥3 过滤（只剩 PAT-3）");
ok(remOf(deOut.innerHTML).indexOf("另有 2 条") >= 0, "模式表也报被滤掉的条数");
ok(deOut.innerHTML.indexOf("data-repmin='1'") >= 0, "模式表给「全部」chip");

/* ---------- chip 接线真的接线 ---------- */
const fakeChip = { dataset: { repmin: "1" }, onclick: null };
const wireOut = { innerHTML: "", querySelectorAll: sel =>
  sel === "[data-repmin]" ? [fakeChip] : [] };
wireRepThresh(wireOut);
ok(typeof fakeChip.onclick === "function", "阈值 chip 有点击处理器（不是死按钮）");
let rerendered = null;
state.repLast = DEP;
const origRender = renderErrors;
fakeChip.onclick();
ok(state.repMin === 1, "点「全部」后阈值变成 1（state.repMin）");

/* ---------- 方案 B：被滤掉的尾部里，"窗口内首见"必须单独报数 ---------- */
const FRESH = {
  meta: { total_steps: 100, error_count: 10, sessions: 2,
          since: "2026-09-23 00:00:00" },
  by_class: {}, attrib_status: "n/a",
  patterns: [
    { pattern: "OLD-BIG", class: "env", count: 9, tools: ["t"],
      samples: [], first_seen: "2026-01-01 00:00:00" },
    { pattern: "NEW-1", class: "env", count: 1, tools: ["t"],
      samples: [], first_seen: "2026-10-20 10:00:00" },
    { pattern: "NEW-2", class: "env", count: 2, tools: ["t"],
      samples: [], first_seen: "2026-10-21 10:00:00" },
    { pattern: "OLDER-SMALL", class: "env", count: 1, tools: ["t"],
      samples: [], first_seen: "2026-02-01 00:00:00" },
  ],
};
state.repMin = undefined;
state.repLast = FRESH;
const fOut = mkOut();
renderErrors(FRESH, fOut);
ok(remOf(fOut.innerHTML).indexOf("另有 3 条次数") >= 0,
   "三条被滤掉的（两条新 + 一条旧小样本）");
ok(remOf(fOut.innerHTML).indexOf("2 条是本窗口首见的新坑") >= 0,
   "首见条数按 first_seen ≥ 窗口起点算（2 条，不是 3 条）");
/* 无窗口（since=null，全库口径）时不许瞎报"首见" */
const NO_WIN = { meta: { since: null }, by_class: {}, patterns: FRESH.patterns };
state.repLast = NO_WIN;
const nwOut = mkOut();
renderErrors(NO_WIN, nwOut);
ok(remOf(nwOut.innerHTML).indexOf("首见") < 0,
   "全库口径（since=null）不报首见——那是无意义的口径");

console.log(fail ? ("FAILED: " + fail) : "ALL THRESHOLD RENDER CHECKS PASSED");
process.exit(fail ? 1 : 0);