/* v0.81 P1-2 渲染门：report-harness 面板（node 运行，退出码 0=全过）。
 *
 * 纪律与 render_chains_traces 同源：从 index.html 提取**真源码**执行。
 * 本面板的验收红线（用户定位②「一眼看到值得看的」+ 诚实话术纪律）：
 *  - 「没有数据」与「没有错误」分开：无遥测源显示「—（无遥测）」、
 *    只落 call 相的源显示「—（无结果相）」，**绝不渲染成 0%**；
 *  - 无遥测源默认折叠（可展开），折叠条报数——折叠视图不折叠数据；
 *  - 失败率 ≥5% 标红；result 相 <20 标「样本少」（率不可比）；
 *  - 代表工具/skill 超 Top N 时报「等 N 件/次」，不静默截断；
 *  - 源名/工具名里的 HTML 必须 esc；
 *  - md 导出与渲染同口径（同一份响应的两种出口）。
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

/* 元素桩 */
function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", _qa: {}, value: "", checked: false,
    querySelector(sel) { return mkEl(id + ">" + sel); },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }
$("rep-q").value = "";
$("rep-erronly").checked = false;

/* 依赖桩（renderHarness 只碰这几个） */
const state = { harnessHideQuiet: true };
function repFilter() { return { q: $("rep-q").value.trim().toLowerCase(),
                                only: $("rep-erronly").checked }; }
function hitQ(text, q) { return !q || String(text || "").toLowerCase().includes(q); }
let switchedTo = null, filledSource = null, sessionsReload = 0;
function switchTab(name) { switchedTo = name; }
function loadSessions() { sessionsReload++; return Promise.resolve(); }

eval(extract("renderHarness", "d,out"));
eval(extract("cell", "x"));
eval(extract("mdTable", "head,rows"));
eval(extract("mdReport", "kind,d"));

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* ---- 真形载荷（2026-10-24 真库 9 源形状；含恶意串试金石） ---- */
const D = {
  sources: [
    { name: "yuanbao-raw", sessions: 1187, messages: 8453, steps: 0,
      results: 0, errors: 0, fail_rate: null, telemetry: false,
      paired: false, avg_steps: 0.0, models: [],
      top_tools: [], top_skills: [], tools_total: 0, skills_total: 0,
      first_active: "2026-10-01 00:00:00", last_active: "2026-10-06 09:14:29" },
    { name: "workbuddy-transcript", sessions: 131, messages: 43763,
      steps: 34843, results: 17000, errors: 160, fail_rate: 0.0094,
      telemetry: true, paired: true, avg_steps: 266.0, models: ["m1"],
      top_tools: [{ tool: "Bash", steps: 6215, errors: 40 },
                  { tool: "<img src=x onerror=alert(1)>", steps: 9, errors: 0 }],
      top_skills: [{ skill: "skills-security-check", count: 6 }],
      tools_total: 35, skills_total: 54,
      first_active: "2026-09-01 00:00:00",
      last_active: "2026-10-22 08:01:44" },
    { name: "vscode-copilot", sessions: 5, messages: 144, steps: 100,
      results: 0, errors: 0, fail_rate: null, telemetry: true,
      paired: false, avg_steps: 20.0, models: [],
      top_tools: [{ tool: "copilot_readFile", steps: 55, errors: 0 }],
      top_skills: [], tools_total: 10, skills_total: 0,
      first_active: "2026-10-01 00:00:00",
      last_active: "2026-10-06 11:51:31" },
    { name: "autoclaw", sessions: 21, messages: 6911, steps: 5072,
      results: 2500, errors: 88, fail_rate: 0.0352, telemetry: true,
      paired: true, avg_steps: 241.5, models: [],
      top_tools: [{ tool: "exec", steps: 1108, errors: 60 }],
      top_skills: [{ skill: "delivery-artifact", count: 15 }],
      tools_total: 28, skills_total: 22,
      first_active: "2026-10-01 00:00:00",
      last_active: "2026-10-19 08:18:47" },
  ],
  totals: { sources: 4, sessions: 1344, messages: 59261, steps: 40015,
            results: 19500, errors: 248, fail_rate: 0.0127 },
  window: { since_days: null, top_n: 5 },
  note: "取消率不在本面",
};

