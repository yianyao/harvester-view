/* 词云导航渲染门（node 运行，退出码 0=全过）。
 *
 * 覆盖 P7-①（v0.81）渲染行为：
 *   ① 分档渲染：字号档位 c1..c5 按 doc_freq 分位；词 esc 原样展示
 *     （注入试探词必须被转义）；title 带 freq/doc_freq 口径；
 *   ② 点词下钻：jumpCloudWord 灌 state.q + f-q + switchTab("sessions")
 *     + loadSessions（复用真检索通路，不臆造词→会话归属）；
 *   ③ 诚实空态：hint 原样渲染 + 生成命令；空 rows 不编词；
 *   ④ 数据新鲜度：bar 显示 run 日期与档位。
 *
 * 纪律与 render_reports 同源：只渲染服务端给的内容。
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
    querySelector(sel) {
      if (!el._q[sel]) el._q[sel] = mkEl(id + ">" + sel);
      return el._q[sel];
    },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }

const apiCalls = [];
let apiNext = [];
async function api(url) {
  apiCalls.push(url);
  if (!apiNext.length) throw new Error("api 桩没有下一个响应：" + url);
  const d = apiNext.shift();
  if (d instanceof Error) throw d;
  return d;
}
const tabCalls = [];
function switchTab(n) { tabCalls.push(n); }
const state = { q: "", offset: 0, loaded: {} };
let cloudN = 2; /* index.html 模块级变量（档位），extract 只取函数体故需补桩 */
let sessCalls = 0;
async function loadSessions() { sessCalls++; }

eval(extract("loadWordcloud", ""));
eval(extract("jumpCloudWord", "w"));

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

(async () => {
  /* ① 正常渲染：三档词 + 注入试探词 + run 信息 */
  apiNext = [{ rows: [
      { gram: "file changed", freq: 300, doc_freq: 100 },
      { gram: "中档词甲", freq: 30, doc_freq: 50 },
      { gram: "<b>注入词</b>", freq: 3, doc_freq: 5 },
      { gram: "最小词", freq: 1, doc_freq: 1 },
    ], run: { id: 4, generated_at: "2026-10-09T18:09:42+08:00" } }];
  const out = $("cloud-out");
  out._qa[".cw"] = [mkEl("w1"), mkEl("w2"), mkEl("w3"), mkEl("w4")];
  out._qa[".cw"][0].dataset.w = "file changed";
  await loadWordcloud();
  const h1 = out.innerHTML;
  const tiers = new Set((h1.match(/class='cw c[1-5]'/g) || [])
    .map(s => s.slice(-2)));
  check("分档类出现（至少三个不同档位）", tiers.size >= 3,
    "实际档位：" + [...tiers].join(","));
  check("词 esc 原样（注入试探词被转义）",
    h1.includes("&lt;b&gt;注入词&lt;/b&gt;") && !h1.includes("<b>注入词</b>"));
  check("title 带 freq/doc_freq 口径",
    h1.includes("freq 300") && h1.includes("doc_freq 100"));
  check("bar 显示 run 编号与日期（数据新鲜度）",
    $("cloud-bar").textContent.includes("#4") &&
    $("cloud-bar").textContent.includes("2026-10-09"));
  check("请求带 n 档与 limit",
    apiCalls[apiCalls.length - 1] === "/api/keywords?n=2&limit=150");

  /* ② 点词下钻：灌检索通路 */
  jumpCloudWord("file changed");
  check("下钻：switchTab(sessions) + state.q + f-q + loadSessions",
    tabCalls.includes("sessions") && state.q === "file changed" &&
    $("f-q").value === "file changed" && sessCalls === 1);
  jumpCloudWord("");
  check("空词不跳转", sessCalls === 1);

  /* ③ 诚实空态：hint 原样 + 生成命令 */
  apiNext = [{ rows: [], hint: "keywords_meta 未生成（先跑 harvester keywords）" }];
  await loadWordcloud();
  const h3 = out.innerHTML;
  check("hint 原样渲染 + 生成命令",
    h3.includes("keywords_meta 未生成") &&
    h3.includes("python -m harvester keywords"));

  /* ④ run 无数据：不编词 */
  apiNext = [{ rows: [], run: { id: 5, generated_at: "2026-10-19T00:00:00" } }];
  await loadWordcloud();
  check("空 rows 诚实空态", out.innerHTML.includes("没有词频数据"));

  /* ⑤ api 失败：错误空态不静默 */
  apiNext = [new Error("连接中断")];
  await loadWordcloud();
  check("加载失败有空态提示", out.innerHTML.includes("词频加载失败"));

  if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
  console.log("ALL WORDCLOUD CHECKS PASSED");
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
