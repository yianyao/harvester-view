/* v0.81 P1-1 渲染门：report-chains / report-traces 两个新报告面
 * （node 运行，退出码 0=全过）。
 *
 * 纪律与 render_reports 同源：从 index.html 提取**真源码**执行，
 * 只渲染服务端给的载荷（不二次聚合、不自己编数）；恶意串必须 esc；
 * 空态（未配置 traces / 无命中 / 全低于阈值）必须分开说、不装数据；
 * 阈值 chip 必须接线（点了真的重渲染）。
 *
 * 断言：
 *  - renderChains：meta 行含 p95/连击/循环/空转计数；高步会话表带 sid 锚点；
 *    三种打法表带单位与阈值 chip；低于阈值的条目报数不静默；
 *    工具名/循环对里的 HTML 必须 esc；
 *  - renderTraces：未配置 = 诚实空态 + hint；configured 时工具表带
 *    取消率/p50/p95；低于阈值的工具折叠且报数；模型调用段与子代理段在；
 *  - 阈值 chip 点击 → 按 repKind 重渲染（chains 走 renderChains）。
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

/* 元素桩：querySelectorAll 返回预注册结果（chip 点击要能驱动）。 */
function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", _qa: {},
    querySelector(sel) { return mkEl(id + ">" + sel); },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }
/* 报告页工具栏两个输入（repFilter 读它们） */
$("rep-q").value = "";
$("rep-erronly").checked = false;

/* 模块级"次数阈值"辅助块（repMinOf/repGate/repMinChips/wireRepThresh）——
   与 render_cmd.js 同口径：按注释头到 wireRepThresh 收尾整块提出。 */
const _th0 = html.indexOf("/* 报告页「次数阈值」");
const _th1 = html.indexOf("\n}\n", html.indexOf("function wireRepThresh(")) + 3;
if (_th0 < 0 || _th1 < 2) throw new Error("阈值辅助块提取失败");
eval(html.slice(_th0, _th1).replace("const REP_MIN_DEFAULT = 3;",
                                    "var REP_MIN_DEFAULT = 3;"));

/* state 桩：repMinOf/wireRepThresh 读它 */
const state = { repKind: "chains", repLast: null, repMin: 0 };
function repFilter() { return { q: $("rep-q").value.trim().toLowerCase(),
                                only: $("rep-erronly").checked }; }
function hitQ(text, q) { return !q || String(text || "").toLowerCase().includes(q); }
function shortSid(sid) {
  const m = String(sid || "").match(/session-([0-9a-f][0-9a-f-]+)/i);
  if (m) return m[1].slice(0, 13) + "…";
  return sid.length > 28 ? sid.slice(0, 25) + "…" : sid;
}

for (const [n, sig] of [
  ["renderChains", "d,out"], ["renderTraces", "d,out"],
]) {
  eval(extract(n, sig));
}

/* chip 点击驱动：wireRepThresh 给 [data-repmin] 挂 onclick —— 桩要把
   预注册的 chip 交出来，再手动点。 */
function chipEls(out) {
  const chips = [
    { dataset: { repmin: "1" }, onclick: null },
    { dataset: { repmin: "3" }, onclick: null },
  ];
  out._qa["[data-repmin]"] = chips;
  return chips;
}

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* ---- 真形载荷（2026-10-24 真库实测形状；含恶意串试金石） ---- */
const CHAINS = {
  sessions: 192, steps: 62817, p95: 1316, top_n: 10,
  long: [["workbuddy-transcript:aaa", 2000]],
  streaks: [["autoclaw:<img src=x onerror=alert(1)>", "exec", 198, 75],
            ["dsh:s2", "Read", 5, 3]],
  loops: [["autoclaw:loop1", "exec↔tool_result_recall", 11, 102],
          ["dsh:loop2", "Grep↔Read", 3, 7]],
  spins: [["dsh:spin1", "Bash", 5], ["dsh:spin2", "Grep", 2]],
  error_results: 120, result_rows: 30000,
  top: [["workbuddy-transcript:aaa", 4110], ["dsh:s2", 2958]],
};
const TRACES = {
  configured: true,
  tools: [
    { tool: "Bash", calls: 2879, errors: 15, cancelled: 0, running: 1,
      fail_rate: 0.0052, cancel_rate: 0.0, p50_ms: 1711, p95_ms: 35574 },
    { tool: "Edit", calls: 1326, errors: 19, cancelled: 0, running: 0,
      fail_rate: 0.0143, cancel_rate: 0.0, p50_ms: 32, p95_ms: 80 },
    { tool: "Rare<b>tool</b>", calls: 2, errors: 1, cancelled: 0, running: 0,
      fail_rate: 0.5, cancel_rate: 0.0, p50_ms: 10, p95_ms: 20 },
  ],
  generation: { calls: 5981, errors: 0, total_ms: 9000000,
                p50_ms: 9594, p95_ms: 56555 },
  summary: { files: 269, bad_files: 0, spans: 43324,
             agents: { cli: 211, Explore: 9 } },
};