(async () => {
  /* ① 默认折叠：无遥测源（yuanbao-raw）不在表内，但折叠条报数 */
  state.harnessHideQuiet = true;
  const out = $("rep-out");
  renderHarness(D, out);
  check("meta 行含数据源/会话/总体失败率",
    $("rep-meta").textContent.includes("数据源 4 个") &&
    $("rep-meta").textContent.includes("会话 1344") &&
    $("rep-meta").textContent.includes("1.3%"));
  check("默认折叠无遥测源（yuanbao-raw 不在表内）",
    !out.innerHTML.includes("yuanbao-raw"));
  check("折叠条报数（1 个无工具遥测）",
    out.innerHTML.includes("展开 1 个无工具遥测的数据源"));

  /* ② 无结果相 ≠ 无遥测 ≠ 0%：三种话术分开 */
  check("只落 call 相的源显示「—（无结果相）」",
    out.innerHTML.includes("—（无结果相）") &&
    !out.innerHTML.includes("vscode-copilot</a></td><td class='num'>5</td>" +
      "<td class='num'>144</td><td class='num'>100</td>" +
      "<td class='num'>—（无遥测）"));
  check("失败率 ≥5% 才会标红（0.9%/3.5% 都不标）",
    !out.innerHTML.includes("errline"));
  check("result 相 <20 标「样本少」（vscode-copilot 无 result 不适用）",
    !out.innerHTML.includes("样本少"));

  /* ③ 工具/skill 超 Top N 报数 */
  check("代表工具列报「等 35 件」",
    out.innerHTML.includes("等 35 件"));
  check("代表 skill 列报「等 54 次」",
    out.innerHTML.includes("等 54 次"));

  /* ④ esc：恶意工具名不落地 */
  check("工具名 esc（恶意串不落地）",
    out.innerHTML.includes("&lt;img src=x onerror=alert(1)&gt;") &&
    !out.innerHTML.includes("<img src=x"));

  /* ⑤ 点展开 → 无遥测源出现；再渲染时不再自动折叠 */
  const open = $("hq-open");
  check("展开 chip 存在且已接线", typeof open.onclick === "function");
  open.onclick();
  check("点展开后 yuanbao-raw 出现",
    out.innerHTML.includes("yuanbao-raw"));
  check("无遥测源失败率显示「—（无遥测）」而非 0%",
    out.innerHTML.includes("—（无遥测）") &&
    !/<td class='num'>0\.0%<\/td>/.test(out.innerHTML));

  /* ⑥ 高失败率标红 + 样本少标注（改载荷重渲） */
  const hot = JSON.parse(JSON.stringify(D));
  hot.sources[1].fail_rate = 0.12;      // workbuddy-transcript 12%
  hot.sources[3].results = 15;          // autoclaw result<20
  state.harnessHideQuiet = false;
  renderHarness(hot, out);
  check("失败率 12% 标 errline", out.innerHTML.includes("errline"));
  check("result 相 15 标「样本少」", out.innerHTML.includes("样本少"));

  /* ⑦ 点源名 → 会话检索按该源筛选（治理下钻入口） */
  const anchor = { dataset: { src: "dsh" }, onclick: null };
  out._qa["[data-src]"] = [anchor];
  renderHarness(hot, out);
  for (const a of out.querySelectorAll("[data-src]")) a.onclick();
  check("点源名切到会话检索并预填 source",
    switchedTo === "sessions" && $("f-source").value === "dsh");

  /* ⑧ 空态：窗口内没有会话 */
  renderHarness({ sources: [], totals: { sources: 0, sessions: 0, messages: 0,
    steps: 0, results: 0, errors: 0, fail_rate: null } }, out);
  check("空态诚实（没有会话）",
    out.innerHTML.includes("窗口内没有会话") &&
    $("rep-meta").textContent.includes("无结果相可算"));

  /* ⑨ md 导出与渲染同口径 */
  const md = mdReport("harness", D);
  check("md 导出含总体失败率与每源行",
    md.includes("Harness 体检") && md.includes("1.3%") &&
    md.includes("workbuddy-transcript") && md.includes("等35件"));
  check("md 导出无遥测源不写 0%",
    md.includes("—（无遥测）") && !/0\.0%/.test(md));

  console.log(failed ? "\nFAILED: " + failed : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