(async () => {
  /* ① chains：默认阈值 ≥3 —— 2 次的 spin 应折叠且报数 */
  state.repKind = "chains"; state.repMin = 0; state.repLast = CHAINS;
  const out1 = $("rep-out");
  renderChains(CHAINS, out1);
  check("chains meta 行带 p95 与三种打法计数",
    out1.textContent === "" /* textContent 桩不用，改看 innerHTML */ ||
    $("rep-meta").textContent.includes("p95=1316"));
  check("chains meta 行含连击 2 / 循环 2 / 空转 2",
    $("rep-meta").textContent.includes("连击 2") &&
    $("rep-meta").textContent.includes("循环 2") &&
    $("rep-meta").textContent.includes("空转 2"));
  check("高步会话表带 sid 锚点（data-sid）",
    out1.innerHTML.includes('data-sid="workbuddy-transcript:aaa"'));
  check("streaks 工具名 esc（恶意串不落地）",
    out1.innerHTML.includes("&lt;img src=x onerror=alert(1)&gt;") &&
    !out1.innerHTML.includes("<img src=x"));
  check("连击表带单位「连击次数」", out1.innerHTML.includes("连击次数"));
  check("循环表带单位「完整周期」", out1.innerHTML.includes("完整周期"));
  check("空转表带单位「连续错误」", out1.innerHTML.includes("连续错误"));
  check("阈值 chip 出现（默认 ≥3 与全部）",
    out1.innerHTML.includes("data-repmin='1'") &&
    out1.innerHTML.includes("data-repmin='3'"));
  check("低于阈值的空转报数不静默",
    out1.innerHTML.includes("另有 1 条次数 &lt;3") &&
    out1.innerHTML.includes("合计 2"));
  check("chip 已接线（onclick 非空）", (() => {
    const chips = chipEls(out1);
    wireRepThresh(out1);
    return typeof chips[0].onclick === "function";
  })());

  /* ② chip 点击 → 切到「全部」后 2 次的空转应出现 */
  state.repMin = 0; state.repLast = CHAINS;
  renderChains(CHAINS, out1); /* 先重渲染清掉旧 onclick */
  state.repMin = 3;
  const chips2 = chipEls(out1);
  wireRepThresh(out1);
  chips2[0].onclick(); /* 点「全部」（chips2[1] 是 ≥3，别点错） */
  check("点「全部」后低次空转出行（dsh:spin2）",
    out1.innerHTML.includes('data-sid="dsh:spin2"'));

  /* ③ traces：configured 正常渲染 */
  state.repKind = "traces"; state.repMin = 0; state.repLast = TRACES;
  const out2 = $("rep-out");
  renderTraces(TRACES, out2);
  check("traces meta 行带文件/span/取消计数",
    $("rep-meta").textContent.includes("trace 文件 269") &&
    $("rep-meta").textContent.includes("span 43324"));
  check("工具表带取消率与 p50/p95",
    out2.innerHTML.includes("取消率") && out2.innerHTML.includes("p95") &&
    out2.innerHTML.includes("35.6s") /* 35574ms → 35.6s */);
  check("低调用量工具折叠且报数（Rare<b>tool</b> 不落地）",
    out2.innerHTML.includes("另有 1 件调用 &lt;3") &&
    !out2.innerHTML.includes("<b>tool</b>"));
  check("模型调用段在（p50/p95）",
    out2.innerHTML.includes("模型调用") && out2.innerHTML.includes("9.6s"));
  check("子代理段在（cli ×211）",
    out2.innerHTML.includes("cli ×211"));

  /* ④ traces 未配置 = 诚实空态，不拿索引库冒充 */
  state.repLast = { configured: false, hint: "未配置 traces-root" };
  const out3 = $("rep-out");
  renderTraces(state.repLast, out3);
  check("未配置 traces = 空态 + hint（不装数据）",
    out3.innerHTML.includes("未配置 trace 目录") &&
    out3.innerHTML.includes("未配置 traces-root") &&
    $("rep-meta").textContent === "");

  /* ⑤ traces 的 chip 也接线 */
  state.repKind = "traces"; state.repMin = 3; state.repLast = TRACES;
  renderTraces(TRACES, out3);
  const chips3 = chipEls(out3);
  wireRepThresh(out3);
  check("traces chip 已接线", typeof chips3[1].onclick === "function");
  chips3[0].onclick(); /* 点「全部」→ 低调用量工具出现 */
  check("点「全部」后低调用工具出现", out3.innerHTML.includes("Rare"));

  console.log(failed ? "\nFAILED: " + failed : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
